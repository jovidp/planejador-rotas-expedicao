"""Confere, um por um, os pedidos de cada carga na TOTVS (empresa 209).

Antes de expedir, os pedidos ficam Bloqueados (normal); expedida a carga, ficam Em andamento; atendidos no PEDFM099,
viram Atendido ou Parcialmente atendido. Depois de expedida, avisa pedido Bloqueado, Cancelado ou Parcialmente atendido;
quando todos foram atendidos no 099, dá o aviso final (Parcialmente atendido ou Cancelado) e para de conferir a carga.
A carga só é marcada expedida no sistema, por quem expediu; a partir daí, todos atendidos no 099 marcam atendida.
Carga marcada atendida no sistema com pedido ainda Em andamento também avisa.
Os pedidos da carga vêm do relatório da Alcis; o número do pedido se repete entre empresas e anos, então só vale o da
empresa 209 e da loja da carga.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from . import acoes, alcis, totvs
from .models import Documento, Rota

log = logging.getLogger(__name__)

EMPRESA = 209
INTERVALO = 1  # segundos entre rodadas (só consulta a TOTVS; a Alcis vai em segundo plano, ver _volumes_das_cargas)
ALCIS_A_CADA = 60  # segundos para baixar de novo os volumes de uma carga na Alcis
DIAS = 3  # cargas de rotas de até 3 dias atrás
SITUACOES = {"InProgress": "Em andamento", "BillingReleased": "Liberado p/ faturamento", "PartiallyAnswered": "Parcialmente atendido",
             "Attended": "Atendido", "Blocked": "Bloqueado", "Canceled": "Cancelado", "InComposition": "Em composição",
             "InAnalysis": "Em análise", "Missing": "Fora da TOTVS"}  # Missing: pedido da Alcis sem pedido da loja na TOTVS (empresa 209)
PROBLEMAS = ["Blocked", "Canceled", "PartiallyAnswered", "Missing"]  # avisados depois de expedida
FINAIS = {"Attended", "PartiallyAnswered", "Canceled"}  # atendido no PEDFM099 (ou cancelado): fim da conferência
_pool = ThreadPoolExecutor(4, thread_name_prefix="alcis-pedidos")  # o portal leva uns 4 s por carga: 4 de cada vez
_pool_totvs = ThreadPoolExecutor(8, thread_name_prefix="pedidos-totvs")  # separado: download lento da Alcis não segura a TOTVS
_volumes = {}  # pk da carga → (número, volumes na Alcis ou None se falhou, quando baixou)
_baixando = {}  # pk da carga → (número, download em segundo plano)


def _baixar(carga):
    try:
        return alcis.baixar_carga(int(carga.numero))
    except Exception:  # portal fora do ar: as outras cargas seguem, esta tenta de novo na próxima rodada
        log.exception("Pedidos: falha ao baixar a carga %s da Alcis", carga.numero)
        return None


def _volumes_das_cargas(cargas):
    """{pk: volumes} das cargas na Alcis. A carga nova espera o download; as outras usam o último e baixam de novo a cada
    ALCIS_A_CADA em segundo plano, sem segurar a rodada: o que muda rápido é a situação dos pedidos, na TOTVS."""
    agora = time.monotonic()
    for pk, (numero, f) in list(_baixando.items()):
        if f.done():
            del _baixando[pk]
            volumes, antigo = f.result(), _volumes.get(pk)
            if volumes is None and antigo and antigo[0] == numero:
                volumes = antigo[1]  # portal falhou: fica com o último download
            _volumes[pk] = (numero, volumes, agora)
    novas = [c for c in cargas if _volumes.get(c.pk, ("",))[0] != c.numero]
    for c, volumes in zip(novas, _pool.map(_baixar, novas)):
        _volumes[c.pk] = (c.numero, volumes, agora)  # falhou (None): tenta de novo depois de ALCIS_A_CADA, sem travar as rodadas
    for c in cargas:
        numero, _, quando = _volumes[c.pk]
        if numero == c.numero and agora - quando >= ALCIS_A_CADA and c.pk not in _baixando:
            _baixando[c.pk] = (c.numero, _pool.submit(_baixar, c))
    for pk in set(_volumes) - {c.pk for c in cargas}:
        del _volumes[pk]
    return {c.pk: _volumes[c.pk][1] for c in cargas if _volumes[c.pk][0] == c.numero and _volumes[c.pk][1] is not None}


def _marcar(carga, prefixo, texto, usuario, nome=None):
    setattr(carga, f"{prefixo}_por", nome or acoes.nome_usuario(usuario))
    setattr(carga, f"{prefixo}_em", timezone.now())
    carga.save(update_fields=[f"{prefixo}_por", f"{prefixo}_em"])
    acoes._registrar(usuario, carga.parada.rota, f"{texto} {carga.numero} de {carga.parada.loja or 'sem loja'} (pedidos na TOTVS)",
                     parada=carga.parada, campo=carga.tipo.codigo)


def _quem_atendeu(notas):
    """Quem faturou a NF mais recente dos pedidos (o atendimento no PEDFM099); None se não achar."""
    nf = max(notas, key=lambda n: (n.get("issueDate") or "", n.get("code") or 0), default=None)
    if not nf:
        return None
    t = {"transactionCode": nf.get("transactionCode"),
         "relatedFiscalDocument": {"branchCode": nf.get("transactionBranchCode"), "invoiceCode": nf.get("code"), "issueDate": nf.get("issueDate")}}
    return totvs.dados_da_nf(totvs.config(), t)[0]


def _numeros(volumes):
    return sorted({int(v["PEDIDO"]) for v in volumes if str(v.get("PEDIDO", "")).isdigit()})


def _buscar(numeros):
    """Os pedidos na TOTVS (empresa 209), com as NFs; [] sem pedido. Sem banco: roda em paralelo, uma por carga."""
    if not numeros:
        return []
    c = totvs.config()
    url = c["URL"].rstrip("/") + "/api/totvsmoda/sales-order/v2/orders/search"
    return totvs._post(url, {"filter": {"branchCodeList": [EMPRESA], "orderCodeList": numeros}, "expand": "invoices", "page": 1, "pageSize": 500},
                       totvs.token(c)).get("items") or []


def _situacao_dos_pedidos(carga, volumes, itens):
    """[(pedido, situação, NFs)] da carga, um por um, pelos volumes dela na Alcis e os pedidos na TOTVS (itens);
    [] se ainda não tem volume. Grava quantos volumes são."""
    n = len({v.get("NR_VOL") for v in volumes if v.get("NR_VOL")}) or None  # uma linha por volume embalado
    if n != carga.volumes:
        carga.volumes = n
        carga.save(update_fields=["volumes"])
    lojas = {v.get("COD_CLIENTE") for v in volumes}
    numeros = _numeros(volumes)
    if not numeros:
        return []
    achados = [(str(i["orderCode"]), i.get("statusOrder"), i.get("invoices") or []) for i in itens if str(i.get("customerCode")) in lojas]
    vistos = {p for p, _, _ in achados}
    # sem ele na TOTVS não dá para dizer que a carga foi atendida: avisa em vez de ignorar
    return achados + [(str(n), "Missing", []) for n in numeros if str(n) not in vistos]


def notas_fiscais(carga):
    """NF-e da carga WMS, para o CT-e da ida: as notas distintas dos pedidos dela atendidos no 099 (vários pedidos caem
    numa NF só: em 28/10 os 8 pedidos da loja 335 foram todos na NF 85834). Erro se algum pedido não tiver NF."""
    volumes = alcis.baixar_carga(int(carga.numero))
    lojas = {v.get("COD_CLIENTE") for v in volumes}
    nfs = set()
    for i in _buscar(_numeros(volumes)):
        if str(i.get("customerCode")) not in lojas or i.get("statusOrder") == "Canceled":
            continue
        if not i.get("invoices"):
            raise ValueError(f"carga {carga.numero}: pedido {i['orderCode']} sem NF")
        for n in i["invoices"]:
            nfs.add((n.get("transactionBranchCode"), n.get("code"), (n.get("issueDate") or "")[:10], n.get("transactionCode")))
    if not nfs:
        raise ValueError(f"carga {carga.numero}: nenhuma NF nos pedidos")
    c = totvs.config()
    tk = totvs.token(c)
    return [totvs.nota_fiscal(c, tk, *nf, f"carga {carga.numero}") for nf in sorted(nfs)]


def conferir():
    """Uma rodada. Devolve quantas cargas mudaram de aviso ou de fase."""
    if not (totvs.config() and alcis.config()):
        return 0
    Documento.objects.filter(tipo__eh_carga=True, expedida_em=None).exclude(pedidos_fase="").update(
        aviso_pedidos="", pedidos_detalhe="", pedidos_fase="")  # voltou para "criada": confere de novo quando expedir
    # atendida no 099 sai da conferência; menos a que ainda espera a NF (sai 2 a 3 min depois) para ter o nome de quem atendeu
    sem_nome = Q(concluido_por="TOTVS", concluido_em__gte=timezone.now() - totvs.RETENTAR)
    cargas = (Documento.objects.select_related("tipo", "parada__rota")
              .filter(tipo__eh_carga=True, parada__rota__status=Rota.ATIVA,
                      parada__rota__data__gte=timezone.localdate() - timedelta(days=DIAS))
              .exclude(numero="").exclude(Q(pedidos_fase="atendida") & ~sem_nome))
    usuario, mudaram = None, 0
    cargas = [c for c in cargas if c.numero.isdigit()]
    volumes = _volumes_das_cargas(cargas)
    cargas = [c for c in cargas if c.pk in volumes]
    # uma consulta por carga, todas ao mesmo tempo: a rodada leva o tempo da mais lenta
    buscas = list(_pool_totvs.map(lambda c: _buscar(_numeros(volumes[c.pk])), cargas))
    for carga, itens in zip(cargas, buscas):
        pedidos = _situacao_dos_pedidos(carga, volumes[carga.pk], itens)
        if not pedidos:
            continue
        if not carga.expedida_em:
            continue  # só a expedição marca a carga expedida; antes disso pedido bloqueado é normal
        situacoes = {s for _, s, _ in pedidos}
        atendida = situacoes <= FINAIS
        notas = [n for _, _, ns in pedidos for n in ns]
        if atendida and not carga.concluido_em:
            usuario = usuario or totvs._usuario()
            _marcar(carga, "concluido", "concluiu o atendimento da carga", usuario, _quem_atendeu(notas))
            mudaram += 1
        elif atendida and (carga.pedidos_fase != "atendida" or carga.concluido_por == "TOTVS"):
            # concluída à mão antes do 099, ou ainda esperando a NF: vale o nome de quem atendeu na TOTVS
            nome = _quem_atendeu(notas)
            if nome and nome != carga.concluido_por:
                usuario = usuario or totvs._usuario()
                carga.concluido_por = nome
                carga.save(update_fields=["concluido_por"])
                acoes._registrar(usuario, carga.parada.rota, f"informou quem atendeu a carga {carga.numero} de {carga.parada.loja or 'sem loja'}: {nome}",
                                 parada=carga.parada, campo=carga.tipo.codigo)
                mudaram += 1
            elif not nome and carga.pedidos_fase != "atendida":  # NF ainda não saiu: "TOTVS" até achar (por até 2 h)
                carga.concluido_por, carga.concluido_em = acoes.nome_usuario(totvs._usuario()), timezone.now()
                carga.save(update_fields=["concluido_por", "concluido_em"])
        olhar = [s for s in PROBLEMAS if s != "Blocked"] if atendida else PROBLEMAS
        if carga.concluido_em and not atendida:
            olhar = olhar + ["InProgress"]  # atendida no sistema, mas o 099 ainda não atendeu
        por = {s: [p for p, x, _ in pedidos if x == s] for s in SITUACOES}
        aviso = ", ".join(f"{len(por[s])} " + ("fora da TOTVS" if s == "Missing" else
                                                 f"{SITUACOES[s].lower()}{'s' if len(por[s]) > 1 and s != 'InProgress' else ''}")
                          for s in olhar if por[s])
        detalhe = "; ".join(f"{SITUACOES[s]}: {', '.join(p)}" for s, p in por.items() if p)
        fase = "atendida" if atendida else "expedida"
        if (aviso, detalhe, fase) == (carga.aviso_pedidos, carga.pedidos_detalhe, carga.pedidos_fase):
            continue
        avisou = aviso != carga.aviso_pedidos
        carga.aviso_pedidos, carga.pedidos_detalhe, carga.pedidos_fase = aviso, detalhe, fase
        carga.save(update_fields=["aviso_pedidos", "pedidos_detalhe", "pedidos_fase"])
        if avisou:  # o histórico avisa as outras telas
            usuario = usuario or totvs._usuario()
            acoes._registrar(usuario, carga.parada.rota, f"conferiu os pedidos da carga {carga.numero} de {carga.parada.loja or 'sem loja'}: "
                             + (aviso or "todos certos"), parada=carga.parada, campo=carga.tipo.codigo)
        mudaram += 1
    return mudaram


def iniciar():
    """Chamado pelo wsgi: uma thread por servidor."""
    import threading
    if not (totvs.config() and alcis.config()):
        return

    def laco():
        while True:
            try:
                if n := conferir():
                    log.warning("Pedidos: %s carga(s) com situação nova", n)
            except Exception:  # TOTVS ou Alcis fora do ar: tenta de novo na próxima rodada
                log.exception("Pedidos: falha ao conferir")
            time.sleep(INTERVALO)

    threading.Thread(target=laco, name="pedidos", daemon=True).start()
