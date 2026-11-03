"""Semana planejada sozinha: no sábado e no domingo, se a semana que vem (seg a sáb) ainda está vazia, copia as rotas do
cadastro desta semana, com saída, motorista, veículo, ajudantes e rastreador. Semana com qualquer rota (até cancelada) não
é mexida: quem planejou à mão manda. Os ajustes ficam para a grade "Planejar semana"."""
import logging
import threading
import time
from datetime import timedelta

from django.contrib.auth.models import User
from django.utils import timezone

from . import acoes
from .models import Rota, RotaPadrao

log = logging.getLogger(__name__)

CAMPOS = ["saida", "motorista", "veiculo", "ajudantes", "rastreador"]


def copiar_semana(seg):
    """Cria na semana de seg as rotas do cadastro da semana anterior. Devolve quantas criou."""
    dias = [seg + timedelta(days=i) for i in range(6)]
    if Rota.objects.filter(data__in=dias).exists():
        return 0
    antes = list(Rota.objects.filter(data__in=[d - timedelta(days=7) for d in dias], status=Rota.ATIVA,
                                     nome__in=RotaPadrao.objects.values("nome")).order_by("data", "ordem", "id"))
    if not antes:
        return 0
    usuario, novo = User.objects.get_or_create(username="planejamento", defaults={"first_name": "PLANEJAMENTO AUTOMÁTICO"})
    if novo:
        usuario.set_unusable_password()
        usuario.save()
    marcacoes = {}
    for r in antes:
        marcacoes.setdefault(r.data + timedelta(days=7), []).append(r.nome)
    criadas = acoes.planejar_semana(usuario, marcacoes)["criadas"]
    for nova in criadas:
        velha = next(r for r in antes if r.nome == nova.nome and r.data == nova.data - timedelta(days=7))
        for campo in CAMPOS:
            acoes.editar_rota(usuario, nova, campo, getattr(velha, campo))
    return len(criadas)


def iniciar():
    """Chamado pelo wsgi: confere a cada hora."""
    def laco():
        while True:
            hoje = timezone.localdate()
            if hoje.weekday() in (5, 6):  # sábado ou domingo: a semana que vem
                try:
                    if n := copiar_semana(hoje + timedelta(days=7 - hoje.weekday())):
                        log.warning("Planejamento: %s rota(s) da semana que vem copiadas desta semana", n)
                except Exception:
                    log.exception("Planejamento: falha ao copiar a semana")
            time.sleep(3600)

    threading.Thread(target=laco, name="planejar", daemon=True).start()
