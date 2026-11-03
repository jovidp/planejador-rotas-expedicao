@echo off
rem Sobe o sistema de rotas na porta 8000. Os usuarios abrem http://<este-servidor>:8000 no navegador.
cd /d "%~dp0"
.venv\Scripts\waitress-serve --listen=*:8000 --threads=8 config.wsgi:application
