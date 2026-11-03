from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth.views import LoginView
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from . import retiradas as ret
from . import painel as pnl
from . import ssw
from .models import LoginSSW, NotaRetirada, Parada, Rota
from .regras import nome_usuario
from .windows import proteger


class Entrar(LoginView):
    """Sem "Manter conectado", a sessão acaba ao fechar o navegador; marcado, dura 30 dias."""
    def form_valid(self, form):
        resposta = super().form_valid(form)
        self.request.session.set_expiry(30 * 24 * 3600 if self.request.POST.get("lembrar") else 0)
        return resposta


@ensure_csrf_cookie
def semana(request):
    return render(request, "rotas/semana.html", {"usuario": getattr(getattr(request.user, "visao", None), "apelido", "") or nome_usuario(request.user), "login": nome_usuario(request.user), "hoje": timezone.localdate().isoformat(),
                                                 "versao": settings.VERSAO,  # no endereço do CSS/JS: o navegador não usa a cópia velha
                                                 "retiradas": request.user.username in ret.DONOS})


def _dono(request):
    if request.user.username not in ret.DONOS:
        raise Http404


def retiradas(request):
    _dono(request)
    ret.acordar()  # busca já as notas novas (aparecem ao recarregar)
    ps, hoje = ret.paradas(), timezone.localdate()
    dias = {}
    for p in ps:
        dias.setdefault(p.rota.data, set()).add(p.rota_id)
    # um dia por vez (?dia=AAAA-MM-DD; vazio = todos); abre no de hoje, se tiver retirada
    dia = request.GET.get("dia", hoje.isoformat() if hoje in dias else "")
    rotas = {}
    for p in (p for p in ps if not dia or p.rota.data.isoformat() == dia):
        p.notas = list(p.notas_retirada.all())
        for n in p.notas:
            n.pdf = ret.arquivo(n.chave).exists()
        p.vol_rt = sum(n.volumes or 0 for n in p.notas if n.operacao not in ret.CX)
        p.vol_cx = sum(n.volumes or 0 for n in p.notas if n.operacao in ret.CX)
        p.peso = sum(ssw.peso(n) for n in p.notas)
        p.cte_problema = ssw.problema(p, p.notas)
        p.cte_pdf = ssw.arquivo(p).exists()
        rotas.setdefault(p.rota, []).append(p)
    for r, ps_rota in rotas.items():  # cada parada traz a própria cópia da rota: conta na que vai para a tela
        r.dactes = sum(p.cte_pdf for p in ps_rota)
    emitindo = any(p.cte_situacao.startswith((ssw.NA_FILA, ssw.EMITINDO, ssw.GRAVANDO)) or "gravado; enviando à" in p.cte_situacao
                   for ps in rotas.values() for p in ps)
    return render(request, "rotas/retiradas.html", {
        "usuario": nome_usuario(request.user), "versao": settings.VERSAO, "pasta": settings.DADOS / "retiradas", "cx": ret.CX,
        "rotas": rotas.items(), "emitindo": emitindo,
        "pode_todos": [p for ps in rotas.values() for p in ps if p.notas and not p.cte_problema], "dia": dia,
        "dias": [(d.isoformat(), d, len(r)) for d, r in sorted(dias.items(), reverse=True)]})


@require_POST
def retirada_cte(request, pk):
    """Botão "Fazer CT-e" da loja: entra na fila do SSW (rotas/ssw.py)."""
    _dono(request)
    erro = ssw.pedir(get_object_or_404(Parada.objects.select_related("rota"), pk=pk), request.user)
    return JsonResponse({"erro": erro}, status=409) if erro else JsonResponse({"ok": True})


@require_POST
def retiradas_buscar(request):
    """Botão "Buscar agora": procura na TOTVS na hora, sem esperar a busca de 10 em 10 min."""
    _dono(request)
    return JsonResponse({"novas": ret.sincronizar()})


def retirada_cte_pdf(request, pk):
    _dono(request)
    p = get_object_or_404(Parada, pk=pk)
    if not ssw.arquivo(p).exists():
        raise Http404
    # baixa em vez de abrir no Chrome: o Windows abre no Adobe (o leitor de PDF do PC), que é por onde ele imprime
    return FileResponse(open(ssw.arquivo(p), "rb"), content_type="application/pdf", as_attachment=True,
                        filename=f"DACTE {p.cte_ctrc} - {p.loja}.pdf")


def _ctes_da_parada(p):
    """Os dois CT-es da ida da loja (434/3386 e sacola), com o que a página mostra de cada um (docs vazio = coluna sem transação)."""
    saida = []
    for tipo, nome in (("ida", "434 / 3386"), ("sacola", "Sacola")):
        docs = ssw.documentos(p, tipo)
        for d in docs:
            d.pronta = ssw.atendida(d)
            d.rotulo = ("atendida no 099" if d.pronta else "pedidos não atendidos") if d.tipo.eh_carga else d.situacao_totvs or "sem situação"
        vol = sum(d.volumes or 0 for d in docs)
        saida.append({"tipo": tipo, "nome": nome, "docs": docs, "volumes": vol, "peso": vol * ssw.PESO_RT,
                      "problema": ssw.problema(p, docs, tipo) if docs else "sem transação", "situacao": ssw.campo(p, tipo, "situacao"),
                      "em": ssw.campo(p, tipo, "em"), "por": ssw.campo(p, tipo, "por"), "ctrc": ssw.campo(p, tipo, "ctrc"),
                      "pdf": ssw.arquivo(p, tipo).exists()})
    return saida


def painel(request):
    hoje = timezone.localdate()
    try:
        a, m = map(int, request.GET.get("mes", "").split("-"))
        ini = date(a, m, 1)
    except ValueError:
        ini = hoje.replace(day=1)
    ant, seg = pnl.mes_anterior_e_seguinte(ini)
    return render(request, "rotas/painel.html", {"usuario": nome_usuario(request.user), "versao": settings.VERSAO, "ini": ini,
                                                 "ant": ant, "seg": seg, **pnl.resumo(ini)})


def ctes(request):
    """CT-es da ida (todos os usuários): por loja, um das NFs da 434/3386 e um da sacola, no SSW com o login de quem pede."""
    hoje = timezone.localdate()
    ps = (Parada.objects.select_related("rota").prefetch_related("documentos__tipo")
          .filter(rota__status=Rota.ATIVA, rota__data__range=(hoje - timedelta(days=7), hoje + timedelta(days=3)))
          .order_by("-rota__data", "rota__ordem", "rota__id", "ordem"))
    ps = [p for p in ps if any(ssw.documentos(p, t) for t in ssw.CODIGOS)]
    dias = {}
    for p in ps:
        dias.setdefault(p.rota.data, set()).add(p.rota_id)
    dia = request.GET.get("dia", hoje.isoformat() if hoje in dias else "")
    rotas, emitindo = {}, False
    for p in (p for p in ps if not dia or p.rota.data.isoformat() == dia):
        p.ctes = _ctes_da_parada(p)
        emitindo |= any(c["situacao"].startswith((ssw.NA_FILA, ssw.EMITINDO, ssw.GRAVANDO)) or "gravado; enviando à" in c["situacao"]
                        for c in p.ctes)
        rotas.setdefault(p.rota, []).append(p)
    for r, ps_rota in rotas.items():  # cada parada traz a própria cópia da rota: conta na que vai para a tela
        r.dactes = sum(c["pdf"] for p in ps_rota for c in p.ctes)
    return render(request, "rotas/ctes.html", {
        "usuario": nome_usuario(request.user), "versao": settings.VERSAO, "rotas": rotas.items(), "emitindo": emitindo, "dia": dia,
        "login_ssw": LoginSSW.objects.filter(usuario=request.user).first(),
        "pode_todos": sum(1 for ps in rotas.values() for p in ps for c in p.ctes if not c["problema"]),
        "dias": [(d.isoformat(), d, len(r)) for d, r in sorted(dias.items(), reverse=True)]})


def _tipo_ida(tipo):
    if tipo not in ssw.CODIGOS:
        raise Http404


@require_POST
def cte_ida(request, pk, tipo):
    _tipo_ida(tipo)
    p = get_object_or_404(Parada.objects.select_related("rota").prefetch_related("documentos__tipo"), pk=pk)
    erro = ssw.pedir(p, request.user, tipo)
    return JsonResponse({"erro": erro}, status=409) if erro else JsonResponse({"ok": True})


def cte_ida_pdf(request, pk, tipo):
    _tipo_ida(tipo)
    p = get_object_or_404(Parada, pk=pk)
    if not ssw.arquivo(p, tipo).exists():
        raise Http404
    return FileResponse(open(ssw.arquivo(p, tipo), "rb"), content_type="application/pdf", as_attachment=True,
                        filename=f"DACTE {ssw.campo(p, tipo, 'ctrc')} - {p.loja} ({tipo}).pdf")


def _dactes_da_rota(pk, tipos, nome):
    """Botão "Imprimir todos" da rota: os DACTEs já emitidos desses tipos, na ordem das lojas, num PDF só."""
    import io

    from pypdf import PdfWriter

    rota = get_object_or_404(Rota, pk=pk)
    saida, n = PdfWriter(), 0
    for p in rota.paradas.order_by("ordem"):
        for tipo in tipos:
            if ssw.arquivo(p, tipo).exists():
                saida.append(str(ssw.arquivo(p, tipo)))
                n += 1
    if not n:
        raise Http404
    corpo = io.BytesIO()
    saida.write(corpo)
    corpo.seek(0)
    return FileResponse(corpo, content_type="application/pdf", as_attachment=True,
                        filename=f"DACTEs {nome} {rota.nome} {rota.data:%d-%m}.pdf")


def ctes_rota_pdf(request, pk):
    return _dactes_da_rota(pk, ssw.CODIGOS, "ida")


def retiradas_rota_pdf(request, pk):
    _dono(request)
    return _dactes_da_rota(pk, ["retorno"], "retorno")


@require_POST
def login_ssw(request):
    """Cada pessoa salva o próprio login do SSW; a senha vai protegida pelo Windows (rotas/windows.py)."""
    cpf, login, senha = (request.POST.get(k, "").strip() for k in ("cpf", "login", "senha"))
    if not (cpf and login and senha):
        return JsonResponse({"erro": "Preencha CPF, usuário e senha do SSW."}, status=400)
    LoginSSW.objects.update_or_create(usuario=request.user, defaults={"cpf": cpf, "login": login, "senha": proteger(senha)})
    return JsonResponse({"ok": True})


def retirada_pdf(request, chave):
    _dono(request)
    nota = NotaRetirada.objects.select_related("parada").filter(chave=chave).first()
    if not nota or not ret.arquivo(chave).exists():
        raise Http404
    return FileResponse(open(ret.arquivo(chave), "rb"), content_type="application/pdf",
                        filename=f"NF {nota.numero} - {nota.parada.loja}.pdf")  # abre no navegador
