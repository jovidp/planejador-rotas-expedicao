@echo off
rem Atualiza o sistema: backup do banco, dependencias, banco de dados e arquivos estaticos.
rem Rode depois de copiar a versao nova do codigo (ou de "git pull"). Depois reinicie o rodar.bat.
cd /d "%~dp0"
setlocal
if not exist .venv python -m venv .venv || goto erro
if not exist backups mkdir backups
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmm"') do set AGORA=%%i
if exist db.sqlite3 copy /y db.sqlite3 backups\db-%AGORA%.sqlite3 >nul || goto erro
.venv\Scripts\python -m pip install -q -r requirements.txt || goto erro
.venv\Scripts\python manage.py migrate || goto erro
.venv\Scripts\python manage.py collectstatic --noinput -v 0 || goto erro
echo.
echo Atualizado. Backup em backups\db-%AGORA%.sqlite3
exit /b 0
:erro
echo.
echo ERRO na atualizacao. O banco anterior esta em backups\ - nada foi apagado.
exit /b 1
