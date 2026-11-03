"""CT-e da retirada no SSW pelo navegador (Playwright), um por loja, pelo botão da página de retiradas (só os DONOS).

Caminho mapeado com o usuário em 27/10/2026 (BÚZIOS e BÚZIOS II da R. LAGOS, CTRC WLT 000101-1 e 000102-0):
004 tipo N → placa da rota → chave da 1ª nota → "Agrupam" com todas as notas da loja e o peso de cada uma
(RT × 15 kg, CX × 3,5 kg) → Frete 2 → pagador = CD → frete informado = frete de retorno da loja → ► (resumo)
→ "1. Gravar". Depois, na 007: "Enviar à SEFAZ / meus" e, autorizado, "Imprimir / meus" (DACTE em PDF, guardado na
loja e copiado para Downloads). Travas: nota já digitada em outro CTRC não emite; o que o SSW mostra tem de bater
com as notas antes de gravar. O "meus" leva o que mais houver no login (o mesmo de quem digita à mão), como no SSW.
Loja na cidade do Rio: o SSW gera RPS em vez de CT-e; envio à Prefeitura e impressão ficam na 009 (TELAS).

Ida (página /ctes/, todos os usuários, 29/10/2026): por loja, um CT-e com as NFs da 434/3386 (remetente = CD,
empresa 209) e outro com as da sacola (empresa 1, outro CNPJ); os dois com o frete de envio da loja e peso = volumes ×
15 kg. Na ida só vão agrupamento, peso e frete: Frete e CNPJs ficam como o SSW traz (usuário, 29/10). Cada um entra no SSW com o login de quem pediu (LoginSSW).
"""
import asyncio
import logging
import queue
import re
import shutil
import threading
import time
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone

from . import retiradas
from .checklist import _variavel
from types import SimpleNamespace

from . import totvs
from .models import LoginSSW, Parada
from .windows import abrir

log = logging.getLogger(__name__)

CD_CNPJ = "11222333000181"  # destinatário e pagador
PESO_RT, PESO_CX = Decimal("15"), Decimal("3.5")  # kg por volume (caixa com mercadoria / caixa vazia)
NA_FILA, EMITINDO, GRAVANDO = "Na fila", "Emitindo no SSW", "Gravando no SSW"
ESPERA_SEFAZ = 180  # segundos esperando a autorização antes de desistir de imprimir
_fila = queue.Queue()  # lojas esperando o CT-e (não reutilizar o nome: em 27/10 uma função _fila apagou a fila)


class JaDigitada(Exception):
    """O SSW avisou que a nota já está em outro CTRC: a loja já tem CT-e."""


TIPOS = {"retorno": "cte", "ida": "ida", "sacola": "sacola"}  # tipo de CT-e → prefixo dos campos da parada
CODIGOS = {"ida": ["t434", "t3386"], "sacola": ["sacola"]}  # colunas da rota com as transações de cada CT-e da ida (+ carga WMS na ida)


def config():
    """Endereço e domínio do SSW (os mesmos para todos); o login é de cada pessoa (login)."""
    c = {k: _variavel("SSW_" + k) for k in ("URL", "DOMINIO")}
    return c if all(c.values()) else None


def login(username):
    lg = LoginSSW.objects.filter(usuario__username=username).first()
    return {"CPF": lg.cpf, "USUARIO": lg.login, "SENHA": abrir(lg.senha)} if lg else None


def campo(p, tipo, nome):
    return getattr(p, f"{TIPOS[tipo]}_{nome}")


def documentos(p, tipo):
    """Transações da parada que vão no CT-e da ida (434/3386 e carga WMS, usuário 29/10) ou da sacola.
    Cancelada ou recusada na TOTVS não tem NF: nem aparece (usuário, 29/10)."""
    return [d for d in p.documentos.all() if (d.tipo.codigo in CODIGOS[tipo] or (tipo == "ida" and d.tipo.eh_carga)) and d.numero
            and d.situacao_totvs not in totvs.IGNORAR]


def atendida(d):
    """Pronta para o CT-e: transação Atendida na TOTVS; carga WMS com todos os pedidos atendidos no 099 (rotas/pedidos.py)."""
    return d.pedidos_fase == "atendida" if d.tipo.eh_carga else d.situacao_totvs == totvs.SITUACOES["Attended"]


def peso(n):
    return (n.volumes or 0) * (PESO_CX if n.operacao in retiradas.CX else PESO_RT)


def br(v, casas):
    return f"{v:.{casas}f}".replace(".", ",")


def numero(s):
    """'1.967,11' → Decimal('1967.11')."""
    return Decimal((s or "0").strip().replace(".", "").replace(",", "."))


def arquivo(p, tipo="retorno"):
    return settings.DADOS / "retiradas" / (f"cte-{p.pk}.pdf" if tipo == "retorno" else f"cte-{tipo}-{p.pk}.pdf")


def problema(p, notas, tipo="retorno"):
    """O que impede fazer o CT-e da loja agora (None = pode). Na ida, notas = as transações (documentos)."""
    if campo(p, tipo, "ctrc"):
        return "já tem CT-e"
    if campo(p, tipo, "situacao").startswith((NA_FILA, EMITINDO, GRAVANDO)):
        return "já está sendo feito"
    if not config():
        return "SSW não configurado"
    if not notas:
        return "sem notas"
    if tipo != "retorno" and not all(atendida(d) for d in notas):
        return "transação não atendida"
    if any(not n.volumes for n in notas):
        return "nota sem volumes"
    if not p.rota.veiculo:
        return "rota sem placa"
    if not (p.frete_retorno if tipo == "retorno" else p.frete_envio):
        return "loja sem frete"
    return None


def pedir(p, user, tipo="retorno"):
    """Põe a loja na fila, no nome de quem pediu. Devolve o motivo se não puder."""
    if not LoginSSW.objects.filter(usuario=user).exists():
        return "salve o seu login do SSW primeiro"
    if erro := problema(p, list(p.notas_retirada.all()) if tipo == "retorno" else documentos(p, tipo), tipo):
        return erro
    _situacao(p.pk, NA_FILA, tipo, por=user.username)
    _fila.put((p.pk, tipo))
    return None


def _no_banco(fn):
    """Com o Playwright aberto há um loop assíncrono na thread e o Django recusa o banco ali
    (SynchronousOnlyOperation, 27/10 na ARRAIAL DO CABO): nesse caso roda numa thread à parte."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return fn()
    saida = {}

    def rodar():
        try:
            saida["v"] = fn()
        except Exception as e:
            saida["e"] = e
        finally:
            connection.close()

    t = threading.Thread(target=rodar)
    t.start()
    t.join()
    if "e" in saida:
        raise saida["e"]
    return saida.get("v")


def _situacao(pk, texto, tipo="retorno", **campos):
    """campos: ctrc, por (sem o prefixo do tipo)."""
    x = TIPOS[tipo]
    _no_banco(lambda: Parada.objects.filter(pk=pk).update(**{f"{x}_situacao": texto[:200], f"{x}_em": timezone.now()},
                                                          **{f"{x}_{k}": v for k, v in campos.items()}))


def contagem(texto):
    """Números da 007 (CT-e) ou da 009 (RPS): "Digitados: | 1 | Enviar..."; campo vazio = 0."""
    texto = texto.replace("\xa0", " ")  # o SSW separa as palavras dos rótulos com &nbsp;

    def n(rotulo):
        m = re.search(re.escape(rotulo) + r"\s*\|\s*(\d+)", texto)
        return int(m[1]) if m else 0
    return {"digitados": n("Digitados:"), "enviados": n("Enviados à SEFAZ:") + n("Enviados a prefeitura:"),
            "autorizados": n("Autorizados (sem impressão):"), "rejeitados": n("Rejeitados:"), "denegados": n("Denegados:")}


# Loja na cidade do Rio (origem e destino no mesmo município): o SSW gera RPS/NFS-e e não CT-e. O 004 é igual;
# envio e impressão ficam na 009, com os mesmos "meus" (usuário, 27/10).
TELAS = {
    "CT-e": {"opcao": "007", "destino": "SEFAZ", "enviar": ("a[id='5']", "enviaReceita('m')"), "imprimir": ("#link_imp_meus_sem_imp_2", "'MIM'")},
    "RPS": {"opcao": "009", "destino": "Prefeitura", "enviar": ("#link_env_meus_1", "'ENV_MIM'"), "imprimir": ("#lnk_imp_meus_1", "'IMP_MIM'")},
}


def documento(municipio_remetente):
    return "RPS" if municipio_remetente.replace("\xa0", " ").strip().upper().startswith("RIO DE JANEIRO") else "CT-e"


def ctrc_gravado(texto):
    m = re.search(r"CTRC\s+anterior:\s*\|?\s*(\d{6}-\d)", texto)
    return m[1] if m else None


def _texto(pg):
    # o SSW separa as palavras dos rótulos com &nbsp; ("CTRC&nbsp;anterior:"): sem trocar, nada casava (27/10, STA. CRUZ DA SERRA)
    texto = pg.inner_text("body").replace("\xa0", " ")
    return " | ".join(t.strip() for t in texto.splitlines() if t.strip())


def _pronta(pg):
    """Toda tela do SSW leva o cursor ao seu primeiro campo um pouco depois de carregar e seleciona o que tem nele: o que
    for digitado antes some (27/10 a senha caiu no Usuário; 28/10 a placa BRA2E19 virou "2" no 004 da NITERÓI IV)."""
    from playwright.sync_api import TimeoutError

    pg.wait_for_load_state()
    try:
        pg.wait_for_function("() => document.activeElement && document.activeElement.id", timeout=10000)
    except TimeoutError:  # tela sem campo inicial: nada vai mudar o cursor depois
        pass


def _entrar(ctx, c):
    pg = ctx.new_page()
    pg.goto(c["URL"].rstrip("/") + "/bin/ssw0422")
    _pronta(pg)
    campos = ("DOMINIO", "CPF", "USUARIO", "SENHA")
    for i, k in enumerate(campos, 1):
        pg.fill(f"[id='{i}']", c[k])
    if [pg.input_value(f"[id='{i}']") for i in range(1, 5)] != [c[k] for k in campos]:
        raise ValueError("o login do SSW não ficou com os dados certos; não entrei")  # não manda ao SSW um login trocado
    pg.click("[id='5']")
    pg.wait_for_url("**/menu01*", timeout=30000)
    pg.wait_for_selector("[id='3']", timeout=30000)  # campo "Opção"
    _pronta(pg)
    return pg


def _abrir(ctx, menu, opcao):
    """Opção do menu principal; o SSW abre cada opção numa janela nova."""
    menu.fill("[id='3']", opcao)
    with ctx.expect_page(timeout=30000) as nova:
        menu.press("[id='3']", "Enter")
    _pronta(nova.value)
    return nova.value


def _gravar(ctx, menu, p, notas, foto, frete=None, pagador=CD_CNPJ, f16="2", doc=None):
    """Preenche o 004 e grava. Devolve (número do CTRC, "CT-e" ou "RPS", volumes trocados pelos da TOTVS).
    Retorno: Frete 2, pagador = CD, frete de retorno. Ida: f16 e pagador None (ficam como o SSW traz) e
    doc já sabido pelas cidades da NF (o remetente é o CD, no Rio: pela cidade dele toda ida daria RPS)."""
    frete = p.frete_retorno if frete is None else frete
    tipo = _abrir(ctx, menu, "004")
    tipo.wait_for_selector("#link_doc_n", timeout=30000)
    with ctx.expect_page(timeout=30000) as nova:
        tipo.click("#link_doc_n")
    x = nova.value
    x.wait_for_selector("[id='13']", timeout=30000)
    _pronta(x)
    placa = p.rota.veiculo.strip().upper()
    for _ in range(3):
        x.click("[id='13']")
        x.keyboard.press("Control+A")
        x.keyboard.type(placa)
        if x.input_value("[id='13']").strip().upper() == placa:
            break
    else:
        raise ValueError(f"o SSW ficou com a placa {x.input_value('[id=13]')!r} em vez de {placa}")
    x.keyboard.press("Tab")
    x.wait_for_function("() => document.activeElement && document.activeElement.name === 'chaveAcesso'", timeout=30000)
    x.keyboard.type(notas[0].chave, delay=15)  # colar de uma vez vira número científico no campo
    x.keyboard.press("Tab")
    x.wait_for_function("() => (document.getElementById('agrupamento') || {}).value", timeout=30000)
    x.wait_for_timeout(4000)  # o SSW ainda termina de carregar a nota: clicar antes não abre o agrupamento
    doc = doc or documento(x.input_value("#msgcadcepr"))  # município do remetente, ex.: "NOVA IGUACU / RJ"

    with ctx.expect_page(timeout=30000) as nova:
        x.get_by_text("Agrupam", exact=True).click()
    g = nova.value
    g.wait_for_selector("[id='13']", timeout=30000)
    _pronta(g)
    trocas = []
    linha = 13  # cada nota ocupa 14 ids: chave, série, número, volumes (+3), peso (+4), valor (+5)
    for i, n in enumerate(notas):
        if i:
            g.click(f"[id='{linha + 5}']")
            g.keyboard.press("Tab")  # sair da última coluna cria a linha seguinte
            linha += 14
            g.wait_for_function(f"() => document.activeElement && document.activeElement.id === '{linha}'", timeout=15000)
            g.wait_for_timeout(1500)  # digitar antes de o campo ficar pronto corta a chave
            g.keyboard.type(n.chave)
            g.keyboard.press("Tab")
            g.wait_for_function(f"() => (document.getElementById('{linha + 3}') || {{}}).value", timeout=30000)
        lida, num = g.input_value(f"[id='{linha}']").strip(), g.input_value(f"[id='{linha + 2}']").strip()
        if lida != n.chave or int(num or 0) != n.numero:
            raise ValueError(f"o SSW ficou com a chave {lida} / NF {num} na linha da NF {n.numero}")
        vol = g.input_value(f"[id='{linha + 3}']").strip()
        if vol != str(n.volumes):  # a NF-e diz outro número (28/10, VALENÇA NF 13630: 50 na nota, 1 na TOTVS e no RT): vale o da TOTVS (usuário)
            g.fill(f"[id='{linha + 3}']", str(n.volumes))
            trocas.append(f"NF {n.numero} com {n.volumes} vol da TOTVS (a nota diz {vol})")
        g.fill(f"[id='{linha + 4}']", br(peso(n), 3))
    g.click("[id='5']")  # ► do agrupamento: fecha a janela, ou avisa nota já digitada
    try:
        g.wait_for_event("close", timeout=20000)
    except Exception:
        texto = _texto(g)
        if "foram digitadas em outro CTRC" in texto:
            m = re.search(r"CTRC\s*\|?\s*([A-Z]{3}\s*\d{6}-\d)", texto.split("outro CTRC")[1])
            raise JaDigitada(m[1] if m else "?")
        raise

    if f16:
        x.fill("input[name=f16]", f16)
    if pagador:
        x.fill("#id_cli_pag_cnpj", pagador)
        x.press("#id_cli_pag_cnpj", "Tab")
        x.wait_for_function(f"() => document.getElementById('id_cli_pag_nome').value && "
                            f"document.getElementById('id_cli_pag_cnpj').value === '{pagador}'", timeout=30000)
    x.click("#lnk_frt_inf")
    x.fill("#id_frt_inf_frete_valor", br(frete, 2))
    x.press("#id_frt_inf_frete_valor", "Tab")
    x.click("#lnk_frt_inf_env")  # ► do painel de frete: só fecha o painel
    x.wait_for_timeout(1000)

    vol, kg = sum(n.volumes for n in notas), sum(peso(n) for n in notas)
    lido = (int(x.input_value("#id_qtde_vol") or 0), numero(x.input_value("#id_peso_real")), numero(x.input_value("#id_frt_inf_total_externo")),
            x.input_value("input[name=f16]"), x.input_value("#id_cli_pag_cnpj"))
    esperado = (vol, kg, frete, f16 or lido[3], pagador or lido[4])
    if lido != esperado:
        foto(x, "conferencia")
        raise ValueError(f"o SSW não bate com as notas: {lido} ≠ {esperado}")

    x.click("#lnk_env")  # ► final: calcula e mostra o resumo, ainda sem gravar
    op = x.locator("a.dialog[ak='1']")
    op.wait_for(timeout=30000)
    foto(x, "resumo")
    if op.inner_text().replace("\xa0", " ").strip() != "1. Gravar":
        raise ValueError(f"opção 1 do resumo não é Gravar: {op.inner_text()!r}")
    antes = ctrc_gravado(_texto(x)) or ""
    _situacao(p.pk, GRAVANDO)
    op.click()
    # o número novo aparece em "CTRC anterior" quando o formulário volta vazio; o wait_for_function do navegador
    # não o enxergou (27/10, ARRAIAL DO CABO gravou o 011455-3 e deu timeout), o texto lido daqui enxerga
    fim = time.time() + 120
    while time.time() < fim:
        x.wait_for_timeout(2000)
        novo = ctrc_gravado(_texto(x))
        if novo and novo != antes:
            return novo, doc, trocas
    foto(x, "depois_de_gravar")
    raise ValueError("cliquei em Gravar e o número do CTRC não apareceu em 2 min")


def _tela(ctx, menu, doc):
    """Abre a 007 (CT-e) ou a 009 (RPS) e lê as contagens."""
    t = TELAS[doc]
    s = _abrir(ctx, menu, t["opcao"])
    s.wait_for_selector(t["imprimir"][0], state="attached", timeout=30000)
    return s, contagem(_texto(s))


def _enviar_e_imprimir(ctx, menu, p, ctrc, doc="CT-e", tipo="retorno"):
    """Como o usuário faz (27/10): "meus" em Digitados e, autorizado, "meus" em Autorizados (007 no CT-e, 009 no RPS).
    O "meus" leva junto o que mais estiver no login (digitado à mão também), como quando ele aperta."""
    t = TELAS[doc]
    s, n = _tela(ctx, menu, doc)
    if n["digitados"]:
        meus = s.locator(t["enviar"][0])
        if meus.inner_text().strip() != "meus" or t["enviar"][1] not in (meus.get_attribute("onclick") or ""):
            return f"CTRC {ctrc} ({doc}) gravado; a {t['opcao']} mudou e não achei o 'Enviar / meus' (enviar pela {t['opcao']})"
        meus.click()
        s.wait_for_timeout(5000)
    s.close()
    return _imprimir(ctx, menu, p, ctrc, doc, tipo)


def _imprimir(ctx, menu, p, ctrc, doc="CT-e", tipo="retorno"):
    """Espera a autorização e aperta "Imprimir / meus"; o PDF fica na loja e na pasta Downloads, como no navegador."""
    t = TELAS[doc]
    fim = time.time() + ESPERA_SEFAZ
    while True:
        s, n = _tela(ctx, menu, doc)
        if n["rejeitados"] or n["denegados"]:
            return f"CTRC {ctrc} ({doc}) enviado, mas há rejeitado/denegado na {t['opcao']}: conferir"
        # logo depois do envio ele some de todas as contagens por uns segundos: espera aparecer autorizado e nada mais enviado
        if (n["autorizados"] and not n["enviados"]) or time.time() > fim:
            break
        s.close()
        time.sleep(10)
    if not n["autorizados"]:
        return f"CTRC {ctrc} ({doc}) enviado à {t['destino']}, sem autorização em {ESPERA_SEFAZ // 60} min (conferir e imprimir pela {t['opcao']})"
    meus = s.locator(t["imprimir"][0])
    if t["imprimir"][1] not in (meus.get_attribute("onclick") or ""):
        return f"CTRC {ctrc} ({doc}) autorizado; a {t['opcao']} mudou e não achei o 'Imprimir / meus' (imprimir pela {t['opcao']})"
    try:
        with s.expect_download(timeout=60000) as baixado:
            meus.click()
        destino = arquivo(p, tipo)
        destino.parent.mkdir(parents=True, exist_ok=True)
        baixado.value.save_as(destino)
    except Exception:  # já aconteceu de o DACTE ir para o navegador de quem estava com o SSW aberto no mesmo login
        log.exception("SSW: PDF do CTRC %s não veio para o sistema", ctrc)
        return f"CTRC {ctrc} ({doc}) autorizado e impresso; o PDF não veio para o sistema (reimprimir pela {t['opcao']})"
    try:
        carimbar(destino, p, ctrc)
    except Exception:  # o DACTE sem a loja ainda serve: o PDF não se perde por causa do carimbo
        log.exception("SSW: não deu para escrever a loja no DACTE do CTRC %s", ctrc)
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():  # o servidor roda nesta máquina com o usuário do João: o PDF cai onde cairia ao apertar no SSW
        shutil.copyfile(destino, downloads / baixado.value.suggested_filename)
    return (f"CTRC {ctrc} ({doc}) autorizado"
            + (f" (o PDF tem {n['autorizados']} documentos: tinha outro na fila)" if n["autorizados"] > 1 else ""))


def carimbar(pdf, p, ctrc):
    """Escreve a loja centralizada na margem de cima de cada via desse CTRC, logo acima do cabeçalho. Via de outro
    CT-e (o "Imprimir / meus" pega tudo o que estava autorizado) fica como está. Devolve quantas vias carimbou."""
    import io

    from fpdf import FPDF
    from pypdf import PdfReader, PdfWriter

    nome = p.loja.upper().encode("latin-1", "replace").decode("latin-1")  # fonte padrão do PDF
    saida, total = PdfWriter(clone_from=str(pdf)), 0
    for pagina in saida.pages:
        textos = []
        pagina.extract_text(visitor_text=lambda s, cm, tm, *_: textos.append((cm[3] * tm[5] + cm[5], s)))
        titulos = sorted((y for y, s in textos if s.replace(" ", "").strip() == "DACTE"), reverse=True)
        # cada via vai do seu título até o título da próxima
        vias = [y for y, abaixo in zip(titulos, titulos[1:] + [0]) if any(ctrc in s for yy, s in textos if abaixo < yy <= y + 20)]
        if not vias:
            continue
        largura, altura = float(pagina.mediabox.width), float(pagina.mediabox.height)
        f = FPDF(unit="pt", format=(largura, altura))
        f.set_auto_page_break(False)
        f.add_page()
        f.set_font("helvetica", "B", 8)
        for y in vias:
            # medido no DACTE de 27/10: o cabeçalho começa 12 pt acima do título; na 2ª via o tracejado do "Corte aqui" fica 9 pt acima dele
            f.text((largura - f.get_string_width(nome)) / 2, altura - y - 13, nome)
        pagina.merge_page(PdfReader(io.BytesIO(f.output())).pages[0])
        total += len(vias)
    if total:
        tmp = Path(pdf).with_suffix(".tmp")  # PDF pela metade não substitui o bom
        saida.write(tmp)
        tmp.replace(pdf)
    return total


def _notas_da_ida(p, tipo):
    """As NF-e das transações (TOTVS) como notas do 004 e CT-e ou RPS (mesma cidade = RPS)."""
    from . import pedidos  # pedidos importa este módulo indiretamente (alcis → acoes); aqui só na hora de emitir

    docs = documentos(p, tipo)
    nfs = totvs.notas_fiscais([d for d in docs if not d.tipo.eh_carga])
    for d in docs:
        if d.tipo.eh_carga:
            nfs += pedidos.notas_fiscais(d)
    nfs = list({n["chave"]: n for n in nfs}.values())  # a mesma NF em duas cargas vai uma vez só
    if not nfs:
        raise ValueError("sem transações")
    if len({n["cnpj"] for n in nfs}) > 1:
        raise ValueError(f"NFs de remetentes diferentes ({', '.join(sorted({n['cnpj'] for n in nfs}))}): um CT-e é de um remetente só")
    if any(not n["volumes"] for n in nfs):
        raise ValueError("NF sem volumes na TOTVS")
    notas = [SimpleNamespace(chave=n["chave"], numero=n["numero"], volumes=n["volumes"], operacao=0) for n in nfs]  # operação 0: peso de RT, 15 kg
    return notas, "RPS" if all(n["origem"] == n["destino"] for n in nfs) else "CT-e"


def emitir(pk, tipo="retorno"):
    from playwright.sync_api import sync_playwright

    p = Parada.objects.select_related("rota").get(pk=pk)
    pasta = settings.DADOS / "retiradas"
    pasta.mkdir(parents=True, exist_ok=True)
    prefixo = f"cte-{pk}" if tipo == "retorno" else f"cte-{tipo}-{pk}"

    def foto(pg, nome):
        try:
            pg.screenshot(path=str(pasta / f"{prefixo}-{nome}.png"), full_page=True)
        except Exception:
            pass

    _situacao(pk, EMITINDO, tipo)
    try:  # login e notas antes de abrir o navegador: nada foi ao SSW ainda
        lg = login(campo(p, tipo, "por"))
        if not lg:
            raise ValueError(f"{campo(p, tipo, 'por') or 'quem pediu'} não tem login do SSW salvo")
        c = {**config(), **lg}
        if tipo == "retorno":
            notas, extra = list(p.notas_retirada.order_by("emissao", "numero")), {}
        else:
            notas, doc = _notas_da_ida(p, tipo)
            extra = {"frete": p.frete_envio, "pagador": None, "f16": None, "doc": doc}
    except Exception as e:
        log.exception("SSW: falha antes do CT-e %s da parada %s", tipo, pk)
        _situacao(pk, f"Erro antes de gravar (nada foi gravado): {e}", tipo)
        return
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1400, "height": 900}, accept_downloads=True)
        ctx.on("page", lambda q: q.on("dialog", lambda d: (log.warning("SSW: aviso %r", d.message), d.dismiss())))
        ctrc = None
        try:
            menu = _entrar(ctx, c)
            ctrc, doc, trocas = _gravar(ctx, menu, p, notas, foto, **extra)
            _situacao(pk, f"CTRC {ctrc} ({doc}) gravado; enviando à {TELAS[doc]['destino']}", tipo, ctrc=ctrc)
            _situacao(pk, "; ".join([_enviar_e_imprimir(ctx, menu, p, ctrc, doc, tipo)] + trocas), tipo)
        except JaDigitada as e:
            _situacao(pk, f"Não emitido: as notas já estão no CTRC {e}", tipo, ctrc=str(e)[:20])
        except Exception as e:
            log.exception("SSW: falha no CT-e %s da parada %s", tipo, pk)
            for i, q in enumerate(ctx.pages):
                foto(q, f"erro{i}")
            atual = _no_banco(lambda: Parada.objects.get(pk=pk))
            if ctrc:
                _situacao(pk, f"CTRC {ctrc} gravado; erro depois: {e} (conferir na 007)", tipo)
            elif campo(atual, tipo, "situacao") == GRAVANDO:  # pode ter gravado: trava até alguém conferir
                _situacao(pk, f"Erro ao gravar ({e}): conferir no SSW antes de tentar de novo", tipo, ctrc="?")
            else:
                _situacao(pk, f"Erro antes de gravar (nada foi gravado): {e}", tipo)
        finally:
            b.close()


def iniciar():
    """Chamado pelo wsgi: uma loja por vez. O que ficou no meio quando o servidor reiniciou não volta sozinho."""
    if not config():
        return
    for tipo, x in TIPOS.items():
        for p in Parada.objects.filter(**{f"{x}_situacao__startswith": NA_FILA}):
            _fila.put((p.pk, tipo))
        Parada.objects.filter(**{f"{x}_situacao": EMITINDO}).update(
            **{f"{x}_situacao": "Interrompido antes de gravar (servidor reiniciou): pode tentar de novo"})
        Parada.objects.filter(**{f"{x}_situacao": GRAVANDO}).update(
            **{f"{x}_situacao": "Interrompido ao gravar (servidor reiniciou): conferir no SSW", f"{x}_ctrc": "?"})

    def laco():
        while True:
            pk, tipo = _fila.get()
            try:
                emitir(pk, tipo)
            except Exception as e:
                log.exception("SSW: falha no CT-e %s da parada %s", tipo, pk)
                if campo(Parada.objects.get(pk=pk), tipo, "situacao") == GRAVANDO:  # pode ter gravado: trava até alguém conferir
                    _situacao(pk, f"Erro ao gravar ({e}): conferir no SSW antes de tentar de novo", tipo, ctrc="?")
                else:
                    _situacao(pk, f"Erro antes de gravar (nada foi gravado): {e}", tipo)

    threading.Thread(target=laco, name="ssw", daemon=True).start()
