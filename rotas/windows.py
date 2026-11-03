"""Login com o usuário e a senha do Windows: quem confere a senha é o próprio Windows (domínio), o sistema não guarda."""
import ctypes
import os

from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User

DOMINIO = os.environ.get("ROTAS_DOMINIO") or os.environ.get("USERDOMAIN", "")  # o servidor precisa estar no domínio


def senha_confere(usuario, senha):
    if os.name != "nt":
        return False
    token = ctypes.c_void_p()
    # 3 = LOGON32_LOGON_NETWORK: só valida a senha, não abre sessão na máquina
    ok = ctypes.windll.advapi32.LogonUserW(usuario, DOMINIO or None, senha, 3, 0, ctypes.byref(token))
    if ok:
        ctypes.windll.kernel32.CloseHandle(token)
    return bool(ok)


class WindowsBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None
        usuario = username.split("\\")[-1].split("@")[0].strip().lower()  # aceita DOMINIO\usuario e usuario@dominio
        if not senha_confere(usuario, password):
            return None
        user, novo = User.objects.get_or_create(username=usuario)
        if novo:
            user.set_unusable_password()
            user.save()
        return user if self.user_can_authenticate(user) else None


class _Blob(ctypes.Structure):
    _fields_ = [("tamanho", ctypes.c_uint32), ("dados", ctypes.POINTER(ctypes.c_char))]


def _dpapi(funcao, dados):
    entra = _Blob(len(dados), ctypes.cast(ctypes.create_string_buffer(dados, len(dados)), ctypes.POINTER(ctypes.c_char)))
    sai = _Blob()
    # 1 = CRYPTPROTECT_UI_FORBIDDEN: o servidor não tem tela para perguntar nada
    if not funcao(ctypes.byref(entra), None, None, None, None, 1, ctypes.byref(sai)):
        raise ValueError("o Windows não abriu a senha guardada (salve o login do SSW de novo)")
    try:
        return ctypes.string_at(sai.dados, sai.tamanho)
    finally:
        ctypes.windll.kernel32.LocalFree(sai.dados)


def proteger(texto):
    """Senha guardada pelo próprio Windows (DPAPI): só o usuário do Windows que roda o servidor, nesta máquina, abre.
    O banco copiado para outro lugar não revela a senha; levar o sistema para outra máquina = salvar o login de novo."""
    import base64
    if os.name != "nt":
        raise ValueError("guardar senha só no servidor Windows")
    return base64.b64encode(_dpapi(ctypes.windll.crypt32.CryptProtectData, texto.encode("utf-8"))).decode()


def abrir(guardado):
    import base64
    if os.name != "nt":
        raise ValueError("guardar senha só no servidor Windows")
    return _dpapi(ctypes.windll.crypt32.CryptUnprotectData, base64.b64decode(guardado)).decode("utf-8")
