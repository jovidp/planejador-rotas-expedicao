from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase

from rotas import acoes, planejar
from rotas.models import Rota, RotaPadrao

SEG = date(2026, 11, 2)
ANTES = SEG - timedelta(days=7)


class CopiarSemanaTest(TestCase):
    def setUp(self):
        self.joao = User.objects.create_user("joao")
        RotaPadrao.objects.create(nome="R. LAGOS", saida="04:00")
        r = acoes.criar_rota(self.joao, ANTES + timedelta(days=1), "R. LAGOS", ["BÚZIOS"], False)
        acoes.editar_rota(self.joao, r, "motorista", "CARLOS")
        acoes.editar_rota(self.joao, r, "ajudantes", "ZÉ, TIÃO")
        acoes.criar_rota(self.joao, ANTES + timedelta(days=2), "ROTA AVULSA", ["BÚZIOS"], True)  # fora do cadastro: não copia

    def test_copia_as_rotas_do_cadastro_com_a_equipe(self):
        self.assertEqual(planejar.copiar_semana(SEG), 1)
        r = Rota.objects.get(data=SEG + timedelta(days=1))
        self.assertEqual((r.nome, r.motorista, r.ajudantes), ("R. LAGOS", "CARLOS", "ZÉ / TIÃO"))
        self.assertTrue(r.historico.filter(acao__startswith="PLANEJAMENTO AUTOMÁTICO").exists())
        self.assertEqual(planejar.copiar_semana(SEG), 0)  # já planejada: não copia de novo

    def test_semana_com_alguma_rota_nao_e_mexida(self):
        acoes.criar_rota(self.joao, SEG + timedelta(days=4), "R. LAGOS", ["BÚZIOS"], False)
        self.assertEqual(planejar.copiar_semana(SEG), 0)
        self.assertFalse(Rota.objects.filter(data=SEG + timedelta(days=1)).exists())
