"""Notas de retirada das lojas (RT e CX) em PDF, para fazer os CT-es. Só os usuários de DONOS veem (/retiradas/).

Para cada loja com RT ou CX numa rota ativa, busca na TOTVS as NF-e de saída que a loja emitiu para o CD (pessoa
100000233) de 3 dias antes até a véspera da rota, e guarda o DANFE em ROTAS_DADOS/retiradas. Em 27/10/2026 era essa
janela que batia com o RT/CX lançado (25 de 26 lojas); notas mais antigas ou do próprio dia da rota ficam de fora.
RT = transferências para o CD (1448, 1499, 3343...); CX = embalagens vazias (3341). Nota cancelada sai da lista.
"""
import base64
import logging
import threading
import urllib.error
from datetime import timedelta

from brazilfiscalreport.danfe import Danfe
from django.conf import settings
from django.utils import timezone

from . import totvs
from .models import NotaRetirada, Parada, Rota

log = logging.getLogger(__name__)

DONOS = {"joao", "rafael.mendes"}
CD = 100000233  # o próprio CD como pessoa na TOTVS
CX = {3341}  # SAIDA TRANSFERENCIA EMBALAGENS VAZIAS; as outras notas de saída para o CD são RT
DIAS_ANTES, DIAS_DEPOIS = 7, 3  # rotas de 7 dias atrás até 3 dias à frente
JANELA = 3  # notas do dia da rota e dos 2 anteriores (o sábado entra na rota de segunda)
INTERVALO = 600  # segundos entre buscas (abrir a página busca na hora)
_acordar = threading.Event()
_trava = threading.Lock()  # o botão "Buscar agora" e a busca de 10 em 10 min não baixam o mesmo PDF ao mesmo tempo


def acordar():
    _acordar.set()


def arquivo(chave):
    return settings.DADOS / "retiradas" / f"{chave}.pdf"


def tem_retirada(p):
    return any(v.strip() not in ("", "0") for v in (p.rt, p.cx))


def paradas():
    hoje = timezone.localdate()
    ps = (Parada.objects.select_related("rota").prefetch_related("notas_retirada")
          .filter(rota__status=Rota.ATIVA, rota__data__range=(hoje - timedelta(days=DIAS_ANTES), hoje + timedelta(days=DIAS_DEPOIS)))
          .order_by("-rota__data", "rota__ordem", "rota__id", "ordem"))
    # as de hoje primeiro (CT-es do dia), depois as mais novas
    return sorted((p for p in ps if tem_retirada(p)), key=lambda p: p.rota.data != hoje)


def baixar(c, chave):
    """XML da NF-e → DANFE em PDF pela própria TOTVS. Ela recusa algumas notas, uma a uma ("8816 - Modelo de NF não
    informado" ou "."; outras da mesma loja e operação saem) ou não responde a tempo (28/10, NF 18490 e 18491): essas o
    DANFE é gerado aqui, do mesmo XML."""
    base = c["URL"].rstrip("/") + "/api/totvsmoda/fiscal/v2"
    xml = totvs._get(f"{base}/xml-contents/{chave}", totvs.token(c))["mainInvoiceXml"]  # já vem em base64
    try:
        pdf = base64.b64decode(totvs._post(base + "/danfe-search", {"mainInvoiceXml": xml}, totvs.token(c))["danfePdfBase64"])
    except (urllib.error.HTTPError, TimeoutError) as e:  # o XML já veio: o DANFE sai dele sem esperar a TOTVS
        log.warning("Retiradas: TOTVS não gerou o DANFE da chave %s (%s); gerado pelo sistema", chave, e)
        pdf = bytes(Danfe(xml=base64.b64decode(xml)).output())
    destino = arquivo(chave)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")  # PDF pela metade não passa por pronto
    tmp.write_bytes(pdf)
    tmp.replace(destino)


def sincronizar():
    """Uma busca por loja com retirada. Devolve quantas notas novas entraram."""
    with _trava:
        return _sincronizar()


def _sincronizar():
    c = totvs.config()
    if not c:
        return 0
    url = c["URL"].rstrip("/") + "/api/totvsmoda/fiscal/v2/invoices/search"
    novas = 0
    for p in paradas():
        if not p.emp.isdigit():
            continue
        filtro = {"branchCodeList": [int(p.emp)], "personCodeList": [CD], "operationType": "Output",
                  "startIssueDate": f"{p.rota.data - timedelta(days=JANELA - 1)}T00:00:00",
                  "endIssueDate": f"{p.rota.data}T23:59:59"}
        try:
            itens = totvs._post(url, {"filter": filtro, "expand": "eletronic,shippingCompany", "page": 1, "pageSize": 100},
                                totvs.token(c)).get("items") or []
        except Exception:  # uma loja com erro não trava as outras
            log.exception("Retiradas: falha ao buscar as notas da EMP %s", p.emp)
            continue
        validas = {}
        for n in itens:
            e = n.get("eletronic") or {}
            if e.get("accessKey") and e.get("electronicInvoiceStatus") == "Authorized":
                validas[e["accessKey"]] = n
        p.notas_retirada.exclude(chave__in=validas).delete()  # cancelada ou de outra rota
        for chave, n in validas.items():
            if not arquivo(chave).exists():
                try:
                    baixar(c, chave)
                except Exception as e:  # ex.: "8816 - Modelo de NF não informado" em notas 3343; a nota aparece sem PDF e tenta de novo
                    log.warning("Retiradas: TOTVS não gerou o PDF da NF %s da EMP %s: %s", n.get("invoiceCode"), p.emp, e)
            _, nova = NotaRetirada.objects.update_or_create(parada=p, chave=chave, defaults={
                "numero": n["invoiceCode"], "serie": n.get("serialCode") or "", "emissao": n["issueDate"][:10],
                "operacao": n["operationCode"],  # a TOTVS manda um caractere de controle (\x10) antes do nome
                "nome_operacao": "".join(ch for ch in n.get("operatioName") or "" if ch.isprintable()).strip()[:100],
                "volumes": (n.get("shippingCompany") or {}).get("packageNumber"), "valor": n.get("totalValue")})
            novas += nova
    return novas


def iniciar():
    """Chamado pelo wsgi, se a TOTVS estiver configurada."""
    if not totvs.config():
        return

    def laco():
        while True:
            _acordar.clear()
            try:
                if n := sincronizar():
                    log.warning("Retiradas: %s nota(s) nova(s) em PDF", n)
            except Exception:
                log.exception("Retiradas: falha na busca")
            _acordar.wait(INTERVALO)

    threading.Thread(target=laco, name="retiradas", daemon=True).start()
