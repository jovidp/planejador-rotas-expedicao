from decimal import Decimal
from unittest import mock

import asyncio

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone

from rotas import acoes, ssw, totvs
from rotas.models import Documento, LoginSSW, NotaRetirada
from rotas.windows import abrir, proteger

CARGA = "wms"  # código da coluna da carga WMS (migration 0002)
CONFIG = {"URL": "https://ssw", "DOMINIO": "WLT", "CPF": "1", "USUARIO": "u", "SENHA": "s"}


class CarimboTest(SimpleTestCase):
    def test_loja_so_nas_vias_do_ctrc(self):
        import tempfile
        from datetime import date
        from pathlib import Path
        from types import SimpleNamespace

        from fpdf import FPDF
        from pypdf import PdfReader

        f = FPDF(unit="pt", format="A4")
        for ctrc in ("011492-8", "011500-1"):  # a segunda folha é de outro CT-e que estava na fila do "Imprimir / meus"
            f.add_page()
            f.set_font("helvetica", "", 8)
            for topo in (26, 445):  # duas vias por folha, como o SSW imprime
                f.text(338, topo, "D A C T E")
                f.text(470, topo + 380, f"WLT {ctrc}")
        pdf = Path(tempfile.mkdtemp()) / "cte.pdf"
        f.output(str(pdf))
        p = SimpleNamespace(loja="IGUABA", emp="277", rota=SimpleNamespace(nome="R. LAGOS", data=date(2026, 10, 27)))
        self.assertEqual(ssw.carimbar(pdf, p, "011492-8"), 2)
        folhas = [pg.extract_text() for pg in PdfReader(pdf).pages]
        self.assertEqual(folhas[0].count("IGUABA"), 2)
        self.assertNotIn("IGUABA", folhas[1])


class LeituraTest(SimpleTestCase):
    def test_contagem_da_007(self):
        texto = ("Situação dos CT-e's: | Atualizar fila | Digitados: | 1 | Enviar\xa0à\xa0SEFAZ | / | meus | Enviados\xa0à\xa0SEFAZ: | 3 | "
                 "Pré-CTRCs não enviados: | Autorizados\xa0(sem\xa0impressão): | 2 | Imprimir todos | / | meus | Denegados: | Rejeitados: | Em alteração: | 1")
        # a 007 usa &nbsp; nos rótulos: com espaço comum o "Autorizados" lia 0 (27/10, ARRAIAL DO CABO)
        self.assertEqual(ssw.contagem(texto), {"digitados": 1, "enviados": 3, "autorizados": 2, "rejeitados": 0, "denegados": 0})

    def test_contagem_da_009_rps(self):
        texto = ("Prefeitura Municipal: | RIO DE JANEIRO/RJ | Digitados: | 1 | Enviar à Prefeitura | / | meus | "
                 "Enviados\xa0a\xa0prefeitura: | 2 | Atualizar Fila | RPSs não enviados: | Autorizados (sem impressão): | 3 | Imprimir todos")
        self.assertEqual(ssw.contagem(texto), {"digitados": 1, "enviados": 2, "autorizados": 3, "rejeitados": 0, "denegados": 0})

    def test_loja_na_cidade_do_rio_e_rps(self):
        self.assertEqual(ssw.documento("RIO DE JANEIRO / RJ"), "RPS")
        self.assertEqual(ssw.documento("NOVA IGUACU / RJ"), "CT-e")
        self.assertEqual(ssw.documento("ARMACAO DOS BUZIOS / RJ"), "CT-e")

    def test_ctrc_gravado(self):
        self.assertEqual(ssw.ctrc_gravado("CTRC anterior: | 011454-5 | Placa Coleta:"), "011454-5")
        self.assertEqual(ssw.ctrc_gravado("CTRC\xa0anterior: | 011456-1"), "011456-1")  # o SSW usa &nbsp; no rótulo
        self.assertIsNone(ssw.ctrc_gravado("CTRC anterior: | Placa Coleta:"))

    def test_numero_e_formato_do_ssw(self):
        self.assertEqual(ssw.numero("1.967,11"), Decimal("1967.11"))
        self.assertEqual(ssw.br(Decimal("556.5"), 3), "556,500")


class LoginTest(SimpleTestCase):
    """O login do SSW leva o cursor ao Usuário (e seleciona o que tem nele) logo depois de carregar: preenchido antes
    disso, a senha caiu no Usuário e o SSW respondeu "Informe a senha" (27/10 17:24, ARARUAMA)."""
    PAGINA = """<input id="1"><input id="2"><input id="3"><input id="4" type="password">
        <a id="5" href="#" onclick="location.href = '/bin/menu01?' + new URLSearchParams({cedo: cedo, u: v(3), s: v(4)}); return false">►</a>
        <script>
        var focado = false, cedo = false;
        function v(i) { return document.getElementById(i).value; }
        document.addEventListener("input", function () { if (!focado) cedo = true; });
        onload = function () { setTimeout(function () { var u = document.getElementById("3"); u.focus(); u.select(); focado = true; }, 300); };
        </script>"""

    def entrar(self, extra=""):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            b = pw.chromium.launch()
            ctx = b.new_context()
            ctx.route("https://ssw.test/**", lambda r: r.fulfill(content_type="text/html", body='<input id="3">' if "menu01" in r.request.url else self.PAGINA + extra))
            try:
                return ssw._entrar(ctx, dict(CONFIG, URL="https://ssw.test")).url
            finally:
                b.close()

    def test_espera_o_cursor_da_pagina_antes_de_preencher(self):
        self.assertIn("/bin/menu01?cedo=false&u=u&s=s", self.entrar())

    def test_espera_o_cursor_em_toda_tela_nova(self):
        # 28/10, NITERÓI IV: digitando a placa BRA2E19 enquanto o 004 abria, o SSW selecionou o campo e ficou só o "2"
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page()
            pg.set_content('<input id="13"><script>setTimeout(function () { var f = document.getElementById("13"); f.focus(); f.select(); }, 300);</script>')
            ssw._pronta(pg)
            pg.click("[id='13']")
            pg.keyboard.type("BRA2E19", delay=100)
            self.assertEqual(pg.input_value("[id='13']"), "BRA2E19")
            b.close()

    def test_nao_entra_se_os_campos_nao_ficaram_certos(self):
        troca = '<script>document.getElementById("4").addEventListener("input", function () { this.previousElementSibling.value = this.value; this.value = ""; });</script>'
        with self.assertRaisesMessage(ValueError, "não entrei"):
            self.entrar(troca)


@mock.patch("rotas.ssw.config", return_value=CONFIG)
class BotaoTest(TestCase):
    def setUp(self):
        self.joao = User.objects.create_user("rafael.mendes", password="x")
        LoginSSW.objects.create(usuario=self.joao, cpf="1", login="jvitor", senha=proteger("s"))
        rota = acoes.criar_rota(self.joao, timezone.localdate(), "R. LAGOS", ["BÚZIOS"], False)
        rota.veiculo = "ATG2B02"
        rota.save()
        self.p = rota.paradas.get(loja="BÚZIOS")
        self.p.rt, self.p.cx, self.p.frete_retorno = "21", "69", Decimal("1967.11")
        self.p.save()
        hoje = timezone.localdate()
        for numero, op, vol in [(13377, 1448, 17), (13378, 1499, 3), (13379, 3343, 1), (13380, 3341, 69)]:
            NotaRetirada.objects.create(parada=self.p, chave=str(numero).zfill(44), numero=numero, emissao=hoje, operacao=op, volumes=vol)

    def test_peso_rt_15_e_cx_3_5(self, _):
        self.assertEqual(sum(ssw.peso(n) for n in self.p.notas_retirada.all()), Decimal("556.5"))  # 21 × 15 + 69 × 3,5

    def test_botao_poe_na_fila_e_nao_deixa_apertar_de_novo(self, _):
        # a fila de verdade: em 27/10 uma função com o mesmo nome a apagou e o botão dava AttributeError
        self.addCleanup(lambda: [ssw._fila.get_nowait() for _ in range(ssw._fila.qsize())])
        self.client.login(username="rafael.mendes", password="x")
        pagina = self.client.get("/retiradas/")
        self.assertContains(pagina, "fazer-cte")
        self.assertContains(pagina, 'data-notas="4" data-volumes="90"')  # resumo da janela de confirmação
        self.assertContains(pagina, 'data-peso="556,500 kg" data-frete="R$ 1.967,11"')
        self.assertEqual(self.client.post(f"/retiradas/cte/{self.p.pk}/").status_code, 200)
        self.assertEqual(list(ssw._fila.queue), [(self.p.pk, "retorno")])
        self.p.refresh_from_db()
        self.assertEqual((self.p.cte_situacao, self.p.cte_por), (ssw.NA_FILA, "rafael.mendes"))  # entra no SSW com o login dele
        resposta = self.client.post(f"/retiradas/cte/{self.p.pk}/")
        self.assertEqual((resposta.status_code, resposta.json()), (409, {"erro": "já está sendo feito"}))
        self.p.cte_situacao, self.p.cte_ctrc = "CTRC 011452-9 autorizado", "011452-9"
        self.p.save()
        self.assertEqual(self.client.post(f"/retiradas/cte/{self.p.pk}/").json(), {"erro": "já tem CT-e"})
        self.assertEqual(list(ssw._fila.queue), [(self.p.pk, "retorno")])  # os cliques recusados não entram na fila

    def test_sem_placa_ou_frete_nao_emite(self, _):
        self.p.rota.veiculo = ""
        self.p.rota.save()
        self.assertEqual(ssw.pedir(self.p, self.joao), "rota sem placa")
        self.p.rota.veiculo, self.p.frete_retorno = "ATG2B02", None
        self.assertEqual(ssw.pedir(self.p, self.joao), "loja sem frete")

    def test_sem_login_do_ssw_nao_entra_na_fila(self, _):
        maria = User.objects.create_user("maria", password="x")
        self.assertEqual(ssw.pedir(self.p, maria), "salve o seu login do SSW primeiro")
        self.assertEqual(ssw._fila.qsize(), 0)

    def test_imprimir_todos_da_rota_nas_retiradas(self, _):
        import io, tempfile
        from pathlib import Path
        from django.test import override_settings
        from fpdf import FPDF
        from pypdf import PdfReader
        self.client.login(username="rafael.mendes", password="x")
        with tempfile.TemporaryDirectory() as d, override_settings(DADOS=Path(d)):
            self.assertNotContains(self.client.get("/retiradas/"), "Imprimir todos")
            f = FPDF()
            f.add_page()
            ssw.arquivo(self.p).parent.mkdir(parents=True, exist_ok=True)
            f.output(str(ssw.arquivo(self.p)))
            self.assertContains(self.client.get("/retiradas/"), "Imprimir todos (1)")
            r = self.client.get(f"/retiradas/rota/{self.p.rota_id}.pdf")
            self.assertEqual(len(PdfReader(io.BytesIO(b"".join(r.streaming_content))).pages), 1)
            User.objects.create_user("bruna.lima", password="x")
            self.client.login(username="bruna.lima", password="x")
            self.assertEqual(self.client.get(f"/retiradas/rota/{self.p.rota_id}.pdf").status_code, 404)  # só os donos

    def test_so_o_joao_aperta(self, _):
        User.objects.create_user("bruna.lima", password="x")
        self.client.login(username="bruna.lima", password="x")
        self.assertEqual(self.client.post(f"/retiradas/cte/{self.p.pk}/").status_code, 404)
        self.assertEqual(self.client.get(f"/retiradas/cte/{self.p.pk}/").status_code, 405)  # só POST


class BancoComNavegadorAbertoTest(TransactionTestCase):
    """Com o Playwright aberto há um loop assíncrono na thread; o Django recusava gravar a situação (27/10, ARRAIAL DO CABO)."""
    def test_situacao_grava_dentro_do_loop(self):
        joao = User.objects.create_user("rafael.mendes")
        p = acoes.criar_rota(joao, timezone.localdate(), "R. LAGOS", ["IGUABA"], False).paradas.get(loja="IGUABA")

        async def com_navegador_aberto():
            ssw._situacao(p.pk, ssw.GRAVANDO)
        asyncio.run(com_navegador_aberto())
        p.refresh_from_db()
        self.assertEqual(p.cte_situacao, ssw.GRAVANDO)


class NotasDaCargaTest(TestCase):
    """Vários pedidos da carga numa NF só (28/10, loja 335: 8 pedidos na NF 85834): uma nota no CT-e."""
    @mock.patch("rotas.totvs.nota_fiscal", side_effect=lambda c, tk, emp, num, dia, trans, rotulo: {"numero": num, "emp": emp, "trans": trans})
    @mock.patch("rotas.totvs.token", return_value="tk")
    @mock.patch("rotas.totvs.config", return_value={"URL": "https://totvs"})
    @mock.patch("rotas.pedidos._buscar")
    @mock.patch("rotas.alcis.baixar_carga", return_value=[{"COD_CLIENTE": "100000335", "PEDIDO": "706510"}, {"COD_CLIENTE": "100000335", "PEDIDO": "706512"}])
    def test_uma_nf_por_nota_distinta_dos_pedidos(self, _alcis, buscar, *_):
        from rotas import pedidos
        inv = {"code": 85834, "issueDate": "2026-10-28T00:00:00", "transactionCode": 633, "transactionBranchCode": 209}
        buscar.return_value = [{"orderCode": 706510, "customerCode": 100000335, "statusOrder": "Attended", "invoices": [inv]},
                               {"orderCode": 706512, "customerCode": 100000335, "statusOrder": "Attended", "invoices": [inv]},
                               {"orderCode": 706512, "customerCode": 100000999, "statusOrder": "Attended", "invoices": [{**inv, "code": 1}]}]  # outra loja
        carga = mock.Mock(numero="5001")
        self.assertEqual(pedidos.notas_fiscais(carga), [{"numero": 85834, "emp": 209, "trans": 633}])
        buscar.return_value[0]["invoices"] = []
        with self.assertRaisesMessage(ValueError, "pedido 706510 sem NF"):
            pedidos.notas_fiscais(carga)


class LoginSSWTest(TestCase):
    def test_senha_guardada_protegida_e_cada_um_salva_o_seu(self):
        maria = User.objects.create_user("maria", password="x")
        self.client.login(username="maria", password="x")
        self.assertEqual(self.client.post("/ssw/login/", {"cpf": "1", "login": "maria.s"}).status_code, 400)  # sem senha
        self.assertEqual(self.client.post("/ssw/login/", {"cpf": "123", "login": "maria.s", "senha": "segredo"}).json(), {"ok": True})
        lg = LoginSSW.objects.get(usuario=maria)
        self.assertNotIn("segredo", lg.senha)
        self.assertEqual(abrir(lg.senha), "segredo")
        self.assertEqual(ssw.login("maria"), {"CPF": "123", "USUARIO": "maria.s", "SENHA": "segredo"})
        self.assertIsNone(ssw.login("outro"))


@mock.patch("rotas.ssw.config", return_value={"URL": "https://ssw", "DOMINIO": "WLT"})
class IdaTest(TestCase):
    def setUp(self):
        self.addCleanup(lambda: [ssw._fila.get_nowait() for _ in range(ssw._fila.qsize())])
        self.maria = User.objects.create_user("maria", password="x")  # qualquer usuário faz o CT-e da ida
        LoginSSW.objects.create(usuario=self.maria, cpf="1", login="maria.s", senha=proteger("s"))
        rota = acoes.criar_rota(self.maria, timezone.localdate(), "BAIXADA I", ["MAX SHOPPING"], False)
        rota.veiculo = "ATG2B02"
        rota.save()
        self.p = rota.paradas.get(loja="MAX SHOPPING")
        self.p.pes, self.p.frete_envio = "233", Decimal("1369.11")
        self.p.save()
        for codigo, numero, vol in [("t434", "377", 1), ("t3386", "562", 3), ("sacola", "12136680", 3), (CARGA, "5001", 2)]:
            acoes.editar_documento(self.maria, self.p, codigo, numero)
            Documento.objects.filter(parada=self.p, tipo__codigo=codigo).update(situacao_totvs=totvs.SITUACOES["Attended"], volumes=vol)
        Documento.objects.filter(parada=self.p, tipo__eh_carga=True).update(situacao_totvs="", pedidos_fase="atendida")
        self.client.login(username="maria", password="x")

    def test_pagina_mostra_os_dois_ctes_da_loja(self, _):
        pagina = self.client.get("/ctes/")
        self.assertContains(pagina, "MAX SHOPPING")
        self.assertContains(pagina, f"/ctes/{self.p.pk}/ida/")
        self.assertContains(pagina, f"/ctes/{self.p.pk}/sacola/")
        self.assertContains(pagina, 'data-notas="3" data-volumes="6"')  # 434 + 3386 + carga WMS
        self.assertContains(pagina, 'data-peso="90,000 kg" data-frete="R$ 1.369,11"')  # 6 vol × 15 kg, frete de envio
        self.assertContains(pagina, 'data-peso="45,000 kg"')  # sacola: 3 × 15
        self.assertContains(pagina, "Fazer todos os CT-es (2)")
        acoes.editar_documento(self.maria, self.p, "t434", "377 999")
        Documento.objects.filter(parada=self.p, numero="999").update(situacao_totvs="Cancelada", volumes=5)
        pagina = self.client.get("/ctes/")
        self.assertNotContains(pagina, "434 999")  # cancelada não aparece nem entra no CT-e
        self.assertContains(pagina, 'data-notas="3" data-volumes="6"')

    def test_cada_cte_na_fila_com_quem_pediu_e_transacao_nao_atendida_nao_vai(self, _):
        self.assertEqual(self.client.post(f"/ctes/{self.p.pk}/ida/").json(), {"ok": True})
        self.assertEqual(self.client.post(f"/ctes/{self.p.pk}/sacola/").json(), {"ok": True})
        self.assertEqual(list(ssw._fila.queue), [(self.p.pk, "ida"), (self.p.pk, "sacola")])
        self.p.refresh_from_db()
        self.assertEqual((self.p.ida_por, self.p.sacola_por, self.p.cte_situacao), ("maria", "maria", ""))  # o retorno não mexe
        self.assertEqual(self.client.post(f"/ctes/{self.p.pk}/ida/").json(), {"erro": "já está sendo feito"})
        self.assertEqual(self.client.post(f"/ctes/{self.p.pk}/outro/").status_code, 404)
        Parada = type(self.p)
        Parada.objects.filter(pk=self.p.pk).update(sacola_situacao="")
        Documento.objects.filter(parada=self.p, tipo__codigo="sacola").update(situacao_totvs="Em andamento")
        self.assertEqual(self.client.post(f"/ctes/{self.p.pk}/sacola/").json(), {"erro": "transação não atendida"})
        Parada.objects.filter(pk=self.p.pk).update(ida_situacao="")
        Documento.objects.filter(parada=self.p, tipo__eh_carga=True).update(pedidos_fase="expedida")  # pedidos ainda no 099
        self.assertEqual(self.client.post(f"/ctes/{self.p.pk}/ida/").json(), {"erro": "transação não atendida"})

    def test_imprimir_todos_junta_os_dactes_da_rota(self, _):
        import io, tempfile
        from pathlib import Path
        from django.test import override_settings
        from fpdf import FPDF
        from pypdf import PdfReader
        self.assertNotContains(self.client.get("/ctes/"), "Imprimir todos")
        self.assertEqual(self.client.get(f"/ctes/rota/{self.p.rota_id}.pdf").status_code, 404)  # nenhum DACTE ainda
        with tempfile.TemporaryDirectory() as d, override_settings(DADOS=Path(d)):
            for tipo in ("ida", "sacola"):
                f = FPDF()
                f.add_page()
                ssw.arquivo(self.p, tipo).parent.mkdir(parents=True, exist_ok=True)
                f.output(str(ssw.arquivo(self.p, tipo)))
            self.assertContains(self.client.get("/ctes/"), "Imprimir todos (2)")
            r = self.client.get(f"/ctes/rota/{self.p.rota_id}.pdf")
            self.assertEqual(len(PdfReader(io.BytesIO(b"".join(r.streaming_content))).pages), 2)

    @mock.patch("rotas.pedidos.notas_fiscais")
    @mock.patch("rotas.totvs.notas_fiscais")
    def test_notas_da_ida_rps_so_quando_loja_na_mesma_cidade(self, nfs, da_carga, _):
        nf = {"chave": "3" * 44, "numero": 85809, "volumes": 1, "cnpj": "11222333000181", "origem": "RIO DE JANEIRO", "destino": "NOVA IGUACU"}
        nfs.return_value = [nf, {**nf, "chave": "4" * 44, "numero": 85828, "volumes": 3}]
        da_carga.return_value = [{**nf, "chave": "5" * 44, "numero": 85834, "volumes": 2}, nf]  # a mesma NF de novo vai uma vez só
        notas, doc = ssw._notas_da_ida(self.p, "ida")
        self.assertEqual((doc, [n.numero for n in notas], sum(ssw.peso(n) for n in notas)), ("CT-e", [85809, 85828, 85834], Decimal("90")))
        da_carga.return_value = []
        nfs.return_value = [{**nf, "destino": "RIO DE JANEIRO"}]
        self.assertEqual(ssw._notas_da_ida(self.p, "ida")[1], "RPS")
        nfs.return_value = [nf, {**nf, "chave": "6" * 44, "cnpj": "11222333000262"}]
        with self.assertRaisesMessage(ValueError, "remetentes diferentes"):
            ssw._notas_da_ida(self.p, "ida")

    @mock.patch("rotas.ssw.sync_playwright", create=True)
    def test_sem_nf_na_totvs_nao_abre_o_ssw(self, _pw, _):
        ssw.pedir(self.p, self.maria, "ida")
        ssw._fila.get_nowait()
        with mock.patch("rotas.totvs.notas_fiscais", side_effect=ValueError("434 377: sem NF na TOTVS")), \
             mock.patch("playwright.sync_api.sync_playwright") as pw:
            ssw.emitir(self.p.pk, "ida")
        pw.assert_not_called()
        self.p.refresh_from_db()
        self.assertEqual(self.p.ida_situacao, "Erro antes de gravar (nada foi gravado): 434 377: sem NF na TOTVS")

    def test_javascript_da_pagina_sem_erro_de_sintaxe(self, _):
        import re, shutil, subprocess, tempfile
        from pathlib import Path
        if not shutil.which("node"):
            self.skipTest("sem node")
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / "pagina.js"
            js.write_text(re.findall(r"<script>(.*?)</script>", self.client.get("/ctes/").content.decode(), re.S)[-1], encoding="utf-8")
            r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
