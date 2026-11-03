from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from config.ambiente import banco
from rotas.models import Loja, RotaPadrao


class BancoTest(SimpleTestCase):
    def test_sem_url_usa_o_sqlite_local(self):
        self.assertEqual(banco("", "C:/RotasExpedicao/db.sqlite3"), {"ENGINE": "django.db.backends.sqlite3", "NAME": "C:/RotasExpedicao/db.sqlite3"})

    def test_url_do_render_vira_postgres(self):
        b = banco("postgres://rotas_user:s%40nha@dpg-abc-a.virginia-postgres.render.com:5432/rotas", "x")
        self.assertEqual((b["ENGINE"], b["NAME"], b["USER"], b["PASSWORD"], b["HOST"], b["PORT"]),
                         ("django.db.backends.postgresql", "rotas", "rotas_user", "s@nha", "dpg-abc-a.virginia-postgres.render.com", "5432"))
        self.assertEqual(banco("postgresql://u:p@dpg-abc-a/rotas", "x")["PORT"], "5432")  # endereço interno, sem porta

    def test_url_invalida(self):
        with self.assertRaises(ValueError):
            banco("mysql://u:p@h/db", "x")


class ImportarSeVazioTest(TestCase):
    def test_nao_sobrescreve_cadastro_existente(self):
        call_command("importar_cadastro", "--se-vazio", verbosity=0)
        self.assertEqual(Loja.objects.count(), 93)
        RotaPadrao.objects.filter(nome="R. LAGOS").update(saida="03:30")  # ajuste feito pelo admin
        call_command("importar_cadastro", "--se-vazio", verbosity=0)  # a cada publicação na nuvem
        self.assertEqual(RotaPadrao.objects.get(nome="R. LAGOS").saida, "03:30")


class SupabaseTest(SimpleTestCase):
    def test_url_da_supabase_com_ssl(self):
        b = banco("postgresql://postgres.abcd:s3nh%40@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require", "x")
        self.assertEqual((b["USER"], b["PASSWORD"], b["HOST"], b["PORT"], b["NAME"], b["OPTIONS"]),
                         ("postgres.abcd", "s3nh@", "aws-0-us-east-1.pooler.supabase.com", "5432", "postgres", {"sslmode": "require"}))

    def test_supabase_sem_sslmode_exige_ssl(self):
        self.assertEqual(banco("postgresql://u:p@aws-0-us-east-1.pooler.supabase.com:5432/postgres", "x")["OPTIONS"], {"sslmode": "require"})
