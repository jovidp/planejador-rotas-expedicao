import json
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from rotas import acoes
from rotas.models import Loja, Rota, Veiculo


class FrotaTest(TestCase):
    def test_frota_ja_cadastrada(self):
        # lista do usuário em 23/10/2026
        self.assertEqual(dict(Veiculo.objects.values_list("placa", "tipo")), {
            "ATG1A01": "atego", "ATG2B02": "atego", "ATG3C03": "atego", "ABC1234": "atego", "ATG5E05": "atego",
            "ACL1A01": "accelo", "ACL2B02": "accelo", "ACL3C03": "accelo", "BRA2E19": "accelo",
            "ACL5E05": "accelo", "ACL6F06": "accelo", "ACL7G07": "accelo"})


class VeiculosApiTest(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("joao")
        self.client.force_login(self.u)

    def post(self, url, dados):
        return self.client.post(url, json.dumps(dados), content_type="application/json")

    def test_lista(self):
        d = self.client.get("/api/veiculos/").json()
        self.assertEqual(len(d["veiculos"]), 12)
        self.assertEqual(d["veiculos"][0], {"placa": "ABC1234", "tipo": "atego", "descricao": ""})

    def test_cadastrar_alterar_remover(self):
        r = self.post("/api/veiculos/", {"placa": "abc-1d23", "tipo": "atego", "descricao": "Atego novo"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Veiculo.objects.get(placa="ABC1D23").tipo, "atego")
        self.post("/api/veiculos/", {"placa": "ABC1D23", "tipo": "accelo", "descricao": "trocado"})
        self.assertEqual(Veiculo.objects.get(placa="ABC1D23").tipo, "accelo")  # mesma placa: altera
        self.post("/api/veiculos/remover/", {"placa": "ABC1D23"})
        self.assertFalse(Veiculo.objects.filter(placa="ABC1D23").exists())

    def test_validacao(self):
        self.assertEqual(self.post("/api/veiculos/", {"placa": "", "tipo": "atego"}).status_code, 400)
        self.assertEqual(self.post("/api/veiculos/", {"placa": "ABC1D23", "tipo": "carreta"}).status_code, 400)
        self.assertEqual(self.post("/api/veiculos/", {"placa": "AB1", "tipo": "atego"}).status_code, 400)

    def test_cadastrar_placa_recalcula_rotas_de_hoje_em_diante(self):
        Loja.objects.create(nome="PARK", pes="455", frete_accelo=Decimal("423.36"), frete_atego=Decimal("678.96"))
        hoje = acoes.criar_rota(self.u, date.today(), "Z. OESTE IV", ["PARK"], False)
        acoes.editar_rota(self.u, hoje, "veiculo", "ABC1D23")  # ainda sem cadastro: sem frete
        self.assertIsNone(hoje.paradas.get(loja="PARK").frete_envio)
        self.post("/api/veiculos/", {"placa": "ABC1D23", "tipo": "atego"})
        self.assertEqual(hoje.paradas.get(loja="PARK").frete_envio, Decimal("678.96"))
        self.assertEqual(Rota.objects.get(pk=hoje.pk).historico.last().acao.split(" ", 1)[1], "recalculou o frete para ATEGO (placa ABC1D23)")
