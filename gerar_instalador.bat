@echo off
rem Gera dist\InstalarRotas.exe: um unico arquivo que instala/atualiza o sistema em outra maquina (sem precisar de Python la).
cd /d "%~dp0"
.venv\Scripts\python -m pip install -q -r requirements.txt pyinstaller || goto erro
.venv\Scripts\python manage.py collectstatic --noinput -v 0 || goto erro
.venv\Scripts\pyinstaller --noconfirm --clean --onefile --name InstalarRotas --collect-all django --hidden-import waitress --add-data "rotas;rotas" --add-data "config;config" --add-data "staticfiles;staticfiles" --exclude-module rotas.tests instalador.py || goto erro
echo.
echo Pronto: dist\InstalarRotas.exe
exit /b 0
:erro
echo ERRO ao gerar o instalador.
exit /b 1
