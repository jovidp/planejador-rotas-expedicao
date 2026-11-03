import base64
import tempfile
import urllib.error
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from rotas import acoes, retiradas
from rotas.models import NotaRetirada

CONFIG = {"URL": "https://totvs", "CLIENT_ID": "c", "CLIENT_SECRET": "s", "USUARIO": "u", "SENHA": "p", "EMPRESA": "1"}
HOJE = timezone.localdate()
CHAVE = "3" * 44


def nota(chave=CHAVE, op=1499, status="Authorized", vol=8, numero=9852):
    return {"invoiceCode": numero, "serialCode": "1", "issueDate": (HOJE - timedelta(days=1)).isoformat(), "operationCode": op,
            "operatioName": "\x10499 - SAIDA TRANSFERENCIA", "totalValue": 6190.22, "shippingCompany": {"packageNumber": vol},
            "eletronic": {"accessKey": chave, "electronicInvoiceStatus": status}}


@mock.patch("rotas.totvs.config", return_value=CONFIG)
@mock.patch("rotas.totvs.token", return_value="tk")
@mock.patch("rotas.totvs._get", return_value={"mainInvoiceXml": "<xml/>"})
@mock.patch("rotas.totvs._post")
class RetiradasTest(TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        ajuste = override_settings(DADOS=Path(self.pasta.name))
        ajuste.enable()
        self.addCleanup(ajuste.disable)
        self.joao = User.objects.create_user("rafael.mendes", password="x")
        rota = acoes.criar_rota(self.joao, HOJE, "BAIXADA I", ["N. IGUAÇU II"], False)
        self.parada = rota.paradas.get(loja="N. IGUAÇU II")
        self.parada.emp, self.parada.rt = "268", "8"
        self.parada.save()

    def responder(self, post, notas):
        pdf = base64.b64encode(b"%PDF-1.5 danfe").decode()
        post.side_effect = lambda url, corpo, tk: {"danfePdfBase64": pdf} if url.endswith("danfe-search") else {"items": notas}

    def test_guarda_o_pdf_das_notas_autorizadas_do_dia_da_rota_e_2_antes(self, post, get, *_):
        self.responder(post, [nota(), nota(chave="4" * 44, status="Canceled", numero=9853)])
        self.assertEqual(retiradas.sincronizar(), 1)
        filtro = post.call_args_list[0].args[1]["filter"]
        self.assertEqual(filtro, {"branchCodeList": [268], "personCodeList": [100000233], "operationType": "Output",
                                  "startIssueDate": f"{HOJE - timedelta(days=2)}T00:00:00",
                                  "endIssueDate": f"{HOJE}T23:59:59"})
        get.assert_called_once_with(f"https://totvs/api/totvsmoda/fiscal/v2/xml-contents/{CHAVE}", "tk")
        self.assertEqual(retiradas.arquivo(CHAVE).read_bytes(), b"%PDF-1.5 danfe")
        n = NotaRetirada.objects.get()
        self.assertEqual((n.parada, n.numero, n.operacao, n.volumes), (self.parada, 9852, 1499, 8))
        self.assertEqual(n.nome_operacao, "499 - SAIDA TRANSFERENCIA")
        self.assertEqual(retiradas.sincronizar(), 0)  # já baixada: não gera o PDF de novo
        self.assertEqual(get.call_count, 1)

    def test_nota_cancelada_depois_sai_da_lista(self, post, *_):
        self.responder(post, [nota()])
        retiradas.sincronizar()
        self.responder(post, [nota(status="Canceled")])
        retiradas.sincronizar()
        self.assertFalse(NotaRetirada.objects.exists())

    def test_nota_sem_pdf_aparece_assim_mesmo(self, post, get, *_):
        get.side_effect = RuntimeError("8816 - Modelo de NF não informado")
        self.responder(post, [nota()])
        self.assertEqual(retiradas.sincronizar(), 1)
        self.client.login(username="rafael.mendes", password="x")
        r = self.client.get("/retiradas/")
        self.assertContains(r, "<span class=\"nf\">NF 9852</span>")  # sem link para o PDF
        self.assertContains(r, "<span class=\"dif\">PDF não gerado pela TOTVS</span>")
        get.side_effect = None
        retiradas.sincronizar()  # tenta de novo
        self.assertTrue(retiradas.arquivo(CHAVE).exists())

    @mock.patch("rotas.retiradas.Danfe")
    def test_danfe_recusado_pela_totvs_e_gerado_do_xml(self, danfe, post, get, *_):
        get.return_value = {"mainInvoiceXml": base64.b64encode(b"<nfeProc/>").decode()}
        danfe.return_value.output.return_value = bytearray(b"%PDF-1.4 local")

        def responder(url, corpo, tk):
            if url.endswith("danfe-search"):
                raise urllib.error.HTTPError(url, 400, "8816 - Modelo de NF não informado", {}, None)
            return {"items": [nota()]}
        post.side_effect = responder
        self.assertEqual(retiradas.sincronizar(), 1)
        danfe.assert_called_once_with(xml=b"<nfeProc/>")
        self.assertEqual(retiradas.arquivo(CHAVE).read_bytes(), b"%PDF-1.4 local")

    @mock.patch("rotas.retiradas.Danfe")
    def test_danfe_sem_resposta_da_totvs_e_gerado_do_xml(self, danfe, post, get, *_):
        get.return_value = {"mainInvoiceXml": base64.b64encode(b"<nfeProc/>").decode()}
        danfe.return_value.output.return_value = bytearray(b"%PDF-1.4 local")

        def responder(url, corpo, tk):
            if url.endswith("danfe-search"):
                raise TimeoutError("The read operation timed out")
            return {"items": [nota()]}
        post.side_effect = responder
        self.assertEqual(retiradas.sincronizar(), 1)
        self.assertEqual(retiradas.arquivo(CHAVE).read_bytes(), b"%PDF-1.4 local")

    def test_pagina_mostra_um_dia_por_vez(self, *_):
        ontem = acoes.criar_rota(self.joao, HOJE - timedelta(days=1), "BAIXADA III", ["CARIOCA"], False).paradas.get(loja="CARIOCA")
        ontem.emp, ontem.rt = "248", "5"
        ontem.save()
        self.client.login(username="rafael.mendes", password="x")
        pagina = self.client.get("/retiradas/")  # abre no de hoje
        self.assertContains(pagina, "N. IGUAÇU II")
        self.assertNotContains(pagina, "CARIOCA")
        self.assertContains(pagina, f'value="{ontem.rota.data.isoformat()}" aria-pressed="false"')
        pagina = self.client.get(f"/retiradas/?dia={ontem.rota.data.isoformat()}")
        self.assertContains(pagina, "CARIOCA")
        self.assertNotContains(pagina, "N. IGUAÇU II")
        pagina = self.client.get("/retiradas/?dia=")  # todos
        self.assertContains(pagina, "CARIOCA")
        self.assertContains(pagina, "N. IGUAÇU II")

    def test_so_o_joao_ve_a_pagina_e_o_pdf(self, post, *_):
        self.responder(post, [nota()])
        retiradas.sincronizar()
        User.objects.create_user("bruna.lima", password="x")
        self.client.login(username="bruna.lima", password="x")
        self.assertEqual(self.client.get("/retiradas/").status_code, 404)
        self.assertEqual(self.client.get(f"/retiradas/{CHAVE}.pdf").status_code, 404)
        self.assertNotContains(self.client.get("/"), "/retiradas/")
        self.client.login(username="rafael.mendes", password="x")
        self.assertContains(self.client.get("/"), "/retiradas/")
        pagina = self.client.get("/retiradas/")
        self.assertContains(pagina, "N. IGUAÇU II")
        self.assertContains(pagina, f"/retiradas/{CHAVE}.pdf")
        self.assertContains(pagina, f'data-chaves="{CHAVE}">Copiar 1 chave<')  # para colar no SSW 004
        pdf = self.client.get(f"/retiradas/{CHAVE}.pdf")
        self.assertEqual((pdf["Content-Type"], b"".join(pdf.streaming_content)), ("application/pdf", b"%PDF-1.5 danfe"))
        pdf.close()

    def test_buscar_agora_procura_na_hora_e_so_para_o_joao(self, post, *_):
        self.responder(post, [nota()])
        User.objects.create_user("bruna.lima", password="x")
        self.client.login(username="bruna.lima", password="x")
        self.assertEqual(self.client.post("/retiradas/buscar/").status_code, 404)
        self.client.login(username="rafael.mendes", password="x")
        self.assertEqual(self.client.post("/retiradas/buscar/").json(), {"novas": 1})
        self.assertTrue(NotaRetirada.objects.filter(chave=CHAVE).exists())
        self.assertContains(self.client.get("/retiradas/"), "Buscar agora")

    def test_javascript_da_pagina_sem_erro_de_sintaxe(self, post, *_):
        # 29/10: uma quebra de linha dentro de uma string parou todos os botões da página
        import re, shutil, subprocess
        if not shutil.which("node"):
            self.skipTest("sem node")
        self.responder(post, [nota()])
        retiradas.sincronizar()
        self.client.login(username="rafael.mendes", password="x")
        js = Path(self.pasta.name) / "pagina.js"
        js.write_text(re.findall(r"<script>(.*?)</script>", self.client.get("/retiradas/").content.decode(), re.S)[-1], encoding="utf-8")
        r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
