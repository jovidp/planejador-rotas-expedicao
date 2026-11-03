from unittest import mock

from django.test import SimpleTestCase

from rotas import recarregar


class RecarregarTest(SimpleTestCase):
    @mock.patch("rotas.recarregar.subprocess.run")
    def test_codigo_novo_que_carrega_reinicia(self, run):
        run.return_value.returncode = 0
        sair = mock.Mock()
        self.assertTrue(recarregar.reiniciar(sair))
        sair.assert_called_once_with(3)

    @mock.patch("rotas.recarregar.subprocess.run")
    def test_codigo_novo_com_erro_mantem_o_servidor(self, run):
        run.return_value.returncode, run.return_value.stderr = 1, "SyntaxError"
        sair = mock.Mock()
        with self.assertLogs("rotas.recarregar", "ERROR"):
            self.assertFalse(recarregar.reiniciar(sair))
        sair.assert_not_called()

    def test_vigia_o_codigo_e_ignora_testes_e_cache(self):
        arquivos = recarregar.assinatura()
        self.assertTrue(any(a.endswith("acoes.py") for a in arquivos))
        self.assertTrue(any(a.endswith("app.js") for a in arquivos))
        self.assertFalse(any("tests" in a or "__pycache__" in a for a in arquivos))

    def test_desligado_sem_a_variavel(self):
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch("rotas.recarregar.threading.Thread") as t:
            recarregar.iniciar()
        t.assert_not_called()


class ConferirJavascriptTest(SimpleTestCase):
    @mock.patch("rotas.recarregar.shutil.which", return_value="node")
    @mock.patch("rotas.recarregar.subprocess.run")
    def test_erro_de_javascript_mantem_o_servidor(self, run, _):
        ok, erro = mock.Mock(returncode=0, stderr=""), mock.Mock(returncode=1, stderr="SyntaxError: Unexpected token")
        run.side_effect = [ok, erro] + [ok] * 50  # manage.py check passa, o primeiro .js tem erro
        sair = mock.Mock()
        with self.assertLogs("rotas.recarregar", "ERROR"):
            self.assertFalse(recarregar.reiniciar(sair))
        sair.assert_not_called()
        self.assertEqual(run.call_args_list[1].args[0][:2], ["node", "--check"])
