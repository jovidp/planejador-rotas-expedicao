"""Confirma sozinho o atendimento das transações que a TOTVS Moda (API V2) já deu como Atendida.

Busca pela empresa do CD + operação da coluna + número digitado + cliente da loja ("100000" na frente do PES, ver regras.pessoa_da_loja); vale a transação
mais recente lançada até 30 dias antes da rota (a numeração se repete com os anos).
Qualquer situação (em andamento, liberada...) aparece na célula; só "Atendida" confirma o atendimento.
Desligada enquanto faltar alguma variável TOTVS_*; clicar no número continua como plano B.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone

from . import acoes
from .models import Documento, Historico, Parada, Rota
from .regras import pessoa_da_loja

log = logging.getLogger(__name__)

OPERACOES = {"t434": [434], "t3386": [3386], "almox": [5588], "sacola": [4437, 538]}  # coluna → operações TOTVS
# situação da transação (TRAFL016), na ordem dos códigos 1 a 10; a API devolve o nome em inglês
SITUACOES = {"InProgress": "Em andamento", "BillingReleased": "Liberada p/ faturamento", "PartiallyAnswered": "Parcialmente atendida",
             "Attended": "Atendida", "Closed": "Encerrada", "Canceled": "Cancelada", "PreBilled": "Pré-faturada",
             "BlockedForBilling": "Bloqueada p/ faturamento", "Recused": "Recusada", "Grouped": "Agrupada"}
PARALELAS = 8  # lojas consultadas ao mesmo tempo na rodada
INTERVALO = 1  # segundos entre consultas (número novo digitado consulta na hora, ver acordar; clique no número também, ver conferir_agora)
DIAS_PARA_TRAS = 7  # rotas antigas que ainda podem ser atendidas
JANELA = timedelta(days=30)  # a numeração recomeça com o tempo: só vale a transação lançada até 30 dias antes da rota
COLUNA = {op: col for col, ops in OPERACOES.items() for op in ops}  # operação → coluna
IGNORAR = {"Cancelada", "Recusada"}
PUXAR_A_CADA = 60  # segundos entre buscas de transações novas (uma consulta por loja e dia, ~0,2 s cada)
DIAS_ANTES = 3  # transação de até 3 dias atrás (a de sexta ou sábado vai para a rota de segunda)
REVER_ATENDIDAS = 300  # a cada 5 min confere também as já atendidas: NF cancelada deixa a transação Cancelada
RETENTAR = timedelta(hours=2)  # atendida antes de a NF aparecer na busca: procura quem faturou por mais 2 h
_token = {"valor": "", "vence": 0.0}
_usuarios = {"nomes": {}, "vence": 0.0}  # código → nome dos usuários da TOTVS
_acordar = threading.Event()


def acordar():
    """Número digitado na tela: consulta já, sem esperar o intervalo."""
    _acordar.set()


def config():
    c = {k: os.environ.get("TOTVS_" + k, "").strip() for k in ("URL", "CLIENT_ID", "CLIENT_SECRET", "USUARIO", "SENHA", "EMPRESA")}
    return c if all(c.values()) else None


def _post(url, dados, token=None):
    if token:
        corpo, cab = json.dumps(dados).encode(), {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    else:
        corpo, cab = urllib.parse.urlencode(dados).encode(), {"Content-Type": "application/x-www-form-urlencoded"}
    cab["User-Agent"] = "RotasExpedicao/1.0"  # sem nome o Cloudflare da TOTVS recusa o pedido (erro 1010)
    return _abrir(urllib.request.Request(url, corpo, cab))


def _get(url, token):
    cab = {"Authorization": f"Bearer {token}", "User-Agent": "RotasExpedicao/1.0"}
    return _abrir(urllib.request.Request(url, headers=cab))


def _abrir(pedido):
    try:
        with urllib.request.urlopen(pedido, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 401:  # token recusado antes da hora (senha trocada, TOTVS reiniciada): pede outro na próxima
            _token["vence"] = 0.0
        raise


def dados_da_nf(c, t):
    """(operador, volumes) da NF que a transação gerou: quem faturou a caixa na TOTVS ("Operador/Atualização"), com o nome
    da lista de usuários, e quantos volumes ela leva. None no que faltar: sem NF ou sem acesso à empresa da NF."""
    nf = (t or {}).get("relatedFiscalDocument") or {}
    dia = (nf.get("issueDate") or nf.get("invoiceDate") or "")[:10]
    if not (nf.get("invoiceCode") and dia):
        return None, None
    base = c["URL"].rstrip("/") + "/api/totvsmoda"
    try:
        filtro = {"branchCodeList": [nf["branchCode"]], "invoiceCodeList": [nf["invoiceCode"]],
                  "startIssueDate": dia + "T00:00:00", "endIssueDate": dia + "T23:59:59"}
        notas = _post(base + "/fiscal/v2/invoices/search", {"filter": filtro, "expand": "shippingCompany", "page": 1, "pageSize": 10},
                      token(c)).get("items") or []
        # número e sequencial da NF se repetem com os anos: só vale a nota desta transação
        nota = next((n for n in notas if n.get("transactionCode") == t.get("transactionCode")), None)
        if not nota:
            return None, None
        volumes = (nota.get("shippingCompany") or {}).get("packageNumber")
        codigo = nota.get("userCode")
        if not codigo:
            return None, volumes
        # lista de usuários da TOTVS, renovada a cada 6 h, ou antes (no máximo a cada 10 min) se aparecer usuário novo
        if time.time() > _usuarios["vence"] or (codigo not in _usuarios["nomes"] and time.time() > _usuarios["vence"] - 6 * 3600 + 600):
            nomes, pagina = {}, 1
            while True:
                r = _get(f"{base}/management/v2/users?Page={pagina}&PageSize=500", token(c))
                nomes.update({u["code"]: u.get("name") or u.get("login") or "" for u in r.get("items") or []})
                if not r.get("hasNext"):
                    break
                pagina += 1
            _usuarios.update(nomes=nomes, vence=time.time() + 6 * 3600)
        nome = re.sub(r"\(.*?\)", "", _usuarios["nomes"].get(codigo, "")).upper().split()
        # "BEATRIZ DE ANDRADE ... SANTOS (EXPEDICAO ADM)" → "BEATRIZ SANTOS"
        return (" ".join(dict.fromkeys([nome[0], nome[-1]])) if nome else None), volumes
    except urllib.error.HTTPError as e:
        log.warning("TOTVS: sem acesso ao operador da NF %s (HTTP %s); fica 'TOTVS'", nf["invoiceCode"], e.code)
        return None, None
    except Exception:  # TOTVS lenta ou fora do ar: o atendimento é confirmado assim mesmo e o nome vem numa próxima rodada
        log.exception("TOTVS: falha ao buscar o operador da NF %s; fica 'TOTVS'", nf["invoiceCode"])
        return None, None


def token(c):
    if time.time() < _token["vence"]:
        return _token["valor"]
    r = _post(c["URL"].rstrip("/") + "/api/totvsmoda/authorization/v2/token", {
        "grant_type": "password",  # client_credentials não tem permissão para buscar transações neste ambiente (403)
        "client_id": c["CLIENT_ID"], "client_secret": c["CLIENT_SECRET"], "username": c["USUARIO"], "password": c["SENHA"]})
    _token.update(valor=r["access_token"], vence=time.time() + int(r.get("expires_in", 3600)) * 0.9)
    return _token["valor"]


def situacao(v):
    nomes = list(SITUACOES.values())
    if isinstance(v, int) and 1 <= v <= len(nomes):
        return nomes[v - 1]
    return SITUACOES.get(v, str(v or ""))


def _desfeito_a_mao(doc):
    """Desmarcada no sistema e ainda desmarcada: a TOTVS não marca de novo. Marcada de novo depois: volta a conferir."""
    return not doc.atendido_por and doc.parada.rota.historico.filter(parada=doc.parada, campo=doc.tipo.codigo,
                                            acao__contains=f"desfez atendimento {doc.tipo.nome} {doc.numero} de").exists()


def pendentes(com_atendidas=False, so=None):
    """{pes: [documentos]} com número lançado, ainda não atendidos na TOTVS (mesmo os marcados à mão), em rotas ativas
    de 7 dias atrás em diante; mais os atendidos há pouco que ainda estão sem o nome de quem faturou.
    com_atendidas: também os já atendidos (NF cancelada ou excluída depois deixa a transação Cancelada). so: só esse documento."""
    sem_nome = Q(atendido_por="TOTVS", atendido_em__gte=timezone.now() - RETENTAR)
    docs = (Documento.objects.select_related("tipo", "parada__rota")
            .filter(tipo__codigo__in=OPERACOES).exclude(numero="").exclude(parada__pes=""))
    if so:
        docs = docs.filter(pk=so.pk)  # clique no número: confere em qualquer rota
    else:
        docs = docs.filter(parada__rota__status=Rota.ATIVA, parada__rota__data__gte=timezone.localdate() - timedelta(days=DIAS_PARA_TRAS))
    if not com_atendidas:
        docs = docs.exclude(Q(situacao_totvs=SITUACOES["Attended"]) & ~sem_nome)
    por_loja = {}
    for d in docs:
        if d.numero.isdigit() and d.parada.pes.isdigit():
            por_loja.setdefault(int(d.parada.pes), []).append(d)
    return por_loja


def _transacoes(c, tk, pes, docs):
    """Transações da loja com os números e as operações desses documentos."""
    corpo = {"branchCodeList": [int(e) for e in c["EMPRESA"].split(",")], "customerCodeList": [pessoa_da_loja(pes)],
             "operationCodeList": sorted({op for d in docs for op in OPERACOES[d.tipo.codigo]}),
             "transactionCodeList": sorted({int(d.numero) for d in docs}),
             "pageSize": 500}  # filtrado por número e loja: cabe numa página
    return _post(c["URL"].rstrip("/") + "/api/totvsmoda/general/v2/transactions/search", corpo, tk).get("items") or []


def _escolher(itens, d):
    """A transação do documento: a mais recente com esse número e operação lançada até 30 dias antes da rota."""
    desde = (d.parada.rota.data - JANELA).isoformat()
    iguais = [t for t in itens if t["transactionCode"] == int(d.numero) and t["operationCode"] in OPERACOES[d.tipo.codigo]
              and (t.get("transactionDate") or "")[:10] >= desde]
    return max(iguais, key=lambda t: t["transactionDate"]) if iguais else None


def notas_fiscais(docs):
    """NF-e de cada transação (434/3386/sacola), para o CT-e da ida: [{chave, numero, volumes, cnpj, origem, destino}].
    cnpj, origem e destino vêm do XML (emitente e cidades): a sacola sai da empresa 1, com outro CNPJ (conferido em 29/10/2026).
    Erro (ValueError) se alguma transação não tiver NF autorizada: CT-e sem todas as notas não sai."""
    c = config()
    if not c:
        raise ValueError("A integração com a TOTVS está desligada.")
    tk, saida = token(c), []
    for d in docs:
        t = _escolher(_transacoes(c, tk, int(d.parada.pes), [d]), d)
        nf = (t or {}).get("relatedFiscalDocument") or {}
        dia = (nf.get("issueDate") or "")[:10]
        if not (nf.get("invoiceCode") and dia):
            raise ValueError(f"{d.tipo.nome} {d.numero}: sem NF na TOTVS")
        saida.append(nota_fiscal(c, tk, nf["branchCode"], nf["invoiceCode"], dia, t.get("transactionCode"), f"{d.tipo.nome} {d.numero}"))
    return saida


def nota_fiscal(c, tk, empresa, numero, dia, transacao, rotulo):
    """A NF-e (empresa, número, dia da emissão, transação que a gerou) com chave, volumes, CNPJ do emitente e cidades."""
    import base64
    base = c["URL"].rstrip("/") + "/api/totvsmoda"
    filtro = {"branchCodeList": [empresa], "invoiceCodeList": [numero], "startIssueDate": dia + "T00:00:00", "endIssueDate": dia + "T23:59:59"}
    notas = _post(base + "/fiscal/v2/invoices/search", {"filter": filtro, "expand": "eletronic,shippingCompany", "page": 1, "pageSize": 10},
                  tk).get("items") or []
    nota = next((n for n in notas if n.get("transactionCode") == transacao), None)  # o número da NF se repete com os anos
    e = (nota or {}).get("eletronic") or {}
    if not (e.get("accessKey") and e.get("electronicInvoiceStatus") == "Authorized"):
        raise ValueError(f"{rotulo}: NF {numero} não autorizada")
    xml = base64.b64decode(_get(f"{base}/fiscal/v2/xml-contents/{e['accessKey']}", tk)["mainInvoiceXml"]).decode("utf-8", "replace")

    def tag(bloco, nome):
        m = re.search(rf"<{bloco}>.*?<{nome}>(.*?)</{nome}>", xml, re.S)
        return m[1].strip().upper() if m else ""
    return {"chave": e["accessKey"], "numero": nota["invoiceCode"], "volumes": (nota.get("shippingCompany") or {}).get("packageNumber"),
            "cnpj": tag("emit", "CNPJ"), "origem": tag("emit", "xMun"), "destino": tag("dest", "xMun")}


def entrega(docs):
    """Folha 553 (Relatório de entrega de mercadoria) de cada documento, como está na TOTVS agora: data da transação, NF,
    quem faturou, quantidade e caixas (volumes da NF). Sem contagem e pedido virtual: a API não liga a transação a eles
    (conferido em 28/10/2026). Os documentos vêm com tipo e parada__rota carregados: as threads não usam o banco."""
    c = config()
    if not c:
        raise ValueError("A integração com a TOTVS está desligada.")
    tk = token(c)

    def folha(d):
        f = {"loja": d.parada.loja, "pessoa": pessoa_da_loja(d.parada.pes), "operacao": d.tipo.nome, "numero": d.numero}
        try:
            t = _escolher(_transacoes(c, tk, d.parada.pes, [d]), d)
        except Exception:
            log.exception("TOTVS: falha ao buscar a folha 553 de %s %s", d.tipo.codigo, d.numero)
            return f | {"erro": "a TOTVS não respondeu"}
        if not t:
            return f | {"erro": "transação não encontrada na TOTVS"}
        nf = t.get("relatedFiscalDocument") or {}
        usuario, caixa = dados_da_nf(c, t)
        return f | {"data": t["transactionDate"][:10], "nf": nf.get("invoiceCode"), "usuario": usuario,
                    "quantidade": nf.get("quantity"), "caixa": caixa}

    with ThreadPoolExecutor(PARALELAS) as ex:
        return list(ex.map(folha, docs))


def sincronizar(com_atendidas=False, so=None):
    """Uma rodada: uma consulta por loja. Devolve quantos documentos mudaram de situação.
    so: confere só esse documento e deixa o erro da TOTVS subir (ver conferir_agora)."""
    c = config()
    if not c:
        return 0
    por_loja = pendentes(com_atendidas, so)
    if not por_loja:
        return 0
    usuario = _usuario()
    tk = token(c)

    # as lojas consultadas ao mesmo tempo: a rodada leva o tempo da loja mais lenta, não a soma de todas
    with ThreadPoolExecutor(PARALELAS) as ex:
        consultas = {pes: ex.submit(_transacoes, c, tk, pes, docs) for pes, docs in por_loja.items()}
    feitos = 0
    for pes, docs in por_loja.items():
        try:
            itens = consultas[pes].result()
        except Exception:  # uma loja com erro não trava as outras
            if so:
                raise
            log.exception("TOTVS: falha ao conferir a loja PES %s", pes)
            continue
        for d in docs:
            t = _escolher(itens, d)
            s = situacao(t.get("status")) if t else ""
            if s and s == d.situacao_totvs == SITUACOES["Attended"]:
                if d.atendido_por != "TOTVS" or not d.atendido_em or d.atendido_em < timezone.now() - RETENTAR:
                    continue  # atendida, com o nome (ou desistiu de achar a NF): nada mudou
                # ainda sem o nome: a NF pode ter aparecido agora
                operador, volumes = dados_da_nf(c, t)
                if operador:
                    Documento.objects.filter(pk=d.pk).update(atendido_por=operador, volumes=d.volumes if volumes is None else volumes)
                    acoes._registrar(usuario, d.parada.rota, f"informou quem atendeu {d.tipo.nome} {d.numero} de {d.parada.loja or 'sem loja'}: {operador}",
                                     parada=d.parada, campo=d.tipo.codigo, novo=s)
                    feitos += 1
                continue
            if not s or s == d.situacao_totvs:
                continue
            marcada = bool(d.atendido_por)  # atendida à mão antes da TOTVS: na Atendida vale o nome da TOTVS
            era_atendida = d.situacao_totvs == SITUACOES["Attended"]
            d.situacao_totvs = s
            d.save(update_fields=["situacao_totvs"])
            operador, volumes = dados_da_nf(c, t) if s == SITUACOES["Attended"] else (None, None)
            acoes._registrar(usuario, d.parada.rota, f"informou {d.tipo.nome} {d.numero} de {d.parada.loja or 'sem loja'}: {s}"
                             + (f" por {operador}" if operador else "")
                             + (" (era Atendida: NF cancelada ou excluída)" if era_atendida
                                else " (marcada atendida no sistema)" if marcada and s != SITUACOES["Attended"] else ""),
                             parada=d.parada, campo=d.tipo.codigo, novo=s)  # o histórico avisa as outras telas
            # desfeito à mão: a situação segue a TOTVS, mas o atendimento não volta sozinho
            if s == SITUACOES["Attended"] and not _desfeito_a_mao(d):
                if not marcada:
                    acoes.atender(usuario, d, True)
                elif not operador:  # NF ainda não aparece: "TOTVS" até achar quem faturou (ver RETENTAR)
                    Documento.objects.filter(pk=d.pk).update(atendido_por=acoes.nome_usuario(usuario), atendido_em=timezone.now())
                if operador:  # quem faturou na TOTVS, no lugar de "TOTVS" ou de quem marcou no sistema
                    Documento.objects.filter(pk=d.pk).update(atendido_por=operador)
            if volumes is not None:
                Documento.objects.filter(pk=d.pk).update(volumes=volumes)
            feitos += 1
    return feitos


def conferir_agora(doc):
    """Clique no número: confere a transação na TOTVS na hora (já atendida lá, fica marcada com o nome de quem faturou).
    Devolve a situação na TOTVS ("" se não achou a transação), ou None se não deu para consultar (sem TOTVS, fora do ar,
    loja sem PES) ou se ela já está Atendida lá: aí vale o clique."""
    if not config() or not pendentes(so=doc):
        return None
    try:
        sincronizar(so=doc)
    except Exception:
        log.exception("TOTVS: falha ao conferir %s %s na hora do clique", doc.tipo.codigo, doc.numero)
        return None
    doc.refresh_from_db()
    return doc.situacao_totvs


def _usuario():
    usuario, novo = User.objects.get_or_create(username="totvs", defaults={"first_name": "TOTVS"})
    if novo:
        usuario.set_unusable_password()
        usuario.save()
    return usuario


def puxar():
    """Lança sozinho as transações das lojas: a criada no dia D entra na próxima rota da loja depois de D,
    na coluna da operação. Não repete número que a loja já tem nem recoloca o que alguém apagou ou trocou."""
    c = config()
    if not c:
        return 0
    hoje = timezone.localdate()
    url = c["URL"].rstrip("/") + "/api/totvsmoda/general/v2/transactions/search"
    empresas = [int(e) for e in c["EMPRESA"].split(",")]
    # inclui as rotas dos últimos dias: a transação de 3 dias atrás pode ser de uma rota que já saiu
    paradas = (Parada.objects.filter(rota__status=Rota.ATIVA, rota__data__range=(hoje - timedelta(days=DIAS_ANTES), hoje + timedelta(days=DIAS_ANTES + 1)))
               .select_related("rota").order_by("rota__data", "rota__ordem", "ordem"))
    por_loja = {}
    for p in paradas:
        if p.pes.isdigit():
            por_loja.setdefault(p.pes, []).append(p)
    usuario, feitos = None, 0
    for pes, ps in por_loja.items():
        if not any(p.rota.data >= hoje for p in ps):
            continue
        ja = {(d.tipo.codigo, d.numero) for p in ps for d in p.documentos.select_related("tipo")}
        for k in range(DIAS_ANTES, -1, -1):
            dia = hoje - timedelta(days=k)
            alvo = next((p for p in ps if p.rota.data > dia), None)
            if not alvo or alvo.rota.data < hoje or (alvo.rota.data - dia).days > DIAS_ANTES:
                continue  # sem rota logo depois desse dia, ou a rota dela já saiu
            try:
                itens = _post(url, {"branchCodeList": empresas, "customerCodeList": [pessoa_da_loja(pes)], "operationCodeList": sorted(COLUNA),
                                    "transactionDate": dia.isoformat(), "pageSize": 500}, token(c)).get("items") or []
            except Exception:  # uma loja com erro não trava as outras
                log.exception("TOTVS: falha ao puxar as transações da loja PES %s em %s", pes, dia)
                continue
            for t in itens:
                col, num = COLUNA.get(t["operationCode"]), str(t["transactionCode"])
                if not col or situacao(t.get("status")) in IGNORAR or (col, num) in ja:
                    continue
                if Historico.objects.filter(parada__in=ps, campo=col, anterior=num).exists():
                    continue  # alguém apagou ou trocou esse número à mão (nesta ou em outra rota da loja)
                atuais = list(alvo.documentos.filter(tipo__codigo=col).values_list("numero", flat=True))
                usuario = usuario or _usuario()
                acoes.editar_documento(usuario, alvo, col, " ".join(atuais + [num]))
                ja.add((col, num))
                feitos += 1
    return feitos


def ciclo(com_atendidas=False):
    try:
        n = sincronizar(com_atendidas)
        if n:
            log.warning("TOTVS: %s documento(s) com situação nova", n)
    except Exception:  # TOTVS fora do ar, senha errada, timeout: tenta de novo na próxima rodada
        log.exception("TOTVS: falha na consulta")


def iniciar():
    """Chamado pelo wsgi: uma thread por servidor (o Waitress roda um processo só)."""
    if not config():
        return

    def laco():
        revisto = 0.0
        while True:
            _acordar.clear()  # limpa antes: número digitado durante a rodada gera outra rodada logo em seguida
            rever = time.monotonic() - revisto >= REVER_ATENDIDAS
            ciclo(com_atendidas=rever)
            if rever:
                revisto = time.monotonic()
            _acordar.wait(INTERVALO)

    def laco_puxar():
        while True:
            try:
                if n := puxar():
                    log.warning("TOTVS: %s transação(ões) lançada(s)", n)
                    acordar()  # confere a situação delas já
            except Exception:  # TOTVS fora do ar ou lenta: tenta de novo na próxima rodada
                log.exception("TOTVS: falha ao puxar transações")
            time.sleep(PUXAR_A_CADA)

    threading.Thread(target=laco, name="totvs", daemon=True).start()
    threading.Thread(target=laco_puxar, name="totvs-puxar", daemon=True).start()
