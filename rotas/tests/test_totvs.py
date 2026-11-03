import io
import time
import urllib.error
from unittest import mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from rotas import acoes, totvs
from rotas.models import Documento

HOJE = timezone.localdate().isoformat() + "T00:00:00"
CONFIG = {"URL": "https://totvs", "CLIENT_ID": "c", "CLIENT_SECRET": "s", "USUARIO": "u", "SENHA": "p", "EMPRESA": "1"}


@mock.patch("rotas.totvs.config", return_value=CONFIG)
@mock.patch("rotas.totvs.token", return_value="tk")
@mock.patch("rotas.totvs._post")
class SincronizarTest(TestCase):
    def setUp(self):
        self.joao = User.objects.create_user("joao")
        rota = acoes.criar_rota(self.joao, timezone.localdate(), "BAIXADA II", ["PIABETÁ"], False)
        self.parada = rota.paradas.get(loja="PIABETÁ")
        self.parada.pes = "123"
        self.parada.save()
        self.doc = acoes.editar_documento(self.joao, self.parada, "t434", "23")

    def test_atendida_na_totvs_confirma_o_atendimento(self, post, *_):
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 434, "status": "Attended", "transactionDate": HOJE}]}
        self.assertEqual(totvs.sincronizar(), 1)
        url, corpo, tk = post.call_args.args
        self.assertEqual(url, "https://totvs/api/totvsmoda/general/v2/transactions/search")
        self.assertEqual(corpo, {"branchCodeList": [1], "customerCodeList": [100000123], "operationCodeList": [434],
                                 "transactionCodeList": [23], "pageSize": 500})  # loja = cliente "100000" + PES (3 dígitos); sem filtro de situação
        self.doc.refresh_from_db()
        self.assertEqual((self.doc.atendido_por, self.doc.situacao_totvs), ("TOTVS", "Atendida"))
        self.assertTrue(self.parada.rota.historico.filter(acao__startswith="TOTVS confirmou atendimento").exists())

    def test_marcada_a_mao_continua_conferida_e_avisa_se_nao_bate(self, post, *_):
        acoes.atender(self.joao, self.doc, True)
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 434, "status": "InProgress", "transactionDate": HOJE}]}
        self.assertEqual(totvs.sincronizar(), 1)
        self.doc.refresh_from_db()
        self.assertEqual((self.doc.atendido_por, self.doc.situacao_totvs), ("JOAO", "Em andamento"))
        self.assertTrue(self.parada.rota.historico.filter(acao="TOTVS informou 434 23 de PIABETÁ: Em andamento (marcada atendida no sistema)").exists())
        post.return_value["items"][0]["status"] = "Attended"
        totvs.sincronizar()
        self.doc.refresh_from_db()
        self.assertEqual((self.doc.atendido_por, self.doc.situacao_totvs), ("TOTVS", "Atendida"))  # sem NF ainda: não fica quem marcou
        with mock.patch("rotas.totvs.dados_da_nf", return_value=("BEATRIZ SANTOS", 3)):
            totvs.sincronizar()
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.atendido_por, "BEATRIZ SANTOS")  # nome de quem faturou na TOTVS
        post.reset_mock()
        totvs.sincronizar()
        post.assert_not_called()  # atendida na TOTVS, com o nome: não consulta mais

    @mock.patch("rotas.totvs.dados_da_nf", return_value=("BEATRIZ SANTOS", 3))
    def test_marcada_a_mao_troca_pelo_nome_da_totvs(self, nf, post, *_):
        acoes.atender(self.joao, self.doc, True)
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 434, "status": "Attended", "transactionDate": HOJE}]}
        totvs.sincronizar()
        self.doc.refresh_from_db()
        self.assertEqual((self.doc.atendido_por, self.doc.volumes), ("BEATRIZ SANTOS", 3))

    def test_numero_repetido_de_anos_atras_nao_vale(self, post, *_):
        # a TOTVS recomeça a numeração: a 23 de 2025 (atendida) não confirma a 23 desta rota
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 434, "status": 4, "transactionDate": "2025-08-29T00:00:00"}]}
        self.assertEqual(totvs.sincronizar(), 0)
        post.return_value["items"].append({"transactionCode": 23, "operationCode": 434, "status": 1, "transactionDate": HOJE})
        self.assertEqual(totvs.sincronizar(), 1)  # vale a mais recente, dentro de 30 dias da rota
        self.doc.refresh_from_db()
        self.assertEqual((self.doc.atendido_por, self.doc.situacao_totvs), ("", "Em andamento"))

    def test_em_andamento_aparece_no_sistema_sem_confirmar(self, post, *_):
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 434, "status": "InProgress", "transactionDate": HOJE}]}
        self.assertEqual(totvs.sincronizar(), 1)
        self.doc.refresh_from_db()
        self.assertEqual((self.doc.atendido_por, self.doc.situacao_totvs), ("", "Em andamento"))
        self.assertTrue(self.parada.rota.historico.filter(acao="TOTVS informou 434 23 de PIABETÁ: Em andamento").exists())
        self.assertEqual(totvs.sincronizar(), 0)  # mesma situação: não grava de novo

    def test_situacao_em_numero_tambem_vale(self, post, *_):
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 434, "status": 2, "transactionDate": HOJE}]}
        totvs.sincronizar()
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.situacao_totvs, "Liberada p/ faturamento")

    def _clicar(self):
        self.client.force_login(self.joao)
        return self.client.post(f"/api/documento/{self.doc.pk}/atender/", '{"sim": true}', content_type="application/json")

    def test_clique_antes_de_atender_na_totvs_e_bloqueado(self, post, *_):
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 434, "status": "InProgress", "transactionDate": HOJE}]}
        r = self._clicar()
        self.assertEqual(r.status_code, 400)
        self.assertIn('está "Em andamento" na TOTVS', r.json()["erro"])
        self.doc.refresh_from_db()
        self.assertEqual((self.doc.atendido_por, self.doc.situacao_totvs), ("", "Em andamento"))  # não fica vermelha

    @mock.patch("rotas.totvs.dados_da_nf", return_value=("BEATRIZ SANTOS", 3))
    def test_clique_depois_de_atender_na_totvs_marca_na_hora(self, nf, post, *_):
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 434, "status": "Attended", "transactionDate": HOJE}]}
        self.assertEqual(self._clicar().status_code, 200)
        self.doc.refresh_from_db()
        self.assertEqual((self.doc.atendido_por, self.doc.situacao_totvs), ("BEATRIZ SANTOS", "Atendida"))

    def test_clique_com_totvs_fora_do_ar_vale_o_plano_b(self, post, *_):
        post.side_effect = OSError("fora do ar")
        self.assertEqual(self._clicar().status_code, 200)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.atendido_por, "JOAO")

    def test_clique_sem_achar_na_totvs_e_bloqueado(self, post, *_):
        post.return_value = {"items": []}
        r = self._clicar()
        self.assertEqual(r.status_code, 400)
        self.assertIn("Não achei a transação 23 de PIABETÁ na TOTVS", r.json()["erro"])
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.atendido_por, "")

    def test_clique_em_rota_antiga_tambem_confere(self, post, *_):
        rota = self.parada.rota
        rota.data = timezone.localdate() - timezone.timedelta(days=8)  # fora da conferência automática (DIAS_PARA_TRAS)
        rota.save()
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 434, "status": "InProgress", "transactionDate": rota.data.isoformat()}]}
        self.assertEqual(self._clicar().status_code, 400)

    def test_clique_em_loja_sem_pes_vale_o_plano_b(self, post, *_):
        self.parada.pes = ""
        self.parada.save()
        self.assertEqual(self._clicar().status_code, 200)
        post.assert_not_called()

    def test_nao_encontrada_continua_pendente(self, post, *_):
        post.return_value = {"items": []}
        self.assertEqual(totvs.sincronizar(), 0)
        self.doc.refresh_from_db()
        self.assertEqual((self.doc.atendido_por, self.doc.situacao_totvs), ("", ""))

    def test_trocar_o_numero_limpa_a_situacao(self, post, *_):
        Documento.objects.filter(pk=self.doc.pk).update(situacao_totvs="Em andamento")
        self.doc.refresh_from_db()
        self.assertEqual(acoes.editar_documento(self.joao, self.parada, "t434", "24").situacao_totvs, "")

    def test_outra_operacao_com_o_mesmo_numero_nao_confirma(self, post, *_):
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 3386, "status": "Attended", "transactionDate": HOJE}]}
        self.assertEqual(totvs.sincronizar(), 0)

    def test_sacola_aceita_as_duas_operacoes(self, post, *_):
        acoes.editar_documento(self.joao, self.parada, "t434", "")
        sacola = acoes.editar_documento(self.joao, self.parada, "sacola", "77")
        post.return_value = {"items": [{"transactionCode": 77, "operationCode": 538, "status": "Attended", "transactionDate": HOJE}]}
        self.assertEqual(totvs.sincronizar(), 1)
        self.assertEqual(post.call_args.args[1]["operationCodeList"], [538, 4437])
        sacola.refresh_from_db()
        self.assertEqual(sacola.atendido_por, "TOTVS")

    def test_atendimento_desfeito_a_mao_nao_volta(self, post, *_):
        # caso real: sacola 12136318 lida "Encerrada" no meio do faturamento, desmarcada à mão e presa assim com a TOTVS já Atendida
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 434, "status": 5, "transactionDate": HOJE}]}
        totvs.sincronizar()
        acoes.atender(self.joao, self.doc, True)
        acoes.atender(self.joao, self.doc, False)
        post.return_value["items"][0]["status"] = 4
        self.assertEqual(totvs.sincronizar(), 1)
        self.doc.refresh_from_db()
        self.assertEqual((self.doc.situacao_totvs, self.doc.atendido_por), ("Atendida", ""))  # a situação segue a TOTVS, o atendimento não volta

    @mock.patch("rotas.totvs.dados_da_nf", return_value=("BEATRIZ SANTOS", 3))
    def test_nf_cancelada_depois_de_atendida_avisa(self, nf, post, *_):
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 434, "status": 4, "transactionDate": HOJE}]}
        totvs.sincronizar()
        post.return_value["items"][0]["status"] = 6  # NF cancelada ou excluída: a TOTVS deixa a transação Cancelada
        post.reset_mock()
        self.assertEqual(totvs.sincronizar(), 0)
        post.assert_not_called()  # rodada comum não olha as atendidas
        self.assertEqual(totvs.sincronizar(com_atendidas=True), 1)
        self.doc.refresh_from_db()
        self.assertEqual((self.doc.situacao_totvs, self.doc.atendido_por), ("Cancelada", "BEATRIZ SANTOS"))  # a tela mostra a divergência
        self.assertTrue(self.parada.rota.historico.filter(acao="TOTVS informou 434 23 de PIABETÁ: Cancelada (era Atendida: NF cancelada ou excluída)").exists())
        post.reset_mock()
        totvs.sincronizar()
        post.assert_called_once()  # cancelada: volta a ser conferida em toda rodada

    def test_atendida_sem_mudanca_nao_regrava(self, post, *_):
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 434, "status": 4, "transactionDate": HOJE}]}
        with mock.patch("rotas.totvs.dados_da_nf", return_value=("BEATRIZ SANTOS", 3)):
            totvs.sincronizar()
        with mock.patch("rotas.totvs.dados_da_nf") as nf:
            self.assertEqual(totvs.sincronizar(com_atendidas=True), 0)
            nf.assert_not_called()

    @mock.patch("rotas.totvs.dados_da_nf", return_value=("CARLOS PEREIRA", 2))
    def test_desfeito_e_marcado_de_novo_volta_a_conferir(self, nf, post, *_):
        # caso real: sacola 12135890, marcada e desmarcada várias vezes, ficou "Em andamento" com a TOTVS já Atendida
        acoes.atender(self.joao, self.doc, True)
        acoes.atender(self.joao, self.doc, False)
        acoes.atender(self.joao, self.doc, True)
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 434, "status": 4, "transactionDate": HOJE}]}
        self.assertEqual(totvs.sincronizar(), 1)
        self.doc.refresh_from_db()
        self.assertEqual((self.doc.situacao_totvs, self.doc.atendido_por), ("Atendida", "CARLOS PEREIRA"))

    def test_clique_atrasado_nao_troca_o_nome_da_totvs(self, post, *_):
        # caso real: sacola 12135909, a TOTVS gravou o nome e o clique de quem estava com a tela velha escreveu por cima
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 434, "status": 4, "transactionDate": HOJE}]}
        with mock.patch("rotas.totvs.dados_da_nf", return_value=("CARLOS PEREIRA", 2)):
            totvs.sincronizar()
        acoes.atender(self.joao, Documento.objects.get(pk=self.doc.pk), True)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.atendido_por, "CARLOS PEREIRA")

    def test_loja_com_erro_nao_trava_as_outras(self, post, *_):
        outra = acoes.criar_rota(self.joao, timezone.localdate(), "BAIXADA I", ["CAXIAS I"], False).paradas.get(loja="CAXIAS I")
        outra.pes = "337"
        outra.save()
        acoes.editar_documento(self.joao, outra, "t434", "24")
        ok = {"items": [{"transactionCode": 24, "operationCode": 434, "status": 1, "transactionDate": HOJE}]}
        post.side_effect = lambda url, corpo, tk: ok if corpo["customerCodeList"] == [100000337] else (_ for _ in ()).throw(OSError("timeout"))
        with self.assertLogs("rotas.totvs", "ERROR"):
            self.assertEqual(totvs.sincronizar(), 1)
        self.assertEqual(outra.documentos.get().situacao_totvs, "Em andamento")

    def test_sem_pes_ou_numero_invalido_nao_consulta(self, post, *_):
        self.parada.pes = ""
        self.parada.save()
        self.assertEqual(totvs.sincronizar(), 0)
        self.parada.pes = "123"
        self.parada.save()
        Documento.objects.filter(pk=self.doc.pk).update(numero="23A")
        self.assertEqual(totvs.sincronizar(), 0)
        post.assert_not_called()


class SemConfigTest(TestCase):
    def test_desligada_sem_as_variaveis(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(totvs.config())
            self.assertEqual(totvs.sincronizar(), 0)

    @mock.patch("rotas.totvs.sincronizar", side_effect=OSError("fora do ar"))
    def test_erro_nao_derruba_o_ciclo(self, _):
        with self.assertLogs("rotas.totvs", "ERROR"):
            totvs.ciclo()  # só registra no log


@mock.patch("rotas.totvs._post", return_value={"access_token": "novo", "expires_in": 86400})
class TokenTest(TestCase):
    def test_reaproveita_o_token_ate_perto_de_vencer(self, post):
        totvs._token.update(valor="", vence=0)
        self.assertEqual(totvs.token(CONFIG), "novo")
        self.assertEqual(totvs.token(CONFIG), "novo")
        post.assert_called_once()
        url, corpo = post.call_args.args[:2]
        self.assertEqual(url, "https://totvs/api/totvsmoda/authorization/v2/token")
        self.assertEqual(corpo["grant_type"], "password")  # client_credentials não pode buscar transações neste ambiente


class TokenRecusadoTest(SimpleTestCase):
    @mock.patch("rotas.totvs.urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 401, "x", {}, None))
    def test_401_pede_token_novo(self, _):
        totvs._token.update(valor="velho", vence=time.time() + 3600)
        with self.assertRaises(urllib.error.HTTPError):
            totvs._post("https://totvs/x", {}, "velho")
        self.assertEqual(totvs._token["vence"], 0.0)


class NfForaDoArTest(SimpleTestCase):
    @mock.patch("rotas.totvs.token", return_value="tk")
    @mock.patch("rotas.totvs._post", side_effect=TimeoutError("lenta"))
    def test_timeout_na_nf_nao_impede_confirmar(self, *_):
        t = {"transactionCode": 1, "relatedFiscalDocument": {"branchCode": 209, "invoiceCode": 1, "issueDate": "2026-10-26"}}
        with self.assertLogs("rotas.totvs", "ERROR"):
            self.assertEqual(totvs.dados_da_nf(CONFIG, t), (None, None))


class PedidoTest(SimpleTestCase):
    @mock.patch("rotas.totvs.urllib.request.urlopen")
    def test_pedido_se_identifica(self, urlopen):
        urlopen.return_value.__enter__.return_value = io.BytesIO(b'{"items": []}')
        totvs._post("https://totvs/x", {"a": 1}, "tk")
        req = urlopen.call_args.args[0]
        self.assertTrue(req.get_header("User-agent"))  # sem nome, o Cloudflare da TOTVS recusa (erro 1010)
        self.assertEqual(req.get_header("Authorization"), "Bearer tk")


@mock.patch("rotas.totvs.config", return_value=CONFIG)
@mock.patch("rotas.totvs.token", return_value="tk")
@mock.patch("rotas.totvs._post")
class PuxarTest(TestCase):
    """A transação criada no dia D entra sozinha na próxima rota da loja depois de D."""

    def setUp(self):
        from datetime import timedelta
        self.joao = User.objects.create_user("joao")
        hoje = timezone.localdate()
        self.dia = {k: hoje + timedelta(days=k) for k in (-1, 0, 1)}
        self.hoje = acoes.criar_rota(self.joao, hoje, "R. LAGOS", ["BÚZIOS"], False)
        self.amanha = acoes.criar_rota(self.joao, self.dia[1], "R. LAGOS", ["BÚZIOS"], False)
        for r in (self.hoje, self.amanha):
            r.paradas.filter(loja="BÚZIOS").update(pes="459")

    def responde(self, post, por_dia):
        post.side_effect = lambda url, corpo, tk: {"items": [{"transactionCode": n, "operationCode": op, "status": st} for n, op, st in por_dia.get(corpo["transactionDate"], [])]}

    def numeros(self, rota, coluna):
        return list(Documento.objects.filter(parada__rota=rota, tipo__codigo=coluna).values_list("numero", flat=True))

    def test_de_ontem_vai_para_hoje_e_de_hoje_vai_para_amanha(self, post, *_):
        self.responde(post, {self.dia[-1].isoformat(): [(36, 434, 4), (37, 3386, 4)], self.dia[0].isoformat(): [(38, 434, 1), (12133251, 4437, 1)]})
        self.assertEqual(totvs.puxar(), 4)
        self.assertEqual((self.numeros(self.hoje, "t434"), self.numeros(self.hoje, "t3386")), (["36"], ["37"]))
        self.assertEqual((self.numeros(self.amanha, "t434"), self.numeros(self.amanha, "sacola")), (["38"], ["12133251"]))
        self.assertEqual(post.call_args.args[1]["customerCodeList"], [100000459])
        self.assertTrue(self.amanha.historico.filter(acao__startswith="TOTVS lançou 434 38").exists())
        self.assertEqual(totvs.puxar(), 0)  # já lançadas: não repete

    def test_cancelada_nao_entra_e_apagada_a_mao_nao_volta(self, post, *_):
        self.responde(post, {self.dia[0].isoformat(): [(38, 434, 1), (39, 434, 6)]})
        totvs.puxar()
        self.assertEqual(self.numeros(self.amanha, "t434"), ["38"])  # a 39 está cancelada
        acoes.editar_documento(self.joao, self.amanha.paradas.get(loja="BÚZIOS"), "t434", "")
        self.assertEqual(totvs.puxar(), 0)
        self.assertEqual(self.numeros(self.amanha, "t434"), [])

    def test_numero_ja_lancado_em_outra_rota_da_loja_nao_duplica(self, post, *_):
        acoes.editar_documento(self.joao, self.hoje.paradas.get(loja="BÚZIOS"), "t434", "38")
        self.responde(post, {self.dia[0].isoformat(): [(38, 434, 1)]})
        self.assertEqual(totvs.puxar(), 0)
        self.assertEqual(self.numeros(self.amanha, "t434"), [])


class OperadorTest(TestCase):
    """Quem atendeu na TOTVS: o operador da NF que a transação gerou, com o nome da lista de usuários."""

    def setUp(self):
        totvs._usuarios.update(nomes={}, vence=0)

    @mock.patch("rotas.totvs.token", return_value="tk")
    @mock.patch("rotas.totvs._get", return_value={"items": [{"code": 7, "name": "BEATRIZ DE SOUZA LIMA SANTOS (EXPEDICAO ADM)"},
                                                            {"code": 8, "name": "PEDRO ALVES SOUZA"}], "hasNext": False})
    @mock.patch("rotas.totvs._post")
    def test_nome_de_quem_faturou_a_nf_da_transacao(self, post, get, _):
        # quem faturou a caixa: o operador da NF desta transação (o número da NF se repete com os anos)
        post.return_value = {"items": [{"transactionCode": 1682, "userCode": 8, "shippingCompany": {"packageNumber": 9}},
                                       {"transactionCode": 107, "userCode": 7, "shippingCompany": {"packageNumber": 4}}]}
        t = {"transactionCode": 107, "relatedFiscalDocument": {"branchCode": 209, "invoiceCode": 85639, "issueDate": "2026-10-26"}}
        self.assertEqual(totvs.dados_da_nf(CONFIG, t), ("BEATRIZ SANTOS", 4))
        self.assertEqual(post.call_args.args[1]["filter"], {"branchCodeList": [209], "invoiceCodeList": [85639],
                                                            "startIssueDate": "2026-10-26T00:00:00", "endIssueDate": "2026-10-26T23:59:59"})
        post.return_value = {"items": [{"transactionCode": 1682, "userCode": 8}]}
        self.assertEqual(totvs.dados_da_nf(CONFIG, t), (None, None))  # só nota de outra transação: não chuta
        self.assertEqual(totvs.dados_da_nf(CONFIG, {}), (None, None))  # transação sem NF

    @mock.patch("rotas.totvs.token", return_value="tk")
    @mock.patch("rotas.totvs._post")
    def test_sem_permissao_fica_totvs(self, post, _):
        import urllib.error
        post.side_effect = urllib.error.HTTPError("u", 400, "empresa não permitida", {}, io.BytesIO(b""))
        with self.assertLogs("rotas.totvs", "WARNING"):
            self.assertEqual(totvs.dados_da_nf(CONFIG, {"transactionCode": 1, "relatedFiscalDocument": {"branchCode": 209, "invoiceCode": 1, "issueDate": "2026-10-26"}}),
                             (None, None))

    @mock.patch("rotas.totvs.config", return_value=CONFIG)
    @mock.patch("rotas.totvs.token", return_value="tk")
    @mock.patch("rotas.totvs.dados_da_nf", return_value=("BEATRIZ SANTOS", 4))
    @mock.patch("rotas.totvs._post")
    def test_atendida_mostra_quem_atendeu(self, post, *_):
        joao = User.objects.create_user("joao")
        rota = acoes.criar_rota(joao, timezone.localdate(), "BAIXADA II", ["PIABETÁ"], False)
        parada = rota.paradas.get(loja="PIABETÁ")
        parada.pes = "123"
        parada.save()
        doc = acoes.editar_documento(joao, parada, "t434", "23")
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 434, "status": 4, "transactionDate": HOJE}]}
        totvs.sincronizar()
        doc.refresh_from_db()
        self.assertEqual((doc.atendido_por, doc.volumes), ("BEATRIZ SANTOS", 4))
        self.assertTrue(rota.historico.filter(acao="TOTVS informou 434 23 de PIABETÁ: Atendida por BEATRIZ SANTOS").exists())

    @mock.patch("rotas.totvs.config", return_value=CONFIG)
    @mock.patch("rotas.totvs.token", return_value="tk")
    @mock.patch("rotas.totvs.dados_da_nf", return_value=(None, None))
    @mock.patch("rotas.totvs._post")
    def test_nf_que_aparece_depois_troca_totvs_por_quem_atendeu(self, post, nf, *_):
        joao = User.objects.create_user("joao")
        rota = acoes.criar_rota(joao, timezone.localdate(), "BAIXADA II", ["PIABETÁ"], False)
        parada = rota.paradas.get(loja="PIABETÁ")
        parada.pes = "123"
        parada.save()
        doc = acoes.editar_documento(joao, parada, "t434", "23")
        post.return_value = {"items": [{"transactionCode": 23, "operationCode": 434, "status": 4, "transactionDate": HOJE}]}
        totvs.sincronizar()  # atendida, mas a NF ainda não aparece na busca
        doc.refresh_from_db()
        self.assertEqual(doc.atendido_por, "TOTVS")
        nf.return_value = ("BEATRIZ SANTOS", 4)
        self.assertEqual(totvs.sincronizar(), 1)
        doc.refresh_from_db()
        self.assertEqual((doc.atendido_por, doc.volumes), ("BEATRIZ SANTOS", 4))
        self.assertTrue(rota.historico.filter(acao="TOTVS informou quem atendeu 434 23 de PIABETÁ: BEATRIZ SANTOS").exists())
        post.reset_mock()
        totvs.sincronizar()
        post.assert_not_called()  # com o nome: não consulta mais
        Documento.objects.filter(pk=doc.pk).update(atendido_por="TOTVS", atendido_em=timezone.now() - totvs.RETENTAR)
        totvs.sincronizar()
        post.assert_not_called()  # passou de 2 h sem achar a NF: desiste


@mock.patch("rotas.totvs.config", return_value=CONFIG)
@mock.patch("rotas.totvs.token", return_value="tk")
@mock.patch("rotas.totvs._post")
class PuxarRotaQueJaSaiuTest(TestCase):
    def test_transacao_cuja_rota_ja_saiu_nao_vai_para_a_proxima(self, post, *_):
        # transação de 3 dias atrás: a rota dela foi a de 2 dias atrás (já saiu), e não a de amanhã
        from datetime import timedelta
        joao, hoje = User.objects.create_user("joao"), timezone.localdate()
        passada = acoes.criar_rota(joao, hoje - timedelta(days=2), "CENTRO E Z. SUL", ["BONSUCESSO II"], False)
        amanha = acoes.criar_rota(joao, hoje + timedelta(days=1), "CENTRO E Z. SUL", ["BONSUCESSO II"], False)
        for r in (passada, amanha):
            r.paradas.update(pes="414")
        tres_dias = (hoje - timedelta(days=3)).isoformat()
        post.side_effect = lambda url, corpo, tk: {"items": [{"transactionCode": 153, "operationCode": 434, "status": 4}] if corpo["transactionDate"] == tres_dias else []}
        self.assertEqual(totvs.puxar(), 0)
        self.assertFalse(Documento.objects.filter(parada__rota=amanha).exists())

    def test_transacao_muito_antes_da_rota_nao_entra(self, post, *_):
        # a rota em que ela foi não está no sistema: a de amanhã é 4 dias depois, longe demais
        from datetime import timedelta
        joao, hoje = User.objects.create_user("joao"), timezone.localdate()
        amanha = acoes.criar_rota(joao, hoje + timedelta(days=1), "CENTRO E Z. SUL", ["BONSUCESSO II"], False)
        amanha.paradas.update(pes="414")
        tres_dias = (hoje - timedelta(days=3)).isoformat()
        post.side_effect = lambda url, corpo, tk: {"items": [{"transactionCode": 153, "operationCode": 434, "status": 4}] if corpo["transactionDate"] == tres_dias else []}
        self.assertEqual(totvs.puxar(), 0)


@mock.patch("rotas.totvs.config", return_value=CONFIG)
@mock.patch("rotas.totvs.token", return_value="tk")
@mock.patch("rotas.totvs.dados_da_nf", return_value=("BEATRIZ SANTOS", 1))
@mock.patch("rotas.totvs._post")
class EntregaTest(TestCase):
    def setUp(self):
        joao = User.objects.create_user("joao")
        rota = acoes.criar_rota(joao, timezone.localdate(), "BAIXADA II", ["PIABETÁ"], False)
        parada = rota.paradas.get(loja="PIABETÁ")
        parada.pes = "538"
        parada.save()
        acoes.editar_documento(joao, parada, "t434", "805 806")
        self.docs = list(Documento.objects.select_related("tipo", "parada__rota").order_by("id"))

    def test_folha_com_os_dados_da_transacao_e_da_nf(self, post, *_):
        post.return_value = {"items": [{"transactionCode": 805, "operationCode": 434, "status": 4, "transactionDate": HOJE,
                                        "relatedFiscalDocument": {"invoiceCode": 85784, "quantity": 6}}]}
        folha, sem = totvs.entrega(self.docs)
        self.assertEqual(folha, {"loja": "PIABETÁ", "pessoa": 100000538, "operacao": "434", "numero": "805", "data": HOJE[:10],
                                 "nf": 85784, "usuario": "BEATRIZ SANTOS", "quantidade": 6, "caixa": 1})
        self.assertEqual(sem["erro"], "transação não encontrada na TOTVS")

    def test_totvs_fora_do_ar_avisa_na_folha(self, post, *_):
        post.side_effect = urllib.error.URLError("timeout")
        with self.assertLogs("rotas.totvs", "ERROR"):
            self.assertEqual([f["erro"] for f in totvs.entrega(self.docs)], ["a TOTVS não respondeu"] * 2)
