from unittest import mock

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.test import TestCase

from rotas import windows


@mock.patch("rotas.windows.senha_confere")
class LoginWindowsTest(TestCase):
    def test_senha_certa_cria_usuario_sem_senha_local(self, confere):
        confere.return_value = True
        u = authenticate(username=r"EMPRESA\Rafael.Mendes", password="s")
        confere.assert_called_once_with("rafael.mendes", "s")
        self.assertEqual(u.username, "rafael.mendes")
        self.assertFalse(u.has_usable_password())
        self.assertFalse(u.is_staff)

    def test_senha_errada_nao_entra(self, confere):
        confere.return_value = False
        self.assertIsNone(authenticate(username="rafael.mendes", password="s"))
        self.assertFalse(User.objects.filter(username="rafael.mendes").exists())

    def test_usuario_ja_cadastrado_mantem_permissoes(self, confere):
        confere.return_value = True
        User.objects.create_user("maria.souza", is_staff=True)
        self.assertTrue(authenticate(username="maria.souza@empresa.local", password="s").is_staff)

    def test_usuario_desativado_nao_entra(self, confere):
        confere.return_value = True
        User.objects.create_user("maria.souza", is_active=False)
        self.assertIsNone(authenticate(username="maria.souza", password="s"))

    def test_usuario_local_continua_entrando(self, confere):
        User.objects.create_user("admin", password="local")
        self.assertEqual(authenticate(username="admin", password="local").username, "admin")
        confere.assert_not_called()


class SenhaConfereTest(TestCase):
    def test_usuario_inexistente_no_windows(self):
        self.assertFalse(windows.senha_confere("rotas-usuario-que-nao-existe", "x"))
