from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from rotas import acoes
from rotas.api_externa import resumo
from rotas.models import ChaveApi, Loja


class ApiExternaTest(TestCase):
    def setUp(self):
        ChaveApi.objects.create(nome="BI", resumo=resumo("segredo"))
        self.rota = acoes.criar_rota(User.objects.create_user("joao"), timezone.localdate(), "R. LAGOS", ["BÚZIOS"], False)

    def test_sem_chave_ou_chave_errada_recusa(self):
        self.assertEqual(self.client.get("/api/v1/rotas/").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/rotas/", HTTP_X_API_KEY="outra").status_code, 401)
        ChaveApi.objects.update(ativa=False)
        self.assertEqual(self.client.get("/api/v1/rotas/", HTTP_AUTHORIZATION="Bearer segredo").status_code, 401)

    def test_rotas_do_dia_com_chave(self):
        r = self.client.get("/api/v1/rotas/", HTTP_AUTHORIZATION="Bearer segredo")
        self.assertEqual(r.status_code, 200)
        rotas = r.json()["rotas"]
        self.assertEqual([x["nome"] for x in rotas], ["R. LAGOS"])
        self.assertNotIn("historico", rotas[0])
        self.assertIsNotNone(ChaveApi.objects.get().usada_em)
        self.assertEqual(self.client.get(f"/api/v1/rotas/{self.rota.pk}/", HTTP_X_API_KEY="segredo").json()["nome"], "R. LAGOS")
        self.assertEqual(self.client.get("/api/v1/rotas/?data=ontem", HTTP_X_API_KEY="segredo").status_code, 400)

    def test_so_leitura(self):
        self.assertEqual(self.client.post("/api/v1/rotas/", HTTP_X_API_KEY="segredo").status_code, 405)

    def test_lojas_com_nome_pes_emp(self):
        Loja.objects.create(nome="TESTE", pes="123", emp="45")
        lojas = {l["nome"]: l for l in self.client.get("/api/v1/lojas/", HTTP_X_API_KEY="segredo").json()["lojas"]}
        self.assertEqual((lojas["TESTE"]["pes"], lojas["TESTE"]["emp"]), ("123", "45"))
        self.assertEqual(self.client.get("/api/v1/lojas/").status_code, 401)
