@echo off
rem Sobe o sistema de rotas pelo Python do projeto, com o banco instalado (C:\RotasExpedicao).
rem Usado enquanto o antivirus bloqueia o RotasServidor.exe. A pasta Inicializar chama este arquivo ao entrar no Windows.
rem A janela fica minimizada na barra de tarefas: fechar a janela desliga o sistema.
rem O servidor roda em laco: quando o codigo muda (ou ele cai), atualiza o banco e sobe de novo sozinho.
if "%~1"=="laco" goto laco
netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul && exit /b 0
start "Sistema de Rotas (porta 8000) - nao feche" /min cmd /c "%~f0" laco
exit /b 0

:laco
if not defined ROTAS_DADOS set ROTAS_DADOS=C:\RotasExpedicao
set ROTAS_RECARREGAR=1
set "ROTAS_VERSAO=%date% %time%"
cd /d "%~dp0"
rem Banco sempre na versao do codigo: se o codigo novo trouxe mudanca no banco, faz backup e aplica antes de subir.
.venv\Scripts\python manage.py migrate --check >nul 2>&1 || (
  if not exist "%ROTAS_DADOS%\backups" mkdir "%ROTAS_DADOS%\backups"
  for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmm"') do copy /y "%ROTAS_DADOS%\db.sqlite3" "%ROTAS_DADOS%\backups\db-%%i.sqlite3" >nul
  .venv\Scripts\python manage.py migrate || (echo ERRO ao atualizar o banco. O backup esta em %ROTAS_DADOS%\backups & pause & exit /b 1)
)
.venv\Scripts\python manage.py collectstatic --noinput -v 0
.venv\Scripts\waitress-serve --listen=*:8000 --threads=8 config.wsgi:application
echo %date% %time% servidor parou (codigo novo ou erro); subindo de novo...
timeout /t 3 /nobreak >nul
goto laco
