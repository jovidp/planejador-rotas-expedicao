"""Reinicia o servidor quando o código muda; o iniciar.bat roda em laço e sobe de novo com o banco atualizado.

Só liga com ROTAS_RECARREGAR=1 (o iniciar.bat põe). Antes de sair confere o código novo com "manage.py check":
com erro, o servidor continua na versão anterior.
"""
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PASTAS = [BASE / "rotas", BASE / "config"]
EXTENSOES = {".py", ".html", ".js", ".css", ".json"}
INTERVALO = 5  # segundos entre conferências
QUIETO = 10  # segundos sem mudança antes de reiniciar: espera terminar de salvar vários arquivos
log = logging.getLogger(__name__)


def assinatura():
    return {str(a): a.stat().st_mtime for p in PASTAS for a in p.rglob("*")
            if a.suffix in EXTENSOES and "__pycache__" not in a.parts and "tests" not in a.parts}


def erro_no_codigo():
    """'manage.py check' (Python) e, com o Node instalado, a sintaxe de cada .js da tela. Devolve o erro ou ''."""
    r = subprocess.run([sys.executable, str(BASE / "manage.py"), "check"], capture_output=True, text=True, cwd=BASE)
    if r.returncode:
        return r.stderr or r.stdout
    node = shutil.which("node")
    for js in sorted((BASE / "rotas" / "static" / "rotas").glob("*.js")) if node else []:
        r = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
        if r.returncode:
            return r.stderr or r.stdout
    return ""


def reiniciar(sair=os._exit):
    erro = erro_no_codigo()
    if erro:
        log.error("Código novo com erro; o servidor continua na versão anterior:\n%s", erro[-2000:])
        return False
    log.warning("Código novo: reiniciando o servidor")
    sair(3)  # o iniciar.bat atualiza o banco e sobe de novo
    return True


def vigiar():
    antes = assinatura()
    while True:
        time.sleep(INTERVALO)
        agora = assinatura()
        if agora == antes:
            continue
        while True:  # espera parar de mudar
            time.sleep(QUIETO)
            depois = assinatura()
            if depois == agora:
                break
            agora = depois
        reiniciar()
        antes = agora  # código com erro: só tenta de novo na próxima mudança


def iniciar():
    if os.environ.get("ROTAS_RECARREGAR") == "1":
        threading.Thread(target=vigiar, name="recarregar", daemon=True).start()
