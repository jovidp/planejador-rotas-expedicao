"""Confere, escondido, o que o sistema puxou da TOTVS com o que a expedição preenche no Checklist Expedição.

Só lê o checklist (API do próprio sistema, http://<servidor>:3040) e escreve as diferenças em
DADOS/conferencia_checklist.log. Não muda nada no banco nem na tela: quem manda é a TOTVS, o checklist só serve de
comparação. Por loja (data de entrega + PES): 434, 3386, carga WMS, almoxarifado (que no checklist também leva a
sacola) e os volumes. "Só no sistema" só é anotado depois de o checklist ser finalizado, porque antes ele ainda está sendo
preenchido. Desligada sem CHECKLIST_URL, CHECKLIST_USUARIO e CHECKLIST_SENHA.
"""
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from .models import Documento, Rota

log = logging.getLogger(__name__)

INTERVALO = 600  # segundos entre conferências
DIAS = 1  # entregas de ontem a amanhã
TIPOS = {"TR_434": ("t434",), "TR_3386": ("t3386",), "WMS": ("wms",), "ALMOXARIFADO": ("almox", "sacola")}
NOMES = {"t434": "434", "t3386": "3386", "wms": "Carga WMS", "almox": "Almox", "sacola": "Sacola"}
_token = {"valor": ""}
_avisados = set()  # diferenças já escritas no arquivo: cada uma entra uma vez (de novo só se o servidor reiniciar)


def _variavel(nome):
    """Variável de ambiente; se o servidor subiu antes do setx, lê direto do registro do usuário (Windows)."""
    v = os.environ.get(nome, "").strip()
    if v or os.name != "nt":
        return v
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, nome)[0]).strip()
    except OSError:
        return ""


def config():
    c = {k: _variavel("CHECKLIST_" + k) for k in ("URL", "USUARIO", "SENHA")}
    return c if all(c.values()) else None


def _pedir(c, caminho, corpo=None):
    if not _token["valor"]:
        _token["valor"] = _abrir(c, "/api/auth/login", {"nickname": c["USUARIO"], "password": c["SENHA"]}, None)["token"]
    try:
        return _abrir(c, caminho, corpo, _token["valor"])
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise
        _token["valor"] = ""  # sessão vencida: entra de novo uma vez
        return _pedir(c, caminho, corpo)


def _abrir(c, caminho, corpo, token):
    cab = {"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})}
    pedido = urllib.request.Request(c["URL"].rstrip("/") + caminho, json.dumps(corpo).encode() if corpo else None, cab)
    with urllib.request.urlopen(pedido, timeout=30) as r:
        return json.load(r)


def _entrega(texto):
    """'2026-10-28T03:00:00.000Z' (meia-noite de Brasília em UTC) → date(2026, 10, 28)."""
    return timezone.localtime(datetime.fromisoformat(texto.replace("Z", "+00:00"))).date()


def ler_checklists(c, dias):
    """{(data de entrega, PES): {"lugar", "finalizado", "itens": {(número, tipo do checklist): volume}}} das entregas em dias."""
    lojas, pagina = {}, 1
    while True:
        lista = _pedir(c, f"/api/checklists?page={pagina}&perPage=50")
        for r in lista["rows"]:
            data = _entrega(r["delivery_date"])
            if data not in dias or r["status"] == "CANCELADO":
                continue
            for s in _pedir(c, f"/api/checklists/{r['id']}")["stores"]:
                loja = lojas.setdefault((data, str(s["pes"]).zfill(3)), {
                    "lugar": f"{r['route_name']} / {s['store_name']}", "finalizado": r["status"] == "FINALIZADO", "itens": {}})
                for t in s.get("transactions") or []:
                    numero = str((t.get("carga") if t["transaction_type"] == "WMS" else t.get("transaction_code")) or "").strip()
                    if t["transaction_type"] in TIPOS and numero:
                        loja["itens"][(numero, t["transaction_type"])] = t.get("volume")
        # a lista vem da entrega mais nova para a mais velha: para quando passar da mais antiga que interessa
        if pagina >= lista["pagination"]["totalPages"] or all(_entrega(r["delivery_date"]) < min(dias) for r in lista["rows"]):
            return lojas
        pagina += 1


def comparar(checklist, sistema):
    """Diferenças, em texto, entre o checklist (ler_checklists) e o sistema ({(data, PES): {(número, coluna): volume}})."""
    difs = []
    for chave, loja in sorted(checklist.items()):
        data, pes = chave
        nosso = sistema.get(chave, {})
        onde = f"{data:%d/%m} {loja['lugar']} (PES {pes})"
        achados = set()
        for (numero, tipo), volume in sorted(loja["itens"].items()):
            col = next((col for col in TIPOS[tipo] if (numero, col) in nosso), None)
            if not col:
                outro = next((c for (n, c) in nosso if n == numero), None)
                difs.append(f"{onde}: {'/'.join(NOMES[col] for col in TIPOS[tipo])} {numero} só no checklist"
                            + (f" (no sistema está como {NOMES[outro]})" if outro else ""))
                continue
            achados.add((numero, col))
            if volume is not None and nosso[(numero, col)] is not None and volume != nosso[(numero, col)]:
                difs.append(f"{onde}: {NOMES[col]} {numero} com {volume} volume(s) no checklist e {nosso[(numero, col)]} no sistema")
        if loja["finalizado"]:
            for (numero, col) in sorted(set(nosso) - achados):
                if not any(n == numero for n, _ in loja["itens"]):
                    difs.append(f"{onde}: {NOMES[col]} {numero} só no sistema (checklist finalizado sem ele)")
    return difs


def documentos_do_sistema(dias):
    nosso = defaultdict(dict)
    for d in (Documento.objects.select_related("tipo", "parada__rota")
              .filter(parada__rota__data__in=dias, parada__rota__status=Rota.ATIVA, tipo__codigo__in=NOMES).exclude(numero="")):
        nosso[(d.parada.rota.data, d.parada.pes.zfill(3))][(d.numero, d.tipo.codigo)] = d.volumes
    return nosso


def conferir():
    """Uma rodada. Devolve quantas diferenças novas foram escritas no arquivo."""
    c = config()
    if not c:
        return 0
    hoje = timezone.localdate()
    dias = {hoje + timedelta(days=k) for k in range(-DIAS, DIAS + 1)}
    novas = [d for d in comparar(ler_checklists(c, dias), documentos_do_sistema(dias)) if d not in _avisados]
    if novas:
        with open(settings.DADOS / "conferencia_checklist.log", "a", encoding="utf-8") as f:
            agora = timezone.localtime().strftime("%d/%m/%Y %H:%M")
            f.writelines(f"{agora} {d}\n" for d in novas)
        _avisados.update(novas)
    return len(novas)


def iniciar():
    """Chamado pelo wsgi: uma thread por servidor. Olha as variáveis a cada rodada: liga sozinha depois do setx."""

    def laco():
        while True:
            try:
                conferir()
            except Exception:  # checklist fora do ar ou NetBird caído: tenta de novo na próxima rodada
                log.exception("Checklist: falha ao conferir")
            time.sleep(INTERVALO)

    threading.Thread(target=laco, name="checklist", daemon=True).start()
