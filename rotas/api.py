"""API JSON da tela de rotas. As alterações chamam rotas.acoes; ValueError vira 400 {"erro": ...}."""
import json
import re
from datetime import date, timedelta
from functools import wraps

from django.conf import settings
from django.db.models import Max
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import acoes, retiradas, totvs
from .models import Documento, Historico, Loja, Parada, RegraEntrega, Rota, RotaPadrao, TipoDocumento, Veiculo, VisaoUsuario
from .regras import etapa_carga, nome_usuario, status_parada


def _hora(dt):
    return timezone.localtime(dt).strftime("%H:%M") if dt else ""


def _data(v):
    try:
        return date.fromisoformat(v)
    except (TypeError, ValueError):
        raise ValueError("Data inválida.")


def _rotas():
    return Rota.objects.prefetch_related("paradas__documentos__tipo", "historico")


def parada_json(p):
    return {
        "id": p.id, "ordem": p.ordem, "pes": p.pes, "loja": p.loja, "emp": p.emp, "horario": p.horario, "rt": p.rt, "cx": p.cx,
        "frete_envio": "" if p.frete_envio is None else str(p.frete_envio),
        "frete_retorno": "" if p.frete_retorno is None else str(p.frete_retorno),
        "rt_por": p.rt_por, "rt_em": _hora(p.rt_em), "cx_por": p.cx_por, "cx_em": _hora(p.cx_em),
        "docs": _docs_json(p),
        "status": status_parada(p),
    }


def _docs_json(p):
    """{tipo: [documentos]}: a mesma loja pode ter mais de uma transação do mesmo tipo."""
    docs = {}
    for d in p.documentos.all():
        docs.setdefault(d.tipo.codigo, []).append({
            "id": d.id, "numero": d.numero, "por": d.atendido_por, "em": _hora(d.atendido_em),
            "expedida_por": d.expedida_por, "expedida_em": _hora(d.expedida_em),
            "concluido_por": d.concluido_por, "concluido_em": _hora(d.concluido_em),
            "etapa": etapa_carga(d) if d.tipo.eh_carga else "", "totvs": d.situacao_totvs, "volumes": d.volumes,
            "pedidos": d.aviso_pedidos, "pedidos_detalhe": d.pedidos_detalhe})
    return docs


def rota_json(r):
    campos = ["id", "nome", "saida", "lacre", "motorista", "veiculo", "ajudantes", "rastreador", "status", "fora_do_planejamento", "remarcacoes"]
    d = {c: getattr(r, c) for c in campos}
    d["data"] = r.data.isoformat()
    d["tipo_veiculo"] = acoes.tipo_veiculo(r.veiculo).upper()
    d["paradas"] = [parada_json(p) for p in r.paradas.all()]
    d["historico"] = [f"{timezone.localtime(h.quando):%d/%m %H:%M} · {h.acao}" for h in r.historico.all()]
    return d


def acao(view):
    """POST com corpo JSON; devolve a rota afetada ou {"erro"} com 400."""
    @require_POST
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            dados = json.loads(request.body or "{}")
            rota = view(request, dados, *args, **kwargs)
        except (ValueError, json.JSONDecodeError) as e:
            return JsonResponse({"erro": str(e)}, status=400)
        return JsonResponse({"rota": rota_json(_rotas().get(pk=rota.pk))})
    return wrapper


def leitura(view):
    """GET; ValueError (ex.: data inválida na URL) vira 400."""
    @require_GET
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except ValueError as e:
            return JsonResponse({"erro": str(e)}, status=400)
    return wrapper


# ---------- leitura ----------

@leitura
def semana(request, dia):
    seg = _data(dia) - timedelta(days=_data(dia).weekday())
    dias = [seg + timedelta(days=i) for i in range(7)]  # segunda a domingo
    ativas = {}
    for d in Rota.objects.filter(data__in=dias, status=Rota.ATIVA).values_list("data", flat=True):
        ativas[d] = ativas.get(d, 0) + 1
    return JsonResponse({"dias": [{"data": d.isoformat(), "ativas": ativas.get(d, 0)} for d in dias]})


@leitura
def dia(request, dia):
    return JsonResponse({
        "rotas": [rota_json(r) for r in _rotas().filter(data=_data(dia))],
        "tipos": list(TipoDocumento.objects.values("codigo", "nome", "eh_carga")),
        "ultimo": Historico.objects.aggregate(m=Max("id"))["m"] or 0,
        "pessoas": {nome_usuario(v.usuario): {"cor": v.cor, "apelido": v.apelido}
                    for v in VisaoUsuario.objects.exclude(cor="", apelido="").select_related("usuario")},
    })


@require_GET
def mudancas(request):
    try:
        desde = int(request.GET.get("desde", ""))
    except ValueError:
        return JsonResponse({"ultimo": Historico.objects.aggregate(m=Max("id"))["m"] or 0, "eventos": [], "versao": settings.VERSAO})
    hs = Historico.objects.filter(id__gt=desde).select_related("rota")[:200]
    eventos = [{"id": h.id, "usuario": h.usuario, "texto": h.acao, "rota": h.rota_id, "data": h.rota.data.isoformat(), "parada": h.parada_id, "campo": h.campo} for h in hs]
    return JsonResponse({"ultimo": eventos[-1]["id"] if eventos else desde, "eventos": eventos, "versao": settings.VERSAO})


@require_GET
def cadastro(request):
    return _cadastro_json()


@require_POST
def cadastro_rota(request):
    """POST {rota, lojas}: lojas da rota do cadastro, na ordem. Devolve o cadastro atualizado."""
    try:
        dados = json.loads(request.body or "{}")
        acoes.editar_rota_padrao(request.user, dados.get("rota"), dados.get("lojas") or [])
    except (ValueError, json.JSONDecodeError) as e:
        return JsonResponse({"erro": str(e)}, status=400)
    return _cadastro_json()


def _cadastro_json():
    rotas = {r.nome: {"saida": r.saida, "variacoes": [v for v in r.variacoes.splitlines() if v.strip()], "lojas": []}
             for r in RotaPadrao.objects.filter(ativa=True)}
    lojas = {}
    for l in Loja.objects.select_related("rota_padrao"):
        lojas[l.nome] = {"pes": l.pes, "emp": l.emp, "horario": l.horario, "rota": l.rota_padrao.nome if l.rota_padrao else ""}
        if l.rota_padrao and l.rota_padrao.nome in rotas:
            rotas[l.rota_padrao.nome]["lojas"].append(l.nome)
    return JsonResponse({"rotas": rotas, "lojas": lojas})


def visao(request):
    v, _ = VisaoUsuario.objects.get_or_create(usuario=request.user)
    if request.method == "POST":
        try:
            dados = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"erro": "JSON inválido."}, status=400)
        if isinstance(dados.get("colunas_ocultas"), list):
            v.colunas_ocultas = [str(c) for c in dados["colunas_ocultas"]]
        if "filtro" in dados:
            v.filtro = str(dados["filtro"])[:100]
        if "cor" in dados:  # vai direto para o style do nome: só #rrggbb
            v.cor = dados["cor"] if re.fullmatch(r"#[0-9a-fA-F]{6}", str(dados["cor"])) else ""
        if "apelido" in dados:
            v.apelido = str(dados["apelido"]).strip().upper()[:60]
        v.save()
    return JsonResponse({"colunas_ocultas": v.colunas_ocultas, "filtro": v.filtro, "cor": v.cor, "apelido": v.apelido})


# ---------- alterações ----------

@acao
def criar_rota(request, dados):
    return acoes.criar_rota(request.user, _data(dados.get("data")), dados.get("nome"), dados.get("lojas") or [], bool(dados.get("fora_do_planejamento")))


@acao
def rota_campo(request, dados, pk):
    return acoes.editar_rota(request.user, get_object_or_404(Rota, pk=pk), dados.get("campo"), dados.get("valor"))


@acao
def rota_mover(request, dados, pk):
    return acoes.mover_rota(request.user, get_object_or_404(Rota, pk=pk), _data(dados.get("data")))


@acao
def rota_status(request, dados, pk):
    rota = get_object_or_404(Rota, pk=pk)
    return (acoes.cancelar_rota if dados.get("status") == Rota.CANCELADA else acoes.reativar_rota)(request.user, rota)


@require_POST
def rota_excluir(request, pk):
    acoes.excluir_rota(request.user, get_object_or_404(Rota, pk=pk))
    return JsonResponse({"ok": True})


@acao
def rota_duplicar(request, dados, pk):
    return acoes.duplicar_rota(request.user, get_object_or_404(Rota, pk=pk))


@acao
def rota_inverter(request, dados, pk):
    return acoes.inverter_rota(request.user, get_object_or_404(Rota, pk=pk))


@acao
def rota_parada(request, dados, pk):
    return acoes.adicionar_parada(request.user, get_object_or_404(Rota, pk=pk)).rota


@acao
def parada_campo(request, dados, pk):
    return acoes.editar_parada(request.user, get_object_or_404(Parada, pk=pk), dados.get("campo"), dados.get("valor")).rota


@acao
def parada_documento(request, dados, pk):
    p = get_object_or_404(Parada, pk=pk)
    acoes.editar_documento(request.user, p, dados.get("tipo"), dados.get("numero"))
    totvs.acordar()
    return p.rota


@acao
def parada_retirada(request, dados, pk):
    return acoes.confirmar_retirada(request.user, get_object_or_404(Parada, pk=pk), dados.get("campo"), bool(dados.get("sim"))).rota


@acao
def parada_ordem(request, dados, pk):
    return acoes.mover_parada(request.user, get_object_or_404(Parada, pk=pk), dados.get("direcao")).rota


@acao
def parada_remover(request, dados, pk):
    p = get_object_or_404(Parada, pk=pk)
    acoes.remover_parada(request.user, p)
    return p.rota


@acao
def documento_etapa(request, dados, pk):
    doc = get_object_or_404(Documento, pk=pk)
    (acoes.voltar_carga if dados.get("acao") == "voltar" else acoes.avancar_carga)(request.user, doc)
    return doc.parada.rota


@acao
def documento_atender(request, dados, pk):
    doc, sim = get_object_or_404(Documento, pk=pk), bool(dados.get("sim"))
    if sim and not doc.atendido_por:
        # marcar antes de atender na TOTVS deixava o número vermelho: confere na hora e só deixa marcar se lá já estiver atendida
        s = totvs.conferir_agora(doc)
        if s == "":
            raise ValueError(f"Não achei a transação {doc.numero} de {doc.parada.loja or 'sem loja'} na TOTVS. "
                             "Confira o número e a loja: com o número certo, quando ela ficar Atendida na TOTVS, o sistema marca sozinho.")
        if s and s != totvs.SITUACOES["Attended"]:
            raise ValueError(f"A transação {doc.numero} de {doc.parada.loja or 'sem loja'} está \"{s}\" na TOTVS. "
                             "Atenda primeiro na TOTVS: quando ficar Atendida, o sistema marca sozinho em poucos segundos, "
                             "com o nome de quem faturou. Não precisa clicar aqui.")
    return acoes.atender(request.user, doc, sim).parada.rota


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


@leitura
def capa(request, pk):
    """Capa da frente: lojas da rota (sem a FÁBRICA) com contato e retiradas, regras de entrega e equipe."""
    r = get_object_or_404(_rotas(), pk=pk)
    paradas = [p for p in r.paradas.all() if p.loja and not acoes._BASE.match(p.loja)]
    contatos = {l.nome: l for l in Loja.objects.filter(nome__in=[p.loja for p in paradas])}
    regras = {g.emp: g for g in RegraEntrega.objects.filter(emp__in=[p.emp for p in paradas])}
    lojas, regras_rota = [], []
    for i, p in enumerate(paradas, 1):
        c = contatos.get(p.loja)
        rt, cx = _num(p.rt), _num(p.cx)
        lojas.append({"atd": i, "nome": p.loja, "horario": p.horario or "Horário normal", "ramal": c.ramal if c else "",
                      "oi": c.celular_oi if c else "", "rt": rt, "cx": cx, "total": rt + cx})
        g = regras.get(p.emp) if p.emp else None
        if g:
            regras_rota.append({"loja": p.loja, "carrinho_4": g.carrinho_4, "carrinho_2": g.carrinho_2, "sem_carrinho_apos": g.sem_carrinho_apos,
                                "horario_docas": g.horario_docas, "autorizacao_previa": g.autorizacao_previa, "observacao": g.observacao})
    return JsonResponse({"rota": r.nome, "data": f"{r.data:%d/%m/%Y}", "motorista": r.motorista, "veiculo": r.veiculo, "ajudantes": r.ajudantes,
                         "lacre": r.lacre, "lojas": lojas, "regras": regras_rota, "total": sum(l["total"] for l in lojas)})


@leitura
def entrega(request, pk):
    """Folhas 553 (Relatório de entrega de mercadoria): uma por 434 e 3386 lançada na rota, com os dados da TOTVS.
    Em teste: só para os donos das retiradas."""
    if request.user.username not in retiradas.DONOS:
        raise Http404
    r = get_object_or_404(Rota, pk=pk)
    docs = [d for d in Documento.objects.select_related("tipo", "parada__rota").filter(parada__rota=r, tipo__codigo__in=("t434", "t3386"))
            .order_by("parada__ordem", "tipo__ordem", "id") if d.numero.isdigit() and d.parada.pes.isdigit()]
    return JsonResponse({"rota": r.nome, "data": f"{r.data:%d/%m/%Y}", "folhas": totvs.entrega(docs)})


@leitura
def planejamento(request, dia):
    """Rotas ativas por dia na semana (seg a sáb) e as rotas do cadastro, para a grade de planejamento."""
    seg = _data(dia) - timedelta(days=_data(dia).weekday())
    dias = [seg + timedelta(days=i) for i in range(6)]
    por_dia = {d: set() for d in dias}
    for d, nome in Rota.objects.filter(data__in=dias, status=Rota.ATIVA).values_list("data", "nome"):
        por_dia[d].add(nome)
    return JsonResponse({"rotas": list(RotaPadrao.objects.filter(ativa=True).order_by("nome").values_list("nome", flat=True)),
                         "dias": [{"data": d.isoformat(), "rotas": sorted(por_dia[d])} for d in dias]})


@require_POST
def planejar(request):
    try:
        dados = json.loads(request.body or "{}")
        marcacoes = {_data(d): list(nomes) for d, nomes in (dados.get("marcacoes") or {}).items()}
        desmarcacoes = {_data(d): list(nomes) for d, nomes in (dados.get("desmarcacoes") or {}).items()}
        res = acoes.planejar_semana(request.user, marcacoes, desmarcacoes)
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        return JsonResponse({"erro": str(e)}, status=400)
    return JsonResponse({k: len(v) for k, v in res.items()})


def veiculos(request):
    """GET: frota cadastrada. POST {placa, tipo, descricao}: cadastra ou altera."""
    if request.method == "POST":
        try:
            dados = json.loads(request.body or "{}")
            acoes.salvar_veiculo(request.user, dados.get("placa"), dados.get("tipo"), dados.get("descricao"))
        except (ValueError, json.JSONDecodeError) as e:
            return JsonResponse({"erro": str(e)}, status=400)
    return JsonResponse({"veiculos": list(Veiculo.objects.values("placa", "tipo", "descricao"))})


@require_POST
def veiculos_remover(request):
    try:
        acoes.remover_veiculo(request.user, json.loads(request.body or "{}").get("placa"))
    except json.JSONDecodeError as e:
        return JsonResponse({"erro": str(e)}, status=400)
    return JsonResponse({"veiculos": list(Veiculo.objects.values("placa", "tipo", "descricao"))})
