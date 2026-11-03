#!/usr/bin/env bash
# Publicação na Render: roda a cada "git push". Banco e cadastro não são apagados.
set -o errexit
pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py importar_cadastro --se-vazio
# Primeiro administrador: DJANGO_SUPERUSER_USERNAME/PASSWORD no painel da Render. Depois de criado, este passo só avisa que já existe.
python manage.py createsuperuser --noinput || echo "Administrador já existe (ou variáveis não definidas)."
