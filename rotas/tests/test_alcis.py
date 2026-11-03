from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from rotas import acoes, alcis
from rotas.models import Documento

CONFIG = {"USUARIO": "u", "SENHA": "s"}
HOJE = timezone.localdate()


def volumes(*cods):
    return [{"CARGA": "x", "COD_CLIENTE": c, "CLIENTE": "LOJA X", "ROTA": "R"} for c in cods]


@mock.patch("rotas.alcis.config", return_value=CONFIG)
@mock.patch("rotas.alcis.baixar_carga")
class SincronizarTest(TestCase):
    def setUp(self):
        alcis._sem_rota.clear()
        self.joao = User.objects.create_user("joao")
        self.hoje = acoes.criar_rota(self.joao, HOJE, "R. LAGOS", ["BÚZIOS II", "BÚZIOS"], False)
        self.amanha = acoes.criar_rota(self.joao, HOJE + timedelta(days=1), "R. LAGOS", ["BÚZIOS II"], False)
        for r in (self.hoje, self.amanha):
            r.paradas.filter(loja="BÚZIOS II").update(pes="556")
            r.paradas.filter(loja="BÚZIOS").update(pes="459")
        acoes.editar_documento(self.joao, self.hoje.paradas.get(loja="BÚZIOS"), "wms", "3650")  # maior carga já lançada

    def cargas(self, rota, loja):
        return list(Documento.objects.filter(parada__rota=rota, parada__loja=loja, tipo__eh_carga=True).values_list("numero", flat=True))

    def test_carga_nova_entra_na_proxima_rota_sem_carga_da_loja(self, baixar, _):
        baixar.side_effect = lambda n: volumes("100000556") if n == 3651 else []
        self.assertEqual(alcis.sincronizar(), 1)
        self.assertEqual(self.cargas(self.hoje, "BÚZIOS II"), ["3651"])
        self.assertTrue(self.hoje.historico.filter(acao__startswith="ALCIS lançou Carga WMS 3651").exists())

    def test_loja_com_carga_hoje_vai_para_a_proxima_rota(self, baixar, _):
        acoes.editar_documento(self.joao, self.hoje.paradas.get(loja="BÚZIOS II"), "wms", "3649")
        baixar.side_effect = lambda n: volumes("100000556") if n == 3651 else []
        alcis.sincronizar()
        self.assertEqual(self.cargas(self.amanha, "BÚZIOS II"), ["3651"])

    def test_duas_cargas_da_mesma_loja_juntas_vao_para_a_mesma_rota(self, baixar, _):
        baixar.side_effect = lambda n: volumes("100000556") if n in (3651, 3652) else []
        alcis.sincronizar()
        self.assertEqual(self.cargas(self.hoje, "BÚZIOS II"), ["3651", "3652"])
        self.assertEqual(self.cargas(self.amanha, "BÚZIOS II"), [])

    def test_carga_ja_lancada_ou_de_loja_fora_das_rotas_nao_entra(self, baixar, _):
        baixar.side_effect = lambda n: {3650: volumes("100000459"), 3651: volumes("100000999")}.get(n, [])
        self.assertEqual(alcis.sincronizar(), 0)
        self.assertNotIn(3650, [c.args[0] for c in baixar.call_args_list])  # já está no sistema: nem consulta
        baixar.reset_mock()
        alcis.sincronizar()
        self.assertNotIn(3651, [c.args[0] for c in baixar.call_args_list])  # loja fora das rotas: não consulta de novo

    def test_carga_sem_rota_entra_quando_a_rota_e_criada(self, baixar, _):
        baixar.side_effect = lambda n: volumes("100000441") if n == 3651 else []
        self.assertEqual(alcis.sincronizar(), 0)  # S. P. ALDEIA sem rota: espera
        r = acoes.criar_rota(self.joao, HOJE + timedelta(days=2), "R. LAGOS", ["S. P. ALDEIA"], False)
        r.paradas.filter(loja="S. P. ALDEIA").update(pes="441")
        self.assertEqual(alcis.sincronizar(), 1)
        self.assertEqual(self.cargas(r, "S. P. ALDEIA"), ["3651"])

    def test_sem_carga_lancada_ou_sem_configuracao_nao_faz_nada(self, baixar, config):
        Documento.objects.filter(tipo__eh_carga=True).delete()
        self.assertEqual(alcis.sincronizar(), 0)  # sem carga de referência, não sabe por onde começar
        config.return_value = None
        self.assertEqual(alcis.sincronizar(), 0)
        baixar.assert_not_called()


class PortalTest(SimpleTestCase):
    def test_le_o_csv_do_relatorio(self):
        csv = "﻿CARGA;ROTA;COD_CLIENTE;CLIENTE;PEDIDO\r\n3656;BAIXADA 1;100000457;LOJA NOVA IGUACU 2;123\r\n"
        self.assertEqual(alcis.ler_csv(csv.encode("utf-8")), [{"CARGA": "3656", "ROTA": "BAIXADA 1", "COD_CLIENTE": "100000457",
                                                               "CLIENTE": "LOJA NOVA IGUACU 2", "PEDIDO": "123"}])
        self.assertEqual(alcis.ler_csv("CARGA;ROTA\r\n".encode("cp1252")), [])

