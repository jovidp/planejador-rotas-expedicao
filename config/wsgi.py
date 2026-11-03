"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()

from rotas import alcis, checklist, pedidos, planejar, recarregar, retiradas, ssw, totvs  # noqa: E402  (depois do Django carregado)

totvs.iniciar()  # consulta a TOTVS a cada 30 s e na hora em que um número é digitado, se as variáveis TOTVS_* estiverem configuradas
alcis.iniciar()  # cargas novas da Alcis a cada 15 segundos, se ALCIS_USUARIO e ALCIS_SENHA estiverem configuradas
pedidos.iniciar()  # pedidos das cargas expedidas, um por um, na TOTVS (precisa da TOTVS e da Alcis configuradas)
checklist.iniciar()  # compara, escondido, o que veio da TOTVS com o Checklist Expedição (diferenças em conferencia_checklist.log)
retiradas.iniciar()  # PDFs das notas de retirada (RT/CX) das lojas a cada 10 min, para os CT-es (/retiradas/, só o João)
ssw.iniciar()  # CT-e das retiradas no SSW, uma loja por vez, pelo botão da página de retiradas (variáveis SSW_*)
planejar.iniciar()  # sábado e domingo: copia esta semana para a que vem, se ela ainda estiver vazia
recarregar.iniciar()  # pelo iniciar.bat: reinicia sozinho quando o código muda
