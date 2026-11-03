"""Lança sozinho as cargas WMS novas da Alcis na loja certa, pelo PES.

O portal (PWA) da Alcis tem o relatório "Romaneio de Carga" (o mesmo do Gerador de Romaneios): pelo número da carga,
devolve os volumes com a loja (COD_CLIENTE = "100000" + PES). As cargas são sequenciais, então a cada rodada o sistema
confere os números logo depois da maior carga já lançada (e os poucos abaixo dela que ainda estavam vazios).
A carga entra na próxima rota da loja (hoje ou depois) que ainda está sem carga; cargas da mesma loja achadas juntas
vão para a mesma rota. Só aparece depois de ter volume embalado. Desligada sem ALCIS_USUARIO e ALCIS_SENHA.
"""
import csv
import html
import http.cookiejar
import io
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from django.contrib.auth.models import User
from django.utils import timezone

from . import acoes
from .models import Documento, Parada, Rota, TipoDocumento
from .regras import pes_da_pessoa

log = logging.getLogger(__name__)

BASE = "https://wms.exemplo.com.br/portal/"
TELA = ("conteudo/objetos/BuilderWeb/MinhasConsultas/BuilderLayoutGrid.aspx"
        "?IdSql=1966&?idSite=FLO&userWMS=JF&timer=999&lang=&userAWS=ALCIS&idObjeto=535&Telas=1")
CAMPO_LOGIN = "ucLogin$txtloginusuario"
INTERVALO = 15  # segundos entre rodadas (uma rodada leva uns 4 s no portal)
ADIANTE = 10  # números conferidos depois da maior carga lançada
ATRAS = 20  # números abaixo dela ainda não lançados (carga que estava vazia até embalarem)
# PES → cargas achadas quando a loja não tinha rota futura sem carga: entram sozinhas quando a rota for criada.
# Fica na memória; o servidor reinicia todo dia e acha de novo as que estão até ATRAS números da maior lançada.
_sem_rota = {}
_local = threading.local()  # um login do portal por thread: a conferência de pedidos baixa várias cargas ao mesmo tempo


def config():
    c = {k: os.environ.get("ALCIS_" + k, "").strip() for k in ("USUARIO", "SENHA")}
    return c if all(c.values()) else None


def ler_csv(corpo):
    """Linhas do CSV exportado pelo portal, como dicionários com o cabeçalho em maiúsculas."""
    try:
        texto = corpo.decode("utf-8")
    except UnicodeDecodeError:
        texto = corpo.decode("cp1252")
    texto = texto.lstrip("﻿")
    primeira = texto.split("\n", 1)[0]
    leitor = csv.reader(io.StringIO(texto), delimiter=";" if primeira.count(";") >= primeira.count(",") else ",")
    linhas = [l for l in leitor if any(c.strip() for c in l)]
    if not linhas:
        return []
    cab = [c.strip().upper() for c in linhas[0]]
    return [dict(zip(cab, (c.strip() for c in l))) for l in linhas[1:]]


# ---------- portal ----------

def _pedir(op, caminho, form=None):
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    with op.open(BASE + caminho, data, timeout=60) as r:
        return r.read(), r.headers.get("Content-Type", "")


def _ocultos(pagina):
    campos = {}
    for m in re.finditer(r'<input[^>]*type="hidden"[^>]*>', pagina, re.I):
        n, v = re.search(r'name="([^"]*)"', m.group(0)), re.search(r'value="([^"]*)"', m.group(0))
        if n:
            campos[html.unescape(n.group(1))] = html.unescape(v.group(1)) if v else ""
    return campos


def _login(c):
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (RotasExpedicao)")]
    f = _ocultos(_pedir(op, "default.aspx")[0].decode("utf-8", "replace"))
    f.update({CAMPO_LOGIN: c["USUARIO"], "ucLogin$txtloginsenha": c["SENHA"], "ucLogin$btnLogar": "Entrar"})
    if CAMPO_LOGIN in _pedir(op, "default.aspx", f)[0].decode("utf-8", "replace"):
        raise PermissionError("o portal da Alcis recusou o usuário ou a senha")
    return op


def baixar_carga(n):
    """Volumes da carga n; [] se ela ainda não existe ou não tem volume embalado. Só leitura (filtra e exporta)."""
    for _ in range(2):
        if getattr(_local, "sessao", None) is None:
            _local.sessao = _login(config())
        sessao = _local.sessao
        pagina = _pedir(sessao, TELA)[0].decode("utf-8", "replace")
        if CAMPO_LOGIN in pagina:  # sessão expirou: entra de novo
            _local.sessao = None
            continue
        f = _ocultos(pagina)
        f.update({"ASPxTextBox4": str(n), "ASPxButton3": "Filtrar"})
        f = _ocultos(_pedir(sessao, TELA, f)[0].decode("utf-8", "replace"))
        f.update({"ASPxTextBox4": str(n), "ImageButton1.x": "10", "ImageButton1.y": "10"})
        corpo, tipo = _pedir(sessao, TELA, f)
        if not tipo.lower().startswith("text/csv"):
            raise RuntimeError("o portal da Alcis não devolveu o CSV da carga (a tela pode ter mudado)")
        return ler_csv(corpo)
    raise PermissionError("o portal da Alcis pediu login de novo")


# ---------- sistema ----------

def _numeros(lancadas, esperando):
    if not lancadas:
        return []  # sem carga de referência, não sabe por onde começar
    maior = max(lancadas)
    return [n for n in range(maior - ATRAS, maior + ADIANTE + 1) if n > 0 and n not in lancadas and n not in esperando]


def _proxima_parada_sem_carga(pes):
    paradas = (Parada.objects.filter(pes=pes, rota__status=Rota.ATIVA, rota__data__gte=timezone.localdate())
               .select_related("rota").order_by("rota__data", "rota__ordem", "ordem"))
    return next((p for p in paradas if not p.documentos.filter(tipo__eh_carga=True).exclude(numero="").exists()), None)


def sincronizar():
    """Uma rodada. Devolve quantas cargas foram lançadas."""
    if not config():
        return 0
    lancadas = {int(n) for n in Documento.objects.filter(tipo__eh_carga=True).values_list("numero", flat=True) if n.isdigit()}
    esperando = {n for ns in _sem_rota.values() for n in ns}
    # PES → números das cargas novas, começando pelas que esperavam rota (sem as lançadas à mão enquanto isso)
    novas = {pes: [n for n in ns if n not in lancadas] for pes, ns in _sem_rota.items()}
    _sem_rota.clear()
    for n in _numeros(lancadas, esperando):
        lojas = {p for p in (pes_da_pessoa(v.get("COD_CLIENTE", "")) for v in baixar_carga(n)) if p}
        for pes in lojas:
            novas.setdefault(pes, []).append(n)
    novas = {pes: ns for pes, ns in novas.items() if ns}
    if not novas:
        return 0
    usuario, novo = User.objects.get_or_create(username="alcis", defaults={"first_name": "ALCIS"})
    if novo:
        usuario.set_unusable_password()
        usuario.save()
    tipo = TipoDocumento.objects.filter(eh_carga=True).first()
    feitas = 0
    for pes, numeros in novas.items():
        parada = _proxima_parada_sem_carga(pes)
        if not parada:
            _sem_rota[pes] = numeros
            if set(numeros) - esperando:
                log.warning("Alcis: carga(s) %s da loja PES %s sem rota futura sem carga; entra quando a rota for criada", numeros, pes)
            continue
        acoes.editar_documento(usuario, parada, tipo.codigo, " ".join(map(str, numeros)))
        feitas += len(numeros)
    return feitas


def ciclo():
    try:
        n = sincronizar()
        if n:
            log.warning("Alcis: %s carga(s) lançada(s)", n)
    except Exception:  # portal fora do ar, senha trocada, tela mudou: tenta de novo na próxima rodada
        log.exception("Alcis: falha ao buscar cargas")


def iniciar():
    """Chamado pelo wsgi: uma thread por servidor."""
    if not config():
        return

    def laco():
        while True:
            ciclo()
            time.sleep(INTERVALO)

    threading.Thread(target=laco, name="alcis", daemon=True).start()
