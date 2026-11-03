from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from rotas import acoes, checklist

D = date(2026, 10, 27)


def loja(itens, finalizado=True, lugar="BAIXADA I / QUEIMADOS"):
    return {"lugar": lugar, "finalizado": finalizado, "itens": itens}


class CompararTest(SimpleTestCase):
    def test_tudo_igual_nao_tem_diferenca(self):
        ck = {(D, "508"): loja({("293", "TR_434"): 2, ("12135566", "ALMOXARIFADO"): 3, ("3655", "WMS"): 22})}
        nosso = {(D, "508"): {("293", "t434"): 2, ("12135566", "sacola"): 3, ("3655", "wms"): 22}}
        self.assertEqual(checklist.comparar(ck, nosso), [])

    def test_casos_reais_de_27_10(self):
        ck = {(D, "508"): loja({("12135556", "ALMOXARIFADO"): 3}),  # sacola digitada errada (a certa é 12135566)
              (D, "481"): loja({("424", "TR_434"): 11}, lugar="BAIXADA I / MESQUITA"),  # na TOTVS a 424 é 3386
              (D, "428"): loja({("502", "TR_3386"): 38}, lugar="BAIXADA II / OUTLET")}
        nosso = {(D, "508"): {("12135566", "sacola"): 3}, (D, "481"): {("424", "t3386"): 11}, (D, "428"): {("502", "t3386"): 1}}
        self.assertEqual(checklist.comparar(ck, nosso), [
            "27/10 BAIXADA II / OUTLET (PES 428): 3386 502 com 38 volume(s) no checklist e 1 no sistema",
            "27/10 BAIXADA I / MESQUITA (PES 481): 434 424 só no checklist (no sistema está como 3386)",
            "27/10 BAIXADA I / QUEIMADOS (PES 508): Almox/Sacola 12135556 só no checklist",
            "27/10 BAIXADA I / QUEIMADOS (PES 508): Sacola 12135566 só no sistema (checklist finalizado sem ele)",
        ])

    def test_checklist_em_andamento_nao_cobra_o_que_falta(self):
        ck = {(D, "225"): loja({}, finalizado=False, lugar="N. FLUMINENSE / CAMPOS I")}
        self.assertEqual(checklist.comparar(ck, {(D, "225"): {("29", "t3386"): None}}), [])

    def test_loja_sem_checklist_nao_entra(self):
        self.assertEqual(checklist.comparar({}, {(D, "225"): {("29", "t3386"): 3}}), [])

    def test_data_de_entrega_em_utc(self):
        self.assertEqual(checklist._entrega("2026-10-28T03:00:00.000Z"), date(2026, 10, 28))


class ConferirTest(TestCase):
    def test_escreve_so_no_arquivo_e_uma_vez(self):
        joao = User.objects.create_user("joao")
        rota = acoes.criar_rota(joao, timezone.localdate(), "BAIXADA II", ["PIABETÁ"], False)
        parada = rota.paradas.get(loja="PIABETÁ")
        parada.pes = "437"
        parada.save()
        acoes.editar_documento(joao, parada, "t434", "23")
        ck = {(timezone.localdate(), "437"): loja({("24", "TR_434"): 1}, finalizado=False, lugar="BAIXADA II / PIABETÁ")}
        historico_antes = rota.historico.count()
        with TemporaryDirectory() as pasta, override_settings(DADOS=Path(pasta)), \
                mock.patch("rotas.checklist.config", return_value={"URL": "u", "USUARIO": "u", "SENHA": "s"}), \
                mock.patch("rotas.checklist.ler_checklists", return_value=ck):
            checklist._avisados.clear()
            self.assertEqual(checklist.conferir(), 1)
            self.assertEqual(checklist.conferir(), 0)  # a mesma diferença não repete
            texto = (Path(pasta) / "conferencia_checklist.log").read_text(encoding="utf-8")
        self.assertIn("PIABETÁ (PES 437): 434 24 só no checklist", texto)
        self.assertEqual(rota.historico.count(), historico_antes)  # nada no sistema: nem histórico, nem tela
        self.assertEqual(list(parada.documentos.values_list("numero", flat=True)), ["23"])
