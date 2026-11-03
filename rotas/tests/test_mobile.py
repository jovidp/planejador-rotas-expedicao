import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase


class AppNoCelularTest(TestCase):
    def test_pagina_tem_manifesto_e_icone(self):
        self.client.force_login(User.objects.create_user("joao"))
        html = self.client.get("/").content.decode()
        for trecho in ['rel="manifest"', 'rel="icon"', 'name="theme-color"', 'rel="apple-touch-icon"', 'name="viewport"']:
            self.assertIn(trecho, html)

    def test_manifesto_valido(self):
        m = json.loads((Path(settings.BASE_DIR) / "rotas/static/rotas/manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual((m["start_url"], m["display"], m["short_name"]), ("/", "standalone", "Rotas"))
        tamanhos = {i["sizes"] for i in m["icons"]}
        self.assertTrue({"192x192", "512x512"} <= tamanhos)
        for i in m["icons"]:
            self.assertTrue((Path(settings.BASE_DIR) / "rotas/static/rotas" / i["src"]).exists(), i["src"])

    def test_service_worker_sem_login_na_raiz(self):
        r = self.client.get("/sw.js")  # precisa estar na raiz para valer para o site todo
        self.assertEqual(r.status_code, 200)
        self.assertIn("javascript", r["Content-Type"])
