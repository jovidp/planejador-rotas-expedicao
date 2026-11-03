"""Toda alteração de rota passa por aqui: valida, salva e grava o histórico (com o valor anterior).
Uma ação recusada levanta ValueError com a mensagem para o usuário."""
import re

from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import Documento, Historico, Loja, Parada, Rota, RotaPadrao, TipoDocumento, Veiculo, normalizar_placa
from .regras import etapa_carga, fmt_hora, nome_usuario, parse_valor

ROTULOS = {
    "nome": "nome", "saida": "saída", "lacre": "lacre", "motorista": "motorista", "veiculo": "veículo",
    "ajudantes": "ajudantes", "rastreador": "rastreador",
    "pes": "PES", "loja": "loja", "emp": "EMP", "horario": "horário", "rt": "RT", "cx": "CX",
    "frete_envio": "frete envio", "frete_retorno": "frete retorno",
}
CAMPOS_ROTA = {"nome", "saida", "lacre", "motorista", "veiculo", "ajudantes", "rastreador"}
CAMPOS_PARADA = {"pes", "loja", "emp", "horario", "rt", "cx", "frete_envio", "frete_retorno"}
FABRICA = "FÁBRICA"
_BASE = re.compile(r"^F[AÁ]BRICA$")


def _txt(v):
    return "" if v is None else str(v)


def _registrar(user, rota, acao, parada=None, campo="", anterior="", novo=""):
    nome = nome_usuario(user)
    Historico.objects.create(usuario=nome, rota=rota, parada=parada, campo=campo,
                             anterior=_txt(anterior), novo=_txt(novo), acao=f"{nome} {acao}")


def _ordem_livre(data):
    return (Rota.objects.filter(data=data).aggregate(m=Max("ordem"))["m"] or 0) + 1


def _renumerar(paradas):
    for i, p in enumerate(paradas, 1):
        if p.ordem != i:
            p.ordem = i
            p.save(update_fields=["ordem"])


def _fixa(paradas, i):
    """A FÁBRICA no começo ou no fim da rota não sai do lugar."""
    return (i == 0 or i == len(paradas) - 1) and bool(_BASE.match(paradas[i].loja))


def tipo_veiculo(placa):
    """'accelo', 'atego' ou '' (placa vazia ou sem cadastro)."""
    v = Veiculo.objects.filter(placa=normalizar_placa(placa)).first() if placa else None
    return v.tipo if v else ""


def _frete(loja, tipo):
    return getattr(loja, f"frete_{tipo}") if tipo else None


def _do_cadastro(parada, nome, tipo=None):
    """PES e EMP vêm sempre do cadastro; horário e frete só se a célula estiver vazia (o do dia pode ser outro).
    O frete depende do caminhão (placa da rota); o de retorno é o mesmo do envio."""
    loja = Loja.objects.filter(nome=nome).first()
    if not loja:
        return
    parada.pes, parada.emp = loja.pes, loja.emp
    if not parada.horario:
        parada.horario = loja.horario
    frete = _frete(loja, tipo if tipo is not None else tipo_veiculo(parada.rota.veiculo))
    if parada.frete_envio is None:
        parada.frete_envio = frete
    if parada.frete_retorno is None:
        parada.frete_retorno = frete


# ---------- rota ----------

@transaction.atomic
def editar_rota(user, rota, campo, valor):
    if campo not in CAMPOS_ROTA:
        raise ValueError(f"Campo inválido: {campo}")
    v = (valor or "").strip()
    if campo == "nome":
        v = v.upper()
        if not v:
            raise ValueError("O nome da rota não pode ficar vazio.")
    elif campo == "saida":
        v = fmt_hora(v)
    elif campo == "rastreador":
        v = re.sub(r"^R:\s*", "", v, flags=re.I)
    elif campo == "veiculo":
        v = normalizar_placa(v)
    elif campo == "ajudantes":  # vários ajudantes: separados por vírgula, ; ou /
        v = " / ".join(n.strip() for n in re.split(r"[,;/\n]", v) if n.strip())
    anterior = getattr(rota, campo)
    if anterior == v:
        return rota
    setattr(rota, campo, v)
    rota.save(update_fields=[campo])
    acao = f"renomeou de {anterior} para {v}" if campo == "nome" else f"alterou {ROTULOS[campo]} de {anterior or '—'} para {v or '—'}"
    _registrar(user, rota, acao, campo=campo, anterior=anterior, novo=v)
    if campo == "veiculo":
        _recalcular_frete(user, rota)
    return rota


def _recalcular_frete(user, rota):
    """Placa com cadastro: o frete de todas as lojas do cadastro passa a ser o do tipo do caminhão."""
    tipo = tipo_veiculo(rota.veiculo)
    if not tipo:
        return
    lojas = {l.nome: l for l in Loja.objects.filter(nome__in=rota.paradas.values_list("loja", flat=True))}
    for p in rota.paradas.all():
        if p.loja in lojas:
            p.frete_envio = p.frete_retorno = _frete(lojas[p.loja], tipo)
            p.save(update_fields=["frete_envio", "frete_retorno"])
    _registrar(user, rota, f"recalculou o frete para {tipo.upper()} (placa {rota.veiculo})", campo="frete_envio")


@transaction.atomic
def criar_rota(user, data, nome, lojas, fora_do_planejamento):
    nome = (nome or "").strip().upper()
    if not nome:
        raise ValueError("Pesquise ou digite o nome da rota.")
    padrao = RotaPadrao.objects.filter(nome__iexact=nome).first()
    rota = Rota.objects.create(data=data, nome=nome, saida=padrao.saida if padrao else "",
                               fora_do_planejamento=fora_do_planejamento, ordem=_ordem_livre(data))
    for i, n in enumerate([FABRICA, *lojas, FABRICA], 1):
        p = Parada(rota=rota, ordem=i, loja=n.strip().upper())
        _do_cadastro(p, p.loja, "")
        p.save()
    _registrar(user, rota, "criou a rota fora do planejamento" if fora_do_planejamento else "criou a rota no planejamento")
    return rota


@transaction.atomic
def mover_rota(user, rota, nova_data):
    if not nova_data or nova_data == rota.data:
        raise ValueError("Escolha um dia diferente do atual.")
    anterior = rota.data
    rota.data, rota.remarcacoes, rota.ordem = nova_data, rota.remarcacoes + 1, _ordem_livre(nova_data)
    rota.save(update_fields=["data", "remarcacoes", "ordem"])
    _registrar(user, rota, f"moveu de {anterior:%d/%m} para {nova_data:%d/%m}", campo="data",
               anterior=anterior.isoformat(), novo=nova_data.isoformat())
    return rota


@transaction.atomic
def duplicar_rota(user, rota):
    """Cópia no mesmo dia, para dividir a rota: mesmas paradas e saída; equipe, lacre, documentos e NF não vão."""
    base = re.sub(r" \(\d+\)$", "", rota.nome)
    nomes = set(Rota.objects.filter(data=rota.data).values_list("nome", flat=True))
    nome = next(f"{base} ({n})" for n in range(2, 100) if f"{base} ({n})" not in nomes)
    nova = Rota.objects.create(data=rota.data, nome=nome, saida=rota.saida, fora_do_planejamento=rota.fora_do_planejamento,
                               ordem=_ordem_livre(rota.data))
    for p in rota.paradas.all():
        Parada.objects.create(rota=nova, ordem=p.ordem, pes=p.pes, loja=p.loja, emp=p.emp, horario=p.horario,
                              frete_envio=p.frete_envio, frete_retorno=p.frete_retorno)
    _registrar(user, rota, f"duplicou a rota em {nome}")
    _registrar(user, nova, f"criou a rota como cópia de {rota.nome}")
    return nova


@transaction.atomic
def inverter_rota(user, rota):
    """Inverte a ordem das lojas (ex.: R. Lagos começando por Búzios ou por Maricá); a FÁBRICA fica nas pontas."""
    paradas = list(rota.paradas.all())
    ini = 1 if paradas and _fixa(paradas, 0) else 0
    fim = len(paradas) - 1 if len(paradas) > 1 and _fixa(paradas, len(paradas) - 1) else len(paradas)
    paradas[ini:fim] = paradas[ini:fim][::-1]
    _renumerar(paradas)
    _registrar(user, rota, "inverteu a ordem das lojas", campo="ordem")
    return rota


def _status(user, rota, status, acao):
    if rota.status != status:
        rota.status = status
        rota.save(update_fields=["status"])
        _registrar(user, rota, acao, campo="status", novo=status)
    return rota


def cancelar_rota(user, rota):
    return _status(user, rota, Rota.CANCELADA, "cancelou a rota")


def reativar_rota(user, rota):
    return _status(user, rota, Rota.ATIVA, "reativou a rota")


@transaction.atomic
def excluir_rota(user, rota):
    """Apaga de vez (rota de teste, lançada por engano); o histórico da rota vai junto, fica só o registro no /admin."""
    _registrar_cadastro(user, rota, DELETION, f"excluiu {rota.nome} de {rota.data:%d/%m/%Y}")
    rota.delete()


# ---------- paradas ----------

@transaction.atomic
def editar_parada(user, parada, campo, valor):
    if campo not in CAMPOS_PARADA:
        raise ValueError(f"Campo inválido: {campo}")
    v = (valor or "").strip()
    if campo == "loja":
        v = v.upper()
    elif campo == "horario":
        v = fmt_hora(v)
    elif campo.startswith("frete"):
        v = parse_valor(v)
    anterior = getattr(parada, campo)
    if _txt(anterior) == _txt(v):
        return parada
    setattr(parada, campo, v)
    if campo == "loja":
        _do_cadastro(parada, v)
    if campo in ("rt", "cx"):  # outro volume: a confirmação da retirada recomeça
        setattr(parada, f"{campo}_por", "")
        setattr(parada, f"{campo}_em", None)
    parada.save()
    _registrar(user, parada.rota, f"alterou {ROTULOS[campo]} da parada {parada.ordem}º ({parada.loja or 'sem loja'}) de {_txt(anterior) or '—'} para {_txt(v) or '—'}",
               parada=parada, campo=campo, anterior=anterior, novo=v)
    return parada


@transaction.atomic
def adicionar_parada(user, rota):
    paradas = list(rota.paradas.all())
    nova = Parada.objects.create(rota=rota, ordem=len(paradas) + 1)
    pos = len(paradas) - 1 if paradas and _fixa(paradas, len(paradas) - 1) else len(paradas)
    paradas.insert(pos, nova)
    _renumerar(paradas)
    _registrar(user, rota, f"adicionou uma parada na posição {pos + 1}º", parada=nova)
    return nova


@transaction.atomic
def mover_parada(user, parada, direcao):
    paradas = list(parada.rota.paradas.all())
    i = next(k for k, p in enumerate(paradas) if p.pk == parada.pk)
    j = i + direcao
    if direcao not in (-1, 1) or not 0 <= j < len(paradas) or _fixa(paradas, i) or _fixa(paradas, j):
        raise ValueError("A FÁBRICA fica fixa no começo e no fim da rota.")
    paradas[i], paradas[j] = paradas[j], paradas[i]
    _renumerar(paradas)
    _registrar(user, parada.rota, f"moveu a parada {paradas[j].loja or 'sem loja'} para a posição {j + 1}º", parada=paradas[j], campo="ordem")
    return paradas[j]


@transaction.atomic
def remover_parada(user, parada):
    paradas = list(parada.rota.paradas.all())
    i = next(k for k, p in enumerate(paradas) if p.pk == parada.pk)
    if _fixa(paradas, i):
        raise ValueError("A FÁBRICA fica fixa no começo e no fim da rota.")
    rota = parada.rota
    _registrar(user, rota, f"removeu a parada {parada.loja or 'sem loja'} ({i + 1}º)", campo="parada", anterior=parada.loja)
    parada.delete()
    _renumerar(paradas[:i] + paradas[i + 1:])


# ---------- documentos e NF ----------

def _tem_carga(parada):
    return parada.documentos.filter(tipo__eh_carga=True).exclude(numero="").exists()


_ETAPA_CAMPOS = ["expedida_por", "expedida_em", "atendido_por", "atendido_em", "concluido_por", "concluido_em", "situacao_totvs",
                 "aviso_pedidos", "pedidos_detalhe", "pedidos_fase"]


def _limpar_etapas(doc):
    for campo in _ETAPA_CAMPOS:
        setattr(doc, campo, None if campo.endswith("_em") else "")


_SEPARA_NUMEROS = re.compile(r"[\s,;/+]+")


@transaction.atomic
def editar_documento(user, parada, codigo_tipo, numero):
    """Lança os números de um tipo na parada. Vários números (separados por espaço ou vírgula) viram um documento
    cada, com atendimento (ou, na carga WMS, etapas) próprio. Devolve o primeiro documento (ou None)."""
    tipo = TipoDocumento.objects.filter(codigo=codigo_tipo).first()
    if not tipo:
        raise ValueError(f"Tipo de documento inválido: {codigo_tipo}")
    novos = list(dict.fromkeys(n for n in _SEPARA_NUMEROS.split((numero or "").strip()) if n))
    docs = list(parada.documentos.filter(tipo=tipo).order_by("id"))
    atuais = [d.numero for d in docs]
    if atuais == novos:
        return docs[0] if docs else None
    loja = parada.loja or "sem loja"
    if len(docs) == 1 and len(novos) == 1:
        doc, anterior = docs[0], docs[0].numero
        doc.numero = novos[0]
        _limpar_etapas(doc)  # outro número: atendimento (e o fluxo da carga) recomeça
        doc.save()
        _registrar(user, parada.rota, f"alterou {tipo.nome} de {loja} de {anterior} para {doc.numero}",
                   parada=parada, campo=codigo_tipo, anterior=anterior, novo=doc.numero)
        return doc
    for d in docs:
        if d.numero not in novos:
            d.delete()  # sem documento não há atendimento
            _registrar(user, parada.rota, f"apagou {tipo.nome} {d.numero} de {loja}", parada=parada, campo=codigo_tipo, anterior=d.numero)
    for n in novos:
        if n not in atuais:
            Documento.objects.create(parada=parada, tipo=tipo, numero=n)
            _registrar(user, parada.rota, f"lançou {tipo.nome} {n} em {loja}", parada=parada, campo=codigo_tipo, novo=n)
    return parada.documentos.filter(tipo=tipo).order_by("id").first()


@transaction.atomic
def atender(user, documento, sim):
    if documento.tipo.eh_carga:
        raise ValueError("A carga segue as etapas: expedida, em atendimento, atendida.")
    if sim and not documento.numero:
        raise ValueError("Lance o número antes de confirmar o atendimento.")
    if sim and documento.atendido_por:  # já atendida (tela desatualizada): não troca o nome de quem faturou na TOTVS
        return documento
    documento.atendido_por = nome_usuario(user) if sim else ""
    documento.atendido_em = timezone.now() if sim else None
    documento.save(update_fields=["atendido_por", "atendido_em"])
    p = documento.parada
    _registrar(user, p.rota, f"{'confirmou' if sim else 'desfez'} atendimento {documento.tipo.nome} {documento.numero} de {p.loja or 'sem loja'}",
               parada=p, campo=documento.tipo.codigo)
    return documento


# próxima etapa: (campos de quem/quando que ela grava, texto do histórico)
_AVANCO = {"criada": ("expedida", "marcou a carga {n} como expedida"),
           "expedida": ("atendido", "iniciou o atendimento da carga {n}"),
           "em_atendimento": ("concluido", "concluiu o atendimento da carga {n}")}
_VOLTA = {"expedida": "expedida", "em_atendimento": "atendido", "atendida": "concluido"}


def _carga(documento):
    if not documento.tipo.eh_carga:
        raise ValueError("Só a carga WMS tem etapas.")
    if not documento.numero:
        raise ValueError("Lance o número da carga primeiro.")


@transaction.atomic
def avancar_carga(user, documento):
    """Criada → expedida → em atendimento (grava quem pegou) → atendida."""
    _carga(documento)
    etapa = etapa_carga(documento)
    if etapa not in _AVANCO:
        raise ValueError("A carga já está atendida.")
    prefixo, texto = _AVANCO[etapa]
    setattr(documento, f"{prefixo}_por", nome_usuario(user))
    setattr(documento, f"{prefixo}_em", timezone.now())
    documento.save()
    p = documento.parada
    _registrar(user, p.rota, f"{texto.format(n=documento.numero)} de {p.loja or 'sem loja'}", parada=p, campo=documento.tipo.codigo)
    return documento


@transaction.atomic
def voltar_carga(user, documento):
    """Desfaz a última etapa (fica no histórico)."""
    _carga(documento)
    etapa = etapa_carga(documento)
    if etapa not in _VOLTA:
        raise ValueError("A carga está no começo do fluxo.")
    p = documento.parada
    prefixo = _VOLTA[etapa]
    setattr(documento, f"{prefixo}_por", "")
    setattr(documento, f"{prefixo}_em", None)
    documento.save()
    _registrar(user, p.rota, f"voltou a carga {documento.numero} de {p.loja or 'sem loja'} para {etapa_carga(documento).replace('_', ' ')}",
               parada=p, campo=documento.tipo.codigo)
    return documento


@transaction.atomic
def confirmar_retirada(user, parada, campo, sim):
    """Clique no volume da retirada (RT ou CX): grava quem confirmou e quando; sim=False desfaz."""
    if campo not in ("rt", "cx"):
        raise ValueError(f"Campo inválido: {campo}")
    volume = getattr(parada, campo)
    if sim and not volume:
        raise ValueError("Lance o volume antes de confirmar a retirada.")
    setattr(parada, f"{campo}_por", nome_usuario(user) if sim else "")
    setattr(parada, f"{campo}_em", timezone.now() if sim else None)
    parada.save(update_fields=[f"{campo}_por", f"{campo}_em"])
    _registrar(user, parada.rota, f"{'confirmou' if sim else 'desfez'} retirada {campo.upper()} {volume} de {parada.loja or 'sem loja'}",
               parada=parada, campo=campo)
    return parada


# ---------- planejamento da semana ----------

@transaction.atomic
def planejar_semana(user, marcacoes, desmarcacoes=None):
    """marcacoes {data: [rotas do cadastro]}: cria (com as lojas do cadastro) ou reativa a cancelada do dia.
    desmarcacoes {data: [nomes]}: cancela — rota nunca é apagada."""
    res = {"criadas": [], "reativadas": [], "canceladas": []}
    for data, nomes in sorted((desmarcacoes or {}).items()):
        for nome in nomes:
            for r in Rota.objects.filter(data=data, nome=(nome or "").strip().upper(), status=Rota.ATIVA):
                res["canceladas"].append(cancelar_rota(user, r))
    for data, nomes in sorted(marcacoes.items()):
        for nome in nomes:
            padrao = RotaPadrao.objects.filter(nome__iexact=(nome or "").strip()).first()
            if not padrao:
                raise ValueError(f"Rota fora do cadastro: {nome}")
            do_dia = Rota.objects.filter(data=data, nome=padrao.nome)
            if do_dia.filter(status=Rota.ATIVA).exists():
                continue
            cancelada = do_dia.filter(status=Rota.CANCELADA).order_by("-id").first()
            if cancelada:
                res["reativadas"].append(reativar_rota(user, cancelada))
                continue
            lojas = padrao.lojas.order_by("ordem_na_rota").values_list("nome", flat=True)
            res["criadas"].append(criar_rota(user, data, padrao.nome, list(lojas), False))
    return res


# ---------- veículos ----------

_PLACA = re.compile(r"^[A-Z]{3}\d[A-Z0-9]\d{2}$")  # ABC1234 (antiga) ou BRA2E19 (Mercosul)


@transaction.atomic
def editar_rota_padrao(user, nome, lojas):
    """Troca as lojas de uma rota do cadastro, na ordem dada. A loja que sai fica sem rota (continua no cadastro);
    loja de outra rota muda para esta. As rotas já criadas não mudam."""
    rp = RotaPadrao.objects.filter(nome=nome).first()
    if not rp:
        raise ValueError(f"A rota {nome} não está no cadastro.")
    nomes = list(dict.fromkeys(n.strip().upper() for n in lojas if n and n.strip()))
    achadas = {l.nome: l for l in Loja.objects.filter(nome__in=nomes)}
    faltam = [n for n in nomes if n not in achadas]
    if faltam:
        raise ValueError(f"Fora do cadastro de lojas: {', '.join(faltam)}")
    antes = list(rp.lojas.order_by("ordem_na_rota").values_list("nome", flat=True))
    rp.lojas.exclude(nome__in=nomes).update(rota_padrao=None, ordem_na_rota=0)
    for i, n in enumerate(nomes, 1):
        achadas[n].rota_padrao, achadas[n].ordem_na_rota = rp, i
        achadas[n].save(update_fields=["rota_padrao", "ordem_na_rota"])
    saiu, entrou = [n for n in antes if n not in nomes], [n for n in nomes if n not in antes]
    _registrar_cadastro(user, rp, CHANGE, "; ".join(filter(None, [
        f"tirou {', '.join(saiu)}" if saiu else "", f"acrescentou {', '.join(entrou)}" if entrou else "", "ordem: " + " > ".join(nomes)])))
    return rp


def _registrar_cadastro(user, obj, acao, mensagem):
    """O cadastro (rotas do cadastro, veículos) não tem histórico de rota: fica no registro do /admin, com quem e quando."""
    if getattr(user, "pk", None):
        LogEntry.objects.log_actions(user.pk, [obj], acao, change_message=mensagem, single_object=True)


@transaction.atomic
def salvar_veiculo(user, placa, tipo, descricao=""):
    """Cadastra ou altera; as rotas de hoje em diante com essa placa passam a usar o frete do tipo."""
    placa = normalizar_placa(placa)
    if not _PLACA.match(placa):
        raise ValueError("Placa inválida. Use o formato ABC1234 ou BRA2E19.")
    if tipo not in (Veiculo.ACCELO, Veiculo.ATEGO):
        raise ValueError("Escolha Accelo ou Atego.")
    v, novo = Veiculo.objects.update_or_create(placa=placa, defaults={"tipo": tipo, "descricao": (descricao or "").strip()[:60]})
    _registrar_cadastro(user, v, ADDITION if novo else CHANGE, f"{v.placa}: {v.get_tipo_display()}")
    from django.utils import timezone
    for rota in Rota.objects.filter(veiculo=placa, data__gte=timezone.localdate(), status=Rota.ATIVA):
        _recalcular_frete(user, rota)
    return v


def remover_veiculo(user, placa):
    for v in Veiculo.objects.filter(placa=normalizar_placa(placa)):
        _registrar_cadastro(user, v, DELETION, f"removeu {v.placa}")
        v.delete()
