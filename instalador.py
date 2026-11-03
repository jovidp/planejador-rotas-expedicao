"""InstalarRotas.exe — instala ou atualiza o sistema de rotas nesta máquina e deixa o servidor rodando.

Sem argumentos (dois cliques): instala/atualiza em C:\\RotasExpedicao.
Atualização automática: o servidor olha a pasta de onde foi instalado a cada 30 s; um InstalarRotas.exe mais novo
lá é instalado sozinho (sem janela) e o servidor volta na versão nova.
A cópia instalada se chama RotasServidor.exe e, aberta, só roda o servidor (é o que a inicialização do Windows chama).
Gerado por gerar_instalador.bat (PyInstaller); a máquina de destino não precisa de Python.
"""
import argparse
import getpass
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

NOME_SERVIDOR = "RotasServidor.exe"
NOME_INSTALADOR = "InstalarRotas.exe"
ARQ_PASTA = "pasta_atualizacao.txt"
ARQ_TENTATIVA = "ultima_atualizacao.txt"
INTERVALO = 30
DESTINO_PADRAO = Path(os.environ.get("SystemDrive", "C:") + "\\") / "RotasExpedicao"
REGRA_FIREWALL = "RotasExpedicao"
PORTA_PADRAO = 8000


def modo_padrao(exe):
    return "servir" if Path(exe).name.lower() == NOME_SERVIDOR.lower() else "instalar"


def script_inicializacao(exe, porta):
    """VBScript da pasta Inicializar: sobe o servidor sem janela quando o usuário entra no Windows."""
    return f'CreateObject("WScript.Shell").Run """{exe}"" servir --porta {porta}", 0, False\r\n'


def backup_banco(dados):
    db = Path(dados) / "db.sqlite3"
    if not db.exists():
        return None
    pasta = Path(dados) / "backups"
    pasta.mkdir(exist_ok=True)
    alvo = pasta / f"db-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
    shutil.copy2(db, alvo)
    return alvo


def guardar_pasta_atualizacao(dados, pasta):
    (Path(dados) / ARQ_PASTA).write_text(str(pasta), encoding="utf-8")


def pasta_atualizacao(dados):
    arq = Path(dados) / ARQ_PASTA
    return Path(arq.read_text(encoding="utf-8").strip()) if arq.exists() else None


def atualizacao_disponivel(pasta, instalado):
    """O InstalarRotas.exe da pasta, se for mais novo que o programa instalado (a cópia mantém a data do original)
    e já tiver terminado de ser copiado (sem mudança há 10 s)."""
    novo = Path(pasta) / NOME_INSTALADOR
    try:
        m_novo, m_inst = novo.stat().st_mtime, Path(instalado).stat().st_mtime
    except OSError:
        return None
    return novo if m_novo > m_inst + 1 and time.time() - m_novo > 10 else None


def assinatura(arq):
    """Data e tamanho: o arquivo só é instalado quando fica igual em duas olhadas seguidas (cópia terminou)."""
    try:
        s = Path(arq).stat()
    except OSError:
        return None
    return (s.st_mtime, s.st_size)


def copiar_para_instalar(novo, dados):
    """Roda uma cópia local: o arquivo da pasta compartilhada não fica preso e pode ser trocado a qualquer hora."""
    pasta = Path(dados) / "atualizacao"
    pasta.mkdir(parents=True, exist_ok=True)
    return Path(shutil.copy2(novo, pasta / NOME_INSTALADOR))


def preparar_django(dados):
    os.environ["ROTAS_DADOS"] = str(dados)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()


def porta_aberta(porta):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", porta)) == 0


def pids_da_instalacao(lista, alvo):
    """Das linhas 'pid|caminho', os processos que rodam este arquivo (o Windows mistura caminho curto e longo)."""
    pids = []
    for linha in lista.splitlines():
        pid, _, caminho = linha.strip().partition("|")
        try:
            if caminho and os.path.samefile(caminho, alvo):
                pids.append(pid)
        except OSError:
            pass
    return pids


def parar_servidor(destino):
    """Para só o servidor desta pasta: outra instalação na mesma máquina continua no ar."""
    exe = Path(destino) / NOME_SERVIDOR
    if exe.exists():
        lista = subprocess.run(["powershell", "-NoProfile", "-Command",
                                f"Get-CimInstance Win32_Process -Filter \"Name='{NOME_SERVIDOR}'\" | ForEach-Object {{ \"$($_.ProcessId)|$($_.ExecutablePath)\" }}"],
                               capture_output=True, text=True).stdout
        for pid in pids_da_instalacao(lista, exe):
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    for _ in range(20):  # espera o Windows soltar o arquivo antes de substituir
        try:
            if exe.exists():
                open(exe, "ab").close()  # o Windows recusa enquanto algum processo está rodando o arquivo
            return
        except OSError:
            time.sleep(0.5)


def criar_admin():
    from django.contrib.auth.models import User
    if User.objects.filter(is_superuser=True).exists():
        return
    print("\nCrie o usuário administrador (ele cadastra os outros usuários depois).")
    usuario = os.environ.get("ROTAS_ADMIN", "")
    while not usuario:
        usuario = input("  Usuário: ").strip()
    senha = os.environ.get("ROTAS_SENHA", "")
    while not senha:
        s1, s2 = getpass.getpass("  Senha: "), getpass.getpass("  Repita a senha: ")
        if s1 and s1 == s2:
            senha = s1
        else:
            print("  As senhas não conferem. Tente de novo.")
    User.objects.create_superuser(usuario, "", senha)
    print(f"  Administrador {usuario} criado.")


def liberar_firewall(porta):
    """Libera a porta para as outras máquinas da rede. Pede permissão de administrador (UAC) uma vez."""
    if subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={REGRA_FIREWALL}"], capture_output=True).returncode == 0:
        return True
    regra = f"advfirewall firewall add rule name={REGRA_FIREWALL} dir=in action=allow protocol=TCP localport={porta}"
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f'Start-Process netsh -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "{regra}"'], capture_output=True)
    return subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={REGRA_FIREWALL}"], capture_output=True).returncode == 0


def desligado(args, cwd):
    """Roda um processo separado desta janela/processo, para ele continuar depois que este terminar."""
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:  # se este processo estiver num "job" do Windows, sai dele; senão o filho morre junto
        subprocess.Popen([str(a) for a in args], cwd=cwd, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        subprocess.Popen([str(a) for a in args], cwd=cwd, creationflags=flags)


def iniciar_servidor(exe, destino, porta):
    desligado([exe, "servir", "--porta", porta], destino)


def instalar(destino, porta, firewall=True, inicializacao=True, silencioso=False, pasta_atual=None):
    print("=== Sistema de Rotas · Expedição ADM — instalação ===\n")
    destino.mkdir(parents=True, exist_ok=True)
    parar_servidor(destino)
    b = backup_banco(destino)
    print(f"Backup do banco: {b}" if b else "Primeira instalação: banco novo.")

    exe = destino / NOME_SERVIDOR
    if Path(sys.executable).resolve() != exe.resolve():
        shutil.copy2(sys.executable, exe)
        guardar_pasta_atualizacao(destino, pasta_atual or Path(sys.executable).resolve().parent)
    print(f"Programa copiado para {exe}")

    preparar_django(destino)
    from django.core.management import call_command
    call_command("migrate", verbosity=0)
    print("Banco de dados atualizado.")
    from rotas.models import Loja
    if not Loja.objects.exists():
        call_command("importar_cadastro")
    criar_admin()

    if inicializacao:
        vbs = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/RotasExpedicao.vbs"
        vbs.write_text(script_inicializacao(exe, porta), encoding="ascii")
        print("O sistema vai subir sozinho quando este usuário entrar no Windows.")
    if firewall:
        print("Liberando a porta no firewall (o Windows pode pedir permissão)…")
        print(f"  Porta {porta} liberada." if liberar_firewall(porta) else
              f"  Não foi possível liberar a porta {porta}. As outras máquinas não vão acessar até a TI liberar.")

    iniciar_servidor(exe, destino, porta)
    for _ in range(60):
        if porta_aberta(porta):
            break
        time.sleep(0.5)
    else:
        print(f"\nO servidor não respondeu. Veja {destino / 'servidor.log'}.")
        return 1

    nome = socket.gethostname()
    print(f"\nPronto! Nesta máquina: http://localhost:{porta}")
    print(f"Nas outras máquinas da rede: http://{nome}:{porta}")
    print(f"Atualização automática: coloque um {NOME_INSTALADOR} novo em {pasta_atualizacao(destino)}")
    if not silencioso:
        webbrowser.open(f"http://localhost:{porta}")
        fechar_sozinho(15)
    return 0


def vigiar_atualizacao(dados, porta):
    """Roda junto do servidor: achou um instalador mais novo na pasta de atualização, instala sem janela."""
    pasta = pasta_atualizacao(dados)
    if not pasta:
        return
    tentativa = Path(dados) / ARQ_TENTATIVA
    anterior = None
    while True:
        time.sleep(INTERVALO)
        novo = atualizacao_disponivel(pasta, Path(sys.executable))
        agora = assinatura(novo) if novo else None
        if agora is None or agora != anterior:
            anterior = agora  # ainda copiando (ou acabou de aparecer): confere na próxima olhada
            continue
        marca = str(agora)
        if tentativa.exists() and tentativa.read_text() == marca:
            continue  # este mesmo arquivo já falhou uma vez: não fica derrubando o servidor
        try:
            copia = copiar_para_instalar(novo, dados)
            tentativa.write_text(marca)
            print(f"{datetime.now():%d/%m/%Y %H:%M:%S} atualização encontrada em {novo}; instalando")
            desligado([copia, "--destino", dados, "--porta", porta, "--silencioso", "--sem-firewall", "--sem-inicializacao", "--pasta-atualizacao", pasta], dados)
            return
        except OSError as e:  # arquivo preso ou sem acesso: tenta de novo na próxima olhada
            print(f"{datetime.now():%d/%m/%Y %H:%M:%S} não consegui preparar a atualização ({e}); tento de novo")


def fechar_sozinho(segundos):
    """A janela não pode ficar aberta: enquanto ela existe, o Windows não deixa trocar o InstalarRotas.exe da pasta
    (e a atualização automática não acontece)."""
    import msvcrt
    print(f"\nPronto. Esta janela fecha sozinha em {segundos} segundos (ou aperte qualquer tecla). O sistema continua rodando.")
    fim = time.time() + segundos
    while time.time() < fim and not msvcrt.kbhit():
        time.sleep(0.2)


def servir(dados, porta):
    log = open(Path(dados) / "servidor.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log  # roda sem janela: mensagens e erros vão para o log
    print(f"{datetime.now():%d/%m/%Y %H:%M:%S} servidor iniciando na porta {porta}")
    import threading
    threading.Thread(target=vigiar_atualizacao, args=(dados, porta), daemon=True).start()
    preparar_django(dados)
    from waitress import serve
    from config.wsgi import application
    serve(application, listen=f"*:{porta}", threads=8)


def main(argv=None):
    exe = Path(sys.executable)
    p = argparse.ArgumentParser(description="Instala ou roda o sistema de rotas.")
    p.add_argument("modo", nargs="?", choices=["instalar", "servir"], default=modo_padrao(exe))
    p.add_argument("--destino", type=Path, default=DESTINO_PADRAO)
    p.add_argument("--porta", type=int, default=PORTA_PADRAO)
    p.add_argument("--sem-firewall", action="store_true")
    p.add_argument("--sem-inicializacao", action="store_true")
    p.add_argument("--silencioso", action="store_true", help="não abre o navegador nem espera Enter")
    p.add_argument("--pasta-atualizacao", type=Path, help="pasta vigiada para atualizar (padrão: a pasta deste instalador)")
    a = p.parse_args(argv)
    if a.modo == "servir":
        return servir(exe.parent if getattr(sys, "frozen", False) else a.destino, a.porta)
    if a.silencioso and getattr(sys, "frozen", False):  # atualização automática roda sem janela: guarda o que aconteceu
        a.destino.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(a.destino / "instalacao.log", "a", encoding="utf-8", buffering=1)
        print(f"\n--- {datetime.now():%d/%m/%Y %H:%M:%S} instalação silenciosa de {exe}")
    try:
        return instalar(a.destino, a.porta, not a.sem_firewall, not a.sem_inicializacao, a.silencioso, a.pasta_atualizacao)
    except Exception as e:  # a janela não pode sumir sem a pessoa ver o erro
        print(f"\nERRO na instalação: {e}\nO banco anterior está em {a.destino / 'backups'} — nada foi apagado.")
        if (a.destino / NOME_SERVIDOR).exists() and not porta_aberta(a.porta):
            iniciar_servidor(a.destino / NOME_SERVIDOR, a.destino, a.porta)  # o sistema não fica fora do ar
        if not a.silencioso:
            input("Pressione Enter para fechar.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
