from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from rotas import acoes, pedidos, totvs
from rotas.models import Documento

VOLUMES = [{"PEDIDO": "704835", "COD_CLIENTE": "100000457", "NR_VOL": "1"}, {"PEDIDO": "704922", "COD_CLIENTE": "100000457", "NR_VOL": "2"},
           {"PEDIDO": "705016", "COD_CLIENTE": "100000457", "NR_VOL": "3"}, {"PEDIDO": "705016", "COD_CLIENTE": "100000457", "NR_VOL": "4"}]


def situacoes(**por_pedido):
    return {"items": [{"orderCode": int(p[1:]), "customerCode": 100000457, "statusOrder": s} for p, s in por_pedido.items()]
            + [{"orderCode": 704835, "customerCode": 100000359, "statusOrder": "Canceled"}]}  # mesmo número, outra loja: não conta


@mock.patch("rotas.totvs.config", return_value={"URL": "https://totvs", "EMPRESA": "1,209"})
@mock.patch("rotas.alcis.config", return_value={"USUARIO": "u", "SENHA": "s"})
@mock.patch("rotas.totvs.token", return_value="tk")
@mock.patch("rotas.alcis.baixar_carga", return_value=VOLUMES)
@mock.patch("rotas.totvs._post")
class ConferirPedidosTest(TestCase):
    def setUp(self):
        pedidos._volumes.clear()  # volumes da Alcis guardados entre rodadas: cada teste começa sem
        joao = User.objects.create_user("joao")
        rota = acoes.criar_rota(joao, timezone.localdate(), "BAIXADA I + R. SERRANA", ["N. IGUAÇU II"], False)
        self.parada = rota.paradas.get(loja="N. IGUAÇU II")
        self.carga = acoes.editar_documento(joao, self.parada, "wms", "3656")
        self.joao = joao

    def recarregar(self):
        self.carga.refresh_from_db()
        return self.carga

    def test_carga_criada_com_pedidos_bloqueados_fica_como_esta(self, post, *_):
        post.return_value = situacoes(p704835="Blocked", p704922="Blocked", p705016="Blocked")
        self.assertEqual(pedidos.conferir(), 0)
        c = self.recarregar()
        self.assertEqual((c.expedida_em, c.aviso_pedidos, c.volumes), (None, "", 4))

    def test_carga_nao_se_expede_sozinha(self, post, *_):
        post.return_value = situacoes(p704835="Attended", p704922="InProgress", p705016="InProgress")
        self.assertEqual(pedidos.conferir(), 0)
        c = self.recarregar()
        self.assertEqual((c.expedida_em, c.concluido_em, c.aviso_pedidos, c.pedidos_fase), (None, None, "", ""))

    def test_tudo_atendido_no_099_marca_a_carga_atendida(self, post, *_):
        acoes.avancar_carga(self.joao, self.carga)  # expedida
        post.return_value = situacoes(p704835="Attended", p704922="Attended", p705016="Attended")
        pedidos.conferir()
        c = self.recarregar()
        self.assertEqual((c.expedida_por, c.concluido_por, c.pedidos_fase), ("JOAO", "TOTVS", "atendida"))  # sem NF: fica "TOTVS"

    @mock.patch("rotas.totvs.dados_da_nf", return_value=("CARLOS PEREIRA", 25))
    def test_atendida_no_099_mostra_quem_faturou(self, nf, post, *_):
        acoes.avancar_carga(self.joao, self.carga)
        post.return_value = situacoes(p704835="Attended", p704922="Attended", p705016="Attended")
        nota = lambda n, dia: {"code": n, "issueDate": dia, "transactionBranchCode": 209, "transactionCode": n - 85000}
        post.return_value["items"][0]["invoices"] = [nota(85600, "2026-10-26")]
        post.return_value["items"][1]["invoices"] = [nota(85691, "2026-10-27")]
        pedidos.conferir()
        self.assertEqual(self.recarregar().concluido_por, "CARLOS PEREIRA")
        t = nf.call_args.args[1]  # a NF mais recente
        self.assertEqual((t["transactionCode"], t["relatedFiscalDocument"]), (691, {"branchCode": 209, "invoiceCode": 85691, "issueDate": "2026-10-27"}))

    def test_carga_atendida_com_pedido_em_andamento_avisa(self, post, *_):
        for _ in range(3):
            acoes.avancar_carga(self.joao, self.carga)  # atendida à mão
        post.return_value = situacoes(p704835="Attended", p704922="InProgress", p705016="InProgress")
        pedidos.conferir()
        self.assertEqual(self.recarregar().aviso_pedidos, "2 em andamento")

    def test_depois_de_expedida_avisa_bloqueado_e_cancelado(self, post, *_):
        acoes.avancar_carga(self.joao, self.carga)  # expedida
        post.return_value = situacoes(p704835="Blocked", p704922="InProgress", p705016="Canceled")
        self.assertEqual(pedidos.conferir(), 1)
        c = self.recarregar()
        self.assertEqual((c.aviso_pedidos, c.pedidos_detalhe, c.pedidos_fase),
                         ("1 bloqueado, 1 cancelado", "Em andamento: 704922; Bloqueado: 704835; Cancelado: 705016", "expedida"))
        self.assertEqual(post.call_args.args[1]["filter"], {"branchCodeList": [209], "orderCodeList": [704835, 704922, 705016]})
        self.assertTrue(self.parada.rota.historico.filter(acao__contains="pedidos da carga 3656 de N. IGUAÇU II: 1 bloqueado, 1 cancelado").exists())
        self.assertEqual(pedidos.conferir(), 0)  # nada mudou: não grava de novo

    def test_rodada_seguinte_so_consulta_a_totvs(self, post, baixar, *_):
        acoes.avancar_carga(self.joao, self.carga)
        post.return_value = situacoes(p704835="InProgress", p704922="InProgress", p705016="InProgress")
        pedidos.conferir()
        baixar.reset_mock()
        post.return_value = situacoes(p704835="Attended", p704922="Attended", p705016="Attended")
        pedidos.conferir()
        baixar.assert_not_called()  # pedidos da carga: os da Alcis já baixados (de novo só a cada ALCIS_A_CADA, em segundo plano)
        self.assertEqual(self.recarregar().pedidos_fase, "atendida")

    def test_depois_do_099_aviso_final_e_para_de_conferir(self, post, baixar, *_):
        acoes.avancar_carga(self.joao, self.carga)
        post.return_value = situacoes(p704835="Attended", p704922="PartiallyAnswered", p705016="PartiallyAnswered")
        pedidos.conferir()
        c = self.recarregar()
        self.assertEqual((c.aviso_pedidos, c.pedidos_fase), ("2 parcialmente atendidos", "atendida"))
        Documento.objects.filter(pk=c.pk).update(concluido_por="CARLOS PEREIRA")
        baixar.reset_mock()
        pedidos.conferir()
        baixar.assert_not_called()  # atendida no 099, com o nome: não consulta mais

    def test_nf_que_sai_depois_do_099_troca_totvs_pelo_nome(self, post, baixar, *_):
        # caso real: carga 3677, atendida no 099 às 09:25:18 e NF às 09:27:12
        acoes.avancar_carga(self.joao, self.carga)
        post.return_value = situacoes(p704835="Attended", p704922="Attended", p705016="Attended")
        pedidos.conferir()
        self.assertEqual(self.recarregar().concluido_por, "TOTVS")
        post.return_value["items"][0]["invoices"] = [{"code": 85692, "issueDate": "2026-10-27", "transactionBranchCode": 209, "transactionCode": 90}]
        with mock.patch("rotas.totvs.dados_da_nf", return_value=("CARLOS PEREIRA", 25)):
            self.assertEqual(pedidos.conferir(), 1)
        self.assertEqual(self.recarregar().concluido_por, "CARLOS PEREIRA")
        self.assertTrue(self.parada.rota.historico.filter(acao="TOTVS informou quem atendeu a carga 3656 de N. IGUAÇU II: CARLOS PEREIRA").exists())
        baixar.reset_mock()
        pedidos.conferir()
        baixar.assert_not_called()
        Documento.objects.filter(pk=self.carga.pk).update(concluido_por="TOTVS", concluido_em=timezone.now() - totvs.RETENTAR)
        pedidos.conferir()
        baixar.assert_not_called()  # 2 h sem NF: desiste

    def test_concluida_a_mao_fica_com_o_nome_da_totvs(self, post, *_):
        for _ in range(3):
            acoes.avancar_carga(self.joao, self.carga)  # concluída no sistema antes do 099
        post.return_value = situacoes(p704835="Attended", p704922="Attended", p705016="Attended")
        pedidos.conferir()
        c = self.recarregar()
        self.assertEqual((c.concluido_por, c.pedidos_fase), ("TOTVS", "atendida"))  # NF ainda não saiu
        post.return_value["items"][0]["invoices"] = [{"code": 85692, "issueDate": "2026-10-27", "transactionBranchCode": 209, "transactionCode": 90}]
        with mock.patch("rotas.totvs.dados_da_nf", return_value=("CARLOS PEREIRA", 25)):
            pedidos.conferir()
        self.assertEqual(self.recarregar().concluido_por, "CARLOS PEREIRA")

    def test_tudo_atendido_sem_aviso(self, post, *_):
        acoes.avancar_carga(self.joao, self.carga)
        post.return_value = situacoes(p704835="Attended", p704922="Attended", p705016="Attended")
        pedidos.conferir()
        c = self.recarregar()
        self.assertEqual((c.aviso_pedidos, c.pedidos_fase), ("", "atendida"))

    def test_pedido_fora_da_totvs_nao_deixa_marcar_atendida(self, post, *_):
        acoes.avancar_carga(self.joao, self.carga)
        post.return_value = situacoes(p704835="Attended", p704922="Attended")  # 705016 está na Alcis, mas não na TOTVS
        pedidos.conferir()
        c = self.recarregar()
        self.assertEqual((c.concluido_em, c.aviso_pedidos, c.pedidos_fase), (None, "1 fora da TOTVS", "expedida"))
        self.assertIn("Fora da TOTVS: 705016", c.pedidos_detalhe)

    def test_trocar_o_numero_da_carga_limpa_o_aviso(self, post, *_):
        Documento.objects.filter(pk=self.carga.pk).update(aviso_pedidos="1 bloqueado", pedidos_fase="expedida")
        c = acoes.editar_documento(self.joao, self.parada, "wms", "3657")
        self.assertEqual((c.aviso_pedidos, c.pedidos_fase), ("", ""))
