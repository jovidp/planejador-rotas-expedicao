from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from rotas.models import Loja, RegraEntrega, RotaPadrao


class CadastroTest(TestCase):
    def test_importa_contagens(self):
        call_command("importar_cadastro", verbosity=0)
        call_command("importar_cadastro", verbosity=0)
        self.assertEqual(RotaPadrao.objects.count(), 12)
        self.assertEqual(Loja.objects.count(), 93)
        self.assertEqual(RegraEntrega.objects.count(), 33)
        b = Loja.objects.get(nome="BÚZIOS II")
        self.assertEqual((b.pes, b.emp, b.ramal, b.rota_padrao.nome), ("556", "330", "1824", "R. LAGOS"))
        w = Loja.objects.get(nome="WEST III")
        self.assertEqual((w.frete_accelo, w.frete_atego, w.celular_oi, w.ordem_na_rota), (Decimal("627.27"), Decimal("576.95"), "(21) 90000-0033", 3))
        # tabela Accelo/Atego de 23/10, valores como vieram (PARK Atego 678,96 está fora da conta KM×tarifa+fixo)
        self.assertEqual(Loja.objects.get(nome="PARK").frete_atego, Decimal("678.96"))
        self.assertEqual(Loja.objects.get(nome="BÚZIOS II").frete_accelo, Decimal("3121.44"))
        self.assertFalse(Loja.objects.filter(frete_accelo=None).exists())
        self.assertFalse(Loja.objects.filter(frete_atego=None).exists())
        self.assertIn("taquara plaza", RotaPadrao.objects.get(nome="Z. NORTE I").variacoes)
        self.assertEqual(RegraEntrega.objects.get(emp="230").horario_docas, "07:00 ÀS 19:00")


class EditarRotaDoCadastroTest(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        call_command("importar_cadastro", verbosity=0)
        self.client.force_login(User.objects.create_user("joao"))

    def salvar(self, rota, lojas):
        import json
        return self.client.post("/api/cadastro/rota/", json.dumps({"rota": rota, "lojas": lojas}), content_type="application/json")

    def lojas(self, rota):
        return list(Loja.objects.filter(rota_padrao__nome=rota).order_by("ordem_na_rota").values_list("nome", flat=True))

    def test_tirar_caxias_da_baixada_ii_e_mudar_a_ordem(self):
        r = self.salvar("BAIXADA II", ["OUTLET", "MAGÉ II", "PIABETÁ", "STA. CRUZ DA SERRA", "LOTE XV", "CAXIAS I"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.lojas("BAIXADA II"), ["OUTLET", "MAGÉ II", "PIABETÁ", "STA. CRUZ DA SERRA", "LOTE XV", "CAXIAS I"])
        caxias = Loja.objects.get(nome="CAXIAS CENTRO")
        self.assertIsNone(caxias.rota_padrao)  # sai da rota, mas continua no cadastro de lojas
        self.assertEqual(r.json()["rotas"]["BAIXADA II"]["lojas"][0], "OUTLET")
        from django.contrib.admin.models import LogEntry
        log = LogEntry.objects.get(object_repr="BAIXADA II")
        self.assertIn("tirou CAXIAS CENTRO", log.change_message)
        self.assertEqual(log.user.username, "joao")

    def test_loja_de_outra_rota_muda_para_esta(self):
        self.salvar("BAIXADA II", self.lojas("BAIXADA II") + ["búzios"])
        self.assertEqual(Loja.objects.get(nome="BÚZIOS").rota_padrao.nome, "BAIXADA II")
        self.assertNotIn("BÚZIOS", self.lojas("R. LAGOS"))

    def test_loja_ou_rota_fora_do_cadastro(self):
        self.assertEqual(self.salvar("BAIXADA II", ["LOJA QUE NAO EXISTE"]).status_code, 400)
        self.assertEqual(self.salvar("ROTA QUE NAO EXISTE", ["OUTLET"]).status_code, 400)
        self.assertIn("OUTLET", self.lojas("BAIXADA II"))  # nada mudou


class VeiculoRegistradoTest(TestCase):
    def test_salvar_e_remover_veiculo_ficam_no_registro(self):
        from django.contrib.admin.models import LogEntry
        from django.contrib.auth.models import User
        from rotas import acoes
        u = User.objects.create_user("joao")
        acoes.salvar_veiculo(u, "ABC1D23", "accelo")  # placa fora da frota inicial
        acoes.remover_veiculo(u, "ABC1D23")
        self.assertEqual(list(LogEntry.objects.order_by("id").values_list("action_flag", flat=True)), [1, 3])  # incluiu, apagou
