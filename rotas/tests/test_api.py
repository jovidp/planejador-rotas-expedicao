import json
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

from rotas import acoes, totvs
from rotas.models import Historico, Rota


class AcessoTest(TestCase):
    def test_exige_login(self):
        r = self.client.get("/")
        self.assertRedirects(r, "/accounts/login/?next=/", fetch_redirect_response=False)

    def entrar(self, **extra):
        User.objects.create_user("joao", password="senha-de-teste")
        return self.client.post("/accounts/login/", {"username": "joao", "password": "senha-de-teste", **extra})

    def test_login_sem_manter_conectado_acaba_ao_fechar_o_navegador(self):
        self.assertEqual(self.entrar().status_code, 302)
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_login_mantendo_conectado_dura_30_dias(self):
        self.assertEqual(self.entrar(lembrar="on").status_code, 302)
        self.assertFalse(self.client.session.get_expire_at_browser_close())
        self.assertEqual(self.client.session.get_expiry_age(), 30 * 24 * 3600)

    def test_tela_de_login_tem_manter_conectado(self):
        self.assertContains(self.client.get("/accounts/login/"), 'name="lembrar"')

    def test_css_e_js_com_a_versao_no_endereco(self):
        self.client.force_login(User.objects.create_user("joao"))
        with self.settings(VERSAO="26/10/2026 7:10"):
            html = self.client.get("/").content.decode()
        self.assertIn("rotas/app.css?v=26/10/2026%207%3A10", html)
        self.assertIn("rotas/app.js?v=26/10/2026%207%3A10", html)

    def test_api_exige_login(self):
        self.assertIn(self.client.get("/api/dia/2026-10-23/").status_code, (302, 401))
        self.assertIn(self.client.post("/api/rota/", "{}", content_type="application/json").status_code, (302, 401, 403))


class ApiTest(TestCase):
    def setUp(self):
        self.joao = User.objects.create_user("joao", password="x")
        self.beatriz = User.objects.create_user("beatriz", password="x")
        self.client.force_login(self.joao)
        self.rota = acoes.criar_rota(self.joao, date(2026, 10, 23), "BAIXADA II", ["PIABETÁ"], False)
        self.piabeta = self.rota.paradas.get(loja="PIABETÁ")

    def post(self, url, dados=None):
        return self.client.post(url, json.dumps(dados or {}), content_type="application/json")

    def test_dia_traz_rotas_e_paradas(self):
        acoes.editar_documento(self.joao, self.piabeta, "t434", "182")
        d = self.client.get("/api/dia/2026-10-23/").json()
        self.assertEqual([r["nome"] for r in d["rotas"]], ["BAIXADA II"])
        p = d["rotas"][0]["paradas"][1]
        self.assertEqual((p["loja"], p["docs"]["t434"][0]["numero"]), ("PIABETÁ", "182"))
        self.assertEqual([t["codigo"] for t in d["tipos"]], ["t434", "t3386", "almox", "sacola", "wms"])
        self.assertEqual(d["ultimo"], Historico.objects.last().id)
        acoes.editar_documento(self.joao, self.piabeta, "t434", "182 190")  # duas transações 434 na mesma loja
        p = self.client.get("/api/dia/2026-10-23/").json()["rotas"][0]["paradas"][1]
        self.assertEqual([x["numero"] for x in p["docs"]["t434"]], ["182", "190"])

    def test_confirmar_retirada_pela_api(self):
        acoes.editar_parada(self.joao, self.piabeta, "rt", "5")
        r = self.post(f"/api/parada/{self.piabeta.id}/retirada/", {"campo": "rt", "sim": True})
        p = r.json()["rota"]["paradas"][1]
        self.assertEqual((p["rt"], p["rt_por"]), ("5", "JOAO"))

    def test_dia_conta_so_ativas(self):
        acoes.criar_rota(self.joao, date(2026, 10, 23), "MISSÃO", [], True)
        acoes.cancelar_rota(self.joao, self.rota)
        self.assertEqual(len(self.client.get("/api/dia/2026-10-23/").json()["rotas"]), 2)
        dias = self.client.get("/api/semana/2026-10-23/").json()["dias"]
        self.assertEqual([x["data"] for x in dias], ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25"])
        self.assertEqual(dias[4]["ativas"], 1)

    def test_editar_campos_pela_api(self):
        r = self.post(f"/api/rota/{self.rota.id}/campo/", {"campo": "saida", "valor": "530"})
        self.assertEqual(r.json()["rota"]["saida"], "05:30")
        totvs._acordar.clear()
        r = self.post(f"/api/parada/{self.piabeta.id}/documento/", {"tipo": "wms", "numero": "3636"})
        self.assertTrue(totvs._acordar.is_set())  # número novo: a TOTVS é consultada na hora
        doc = r.json()["rota"]["paradas"][1]["docs"]["wms"][0]
        self.assertEqual(doc["etapa"], "criada")
        for _ in range(2):
            r = self.post(f"/api/documento/{doc['id']}/etapa/", {"acao": "avancar"})
        doc = r.json()["rota"]["paradas"][1]["docs"]["wms"][0]
        self.assertEqual((doc["etapa"], doc["por"], doc["expedida_por"]), ("em_atendimento", "JOAO", "JOAO"))
        r = self.post(f"/api/documento/{doc['id']}/etapa/", {"acao": "avancar"})
        self.assertEqual(r.json()["rota"]["paradas"][1]["docs"]["wms"][0]["etapa"], "atendida")
        r = self.post(f"/api/documento/{doc['id']}/etapa/", {"acao": "voltar"})
        self.assertEqual(r.json()["rota"]["paradas"][1]["docs"]["wms"][0]["etapa"], "em_atendimento")
        r = self.post(f"/api/documento/{doc['id']}/etapa/", {"acao": "avancar"})
        self.assertEqual(r.json()["rota"]["paradas"][1]["status"], None)  # PIABETÁ sem PES aqui: sem status
        r = self.post(f"/api/rota/{self.rota.id}/mover/", {"data": "2026-10-24"})
        self.assertEqual(r.json()["rota"]["remarcacoes"], 1)

    def test_erro_vira_400(self):
        r = self.post(f"/api/parada/{self.piabeta.id}/retirada/", {"campo": "rt", "sim": True})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Lance o volume", r.json()["erro"])
        self.assertEqual(self.post(f"/api/rota/{self.rota.id}/mover/", {"data": "xx"}).status_code, 400)
        self.assertEqual(self.client.get("/api/dia/2026-13-40/").status_code, 400)
        self.assertEqual(self.client.get("/api/semana/xx/").status_code, 400)

    def test_visao_e_individual(self):
        r = self.post("/api/visao/", {"colunas_ocultas": ["frete_envio"], "filtro": "west"})
        self.assertEqual(r.json()["colunas_ocultas"], ["frete_envio"])
        self.client.force_login(self.beatriz)
        self.assertEqual(self.client.get("/api/visao/").json(), {"colunas_ocultas": [], "filtro": "", "cor": "", "apelido": ""})
        self.client.force_login(self.joao)
        self.assertEqual(self.client.get("/api/visao/").json(), {"colunas_ocultas": ["frete_envio"], "filtro": "west", "cor": "", "apelido": ""})

    def test_cor_do_usuario(self):
        self.assertEqual(self.post("/api/visao/", {"cor": "red;x"}).json()["cor"], "")  # só #rrggbb
        self.post("/api/visao/", {"cor": "#22c55e"})
        self.post("/api/visao/", {"apelido": " joãozinho "})
        self.assertIn({"cor": "#22c55e", "apelido": "JOÃOZINHO"}, self.client.get(f"/api/dia/{self.rota.data}/").json()["pessoas"].values())
        self.assertContains(self.client.get("/"), "JOÃOZINHO")

    def test_mudancas_desde(self):
        desde = Historico.objects.last().id
        acoes.editar_rota(self.beatriz, self.rota, "lacre", "928435")
        d = self.client.get(f"/api/mudancas/?desde={desde}").json()
        self.assertEqual(len(d["eventos"]), 1)
        e = d["eventos"][0]
        self.assertEqual((e["rota"], e["campo"], e["data"], e["usuario"]), (self.rota.id, "lacre", "2026-10-23", "BEATRIZ"))
        self.assertIn("BEATRIZ alterou lacre", e["texto"])
        self.assertEqual(d["ultimo"], Historico.objects.last().id)
        self.assertEqual(self.client.get(f"/api/mudancas/?desde={d['ultimo']}").json()["eventos"], [])
        from django.conf import settings
        self.assertEqual(d["versao"], settings.VERSAO)  # a tela recarrega sozinha quando a versão do servidor muda

    def test_criar_rota_pela_api(self):
        r = self.post("/api/rota/", {"data": "2026-10-24", "nome": "missão", "lojas": [], "fora_do_planejamento": True})
        self.assertEqual(r.json()["rota"]["nome"], "MISSÃO")
        self.assertEqual(Rota.objects.filter(data=date(2026, 10, 24)).count(), 1)

    def test_get_em_acao_recusa(self):
        self.assertEqual(self.client.get(f"/api/parada/{self.piabeta.id}/retirada/").status_code, 405)


class CapaTest(TestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command("importar_cadastro", verbosity=0)
        self.u = User.objects.create_user("joao")
        self.client.force_login(self.u)

    def test_capa_loja_sem_contato(self):
        rota = acoes.criar_rota(self.u, date(2026, 10, 22), "Z. OESTE IV", ["PARK", "WEST III"], False)
        csg = acoes.adicionar_parada(self.u, rota)
        acoes.editar_parada(self.u, csg, "loja", "CSG")
        west = rota.paradas.get(loja="WEST III")
        acoes.editar_parada(self.u, west, "rt", "26")
        acoes.editar_parada(self.u, west, "cx", "50")
        acoes.editar_rota(self.u, rota, "lacre", "928435")
        c = self.client.get(f"/api/rota/{rota.id}/capa/").json()
        self.assertEqual([l["nome"] for l in c["lojas"]], ["PARK", "WEST III", "CSG"])
        park, west, csg = c["lojas"]
        self.assertEqual((park["horario"], park["ramal"], park["oi"]), ("08:30", "1845", "(21) 90000-0031"))
        self.assertEqual((west["horario"], west["rt"], west["cx"], west["total"]), ("Horário normal", 26, 50, 76))
        self.assertEqual((csg["ramal"], csg["oi"], csg["total"]), ("", "", 0))
        self.assertEqual(c["total"], 76)
        self.assertEqual([r["loja"] for r in c["regras"]], ["PARK", "WEST III"])
        self.assertEqual((c["data"], c["lacre"]), ("22/10/2026", "928435"))


class EstaticosTest(TestCase):
    def test_estaticos_sem_login(self):
        import tempfile
        from pathlib import Path
        from django.test import override_settings
        with tempfile.TemporaryDirectory() as d:
            Path(d, "x.css").write_text("body{}")
            with override_settings(STATIC_ROOT=d):
                r = self.client.get("/static/x.css")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(b"".join(r.streaming_content), b"body{}")
                self.assertIn(self.client.get("/static/../manage.py").status_code, (400, 404))  # fora da pasta: recusado


class PlanejamentoApiTest(TestCase):
    def setUp(self):
        from rotas.models import RotaPadrao
        self.u = User.objects.create_user("joao")
        self.client.force_login(self.u)
        RotaPadrao.objects.create(nome="Z. OESTE IV", saida="08:00")
        RotaPadrao.objects.create(nome="R. LAGOS", saida="04:00")

    def test_planejamento_da_semana(self):
        acoes.criar_rota(self.u, date(2026, 10, 20), "R. LAGOS", [], False)
        acoes.criar_rota(self.u, date(2026, 10, 20), "MISSÃO", [], True)
        cancelada = acoes.criar_rota(self.u, date(2026, 10, 21), "Z. OESTE IV", [], False)
        acoes.cancelar_rota(self.u, cancelada)
        d = self.client.get("/api/planejamento/2026-10-22/").json()
        self.assertEqual(d["rotas"], ["R. LAGOS", "Z. OESTE IV"])
        self.assertEqual(d["dias"][0], {"data": "2026-10-19", "rotas": []})
        self.assertEqual(d["dias"][1], {"data": "2026-10-20", "rotas": ["MISSÃO", "R. LAGOS"]})
        self.assertEqual(d["dias"][2]["rotas"], [])  # cancelada não conta

    def test_planejar_pela_api(self):
        r = self.client.post("/api/planejar/", json.dumps({"marcacoes": {"2026-10-26": ["R. LAGOS"], "2026-10-27": ["R. LAGOS", "Z. OESTE IV"]}}), content_type="application/json")
        self.assertEqual(r.json(), {"criadas": 3, "canceladas": 0, "reativadas": 0})
        r = self.client.post("/api/planejar/", json.dumps({"marcacoes": {"xx": ["R. LAGOS"]}}), content_type="application/json")
        self.assertEqual(r.status_code, 400)


class NovasAcoesApiTest(TestCase):
    def setUp(self):
        from rotas.models import RotaPadrao
        self.u = User.objects.create_user("joao")
        self.client.force_login(self.u)
        RotaPadrao.objects.create(nome="R. LAGOS", saida="04:00")
        self.rota = acoes.criar_rota(self.u, date(2026, 10, 26), "R. LAGOS", ["MARICÁ II", "BÚZIOS"], False)

    def post(self, url, dados=None):
        return self.client.post(url, json.dumps(dados or {}), content_type="application/json")

    def test_duplicar_e_inverter(self):
        r = self.post(f"/api/rota/{self.rota.id}/duplicar/").json()["rota"]
        self.assertEqual((r["nome"], r["data"]), ("R. LAGOS (2)", "2026-10-26"))
        r = self.post(f"/api/rota/{self.rota.id}/inverter/").json()["rota"]
        self.assertEqual([p["loja"] for p in r["paradas"]], ["FÁBRICA", "BÚZIOS", "MARICÁ II", "FÁBRICA"])

    def test_excluir_rota(self):
        self.assertEqual(self.post(f"/api/rota/{self.rota.id}/excluir/").json(), {"ok": True})
        self.assertFalse(Rota.objects.filter(pk=self.rota.id).exists())

    def test_painel_do_mes(self):
        Rota.objects.all().delete()
        p = acoes.criar_rota(self.u, date(2026, 10, 23), "BAIXADA II", ["PIABETÁ"], False).paradas.get(loja="PIABETÁ")
        p.pes, p.rt, p.cx, p.frete_envio = "450", "12", "x", 300
        p.save()
        r = self.client.get("/painel/?mes=2026-10")
        self.assertEqual((r.context["ativas"], r.context["tot"]["entregas"], r.context["tot"]["rt"], r.context["tot"]["cx"]), (1, 1, 12, 0))
        self.assertEqual(r.context["dias"][4]["rotas"], 1)  # 23/10/2026 é sexta
        self.assertEqual(self.client.get("/painel/?mes=lixo").status_code, 200)
        self.assertEqual(self.client.get("/painel/?mes=2026-12").context["seg"], date(2027, 1, 1))

    def test_planejar_com_desmarcacoes(self):
        r = self.post("/api/planejar/", {"marcacoes": {}, "desmarcacoes": {"2026-10-26": ["R. LAGOS"]}})
        self.assertEqual(r.json(), {"criadas": 0, "canceladas": 1, "reativadas": 0})
        r = self.post("/api/planejar/", {"marcacoes": {"2026-10-26": ["R. LAGOS"]}})
        self.assertEqual(r.json(), {"criadas": 0, "canceladas": 0, "reativadas": 1})

    def test_tipo_do_veiculo_na_rota(self):
        from rotas.models import Veiculo
        # ABC1234 já vem da frota cadastrada (migration 0005)
        r = self.post(f"/api/rota/{self.rota.id}/campo/", {"campo": "veiculo", "valor": "ABC-1234"}).json()["rota"]
        self.assertEqual((r["veiculo"], r["tipo_veiculo"]), ("ABC1234", "ATEGO"))
        r = self.post(f"/api/rota/{self.rota.id}/campo/", {"campo": "veiculo", "valor": "ZZZ9999"}).json()["rota"]
        self.assertEqual(r["tipo_veiculo"], "")


class EntregaTest(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("joao")
        self.rota = acoes.criar_rota(self.u, date(2026, 10, 27), "Z. OESTE IV", ["PARK"], False)
        park = self.rota.paradas.get(loja="PARK")
        park.pes = "538"
        park.save()
        acoes.editar_documento(self.u, park, "t434", "805")
        acoes.editar_documento(self.u, park, "t3386", "26")
        acoes.editar_documento(self.u, park, "wms", "3655")

    def test_so_434_e_3386_e_so_para_os_donos(self):
        from unittest import mock
        self.client.force_login(User.objects.create_user("beatriz"))
        self.assertEqual(self.client.get(f"/api/rota/{self.rota.id}/entrega/").status_code, 404)
        self.client.force_login(self.u)
        with mock.patch("rotas.totvs.entrega", return_value=[{"numero": "805"}]) as entrega:
            c = self.client.get(f"/api/rota/{self.rota.id}/entrega/").json()
        self.assertEqual([(d.tipo.codigo, d.numero) for d in entrega.call_args.args[0]], [("t434", "805"), ("t3386", "26")])
        self.assertEqual(c, {"rota": "Z. OESTE IV", "data": "27/10/2026", "folhas": [{"numero": "805"}]})
