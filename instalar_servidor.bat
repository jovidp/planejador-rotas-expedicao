@echo off
rem Instala (ou atualiza) o sistema de rotas nesta maquina a partir do RotasServidor.zip montado pelo empacotar_servidor.bat.
rem Codigo em C:\RotasExpedicao\sistema, banco e arquivos em C:\RotasExpedicao. Rodar de novo com um pacote novo = atualizar o codigo.
rem Requisito: Python 3.12 ou mais novo, instalado com "Add to PATH", e internet (baixa Django, Playwright e o Chromium).
cd /d "%~dp0"
setlocal
set "DESTINO=C:\RotasExpedicao"
python --version >nul 2>&1 || (echo Instale o Python 3.12+ ^(python.org, marque "Add python.exe to PATH"^) e rode de novo. & pause & exit /b 1)

rem Banco: so copia se ainda nao houver um aqui, para nunca sobrescrever o que ja foi lancado no servidor.
if exist "%DESTINO%\db.sqlite3" (
  echo Banco ja existe em %DESTINO%: mantido. Atualizando so o codigo.
) else (
  robocopy dados "%DESTINO%" /E /NFL /NDL /NJH /NJS >nul
  if errorlevel 8 goto erro
)
robocopy sistema "%DESTINO%\sistema" /E /NFL /NDL /NJH /NJS >nul
if errorlevel 8 goto erro

cd /d "%DESTINO%\sistema"
if not exist .venv python -m venv .venv || goto erro
.venv\Scripts\python -m pip install -q -r requirements.txt || goto erro
.venv\Scripts\python -m playwright install chromium || goto erro
cd /d "%~dp0"

rem Variaveis TOTVS/SSW/ALCIS da maquina de origem, gravadas para este usuario do Windows. O arquivo tem senhas: sai depois de aplicado.
if exist variaveis.txt (
  powershell -NoProfile -Command "Get-Content -Encoding UTF8 variaveis.txt | ? { $_ -match '=' } | %% { $n,$v = $_ -split '=',2; [Environment]::SetEnvironmentVariable($n,$v,'User') }" || goto erro
  del variaveis.txt
)

netsh advfirewall firewall show rule name="Sistema de Rotas 8000" >nul 2>&1 || netsh advfirewall firewall add rule name="Sistema de Rotas 8000" dir=in action=allow protocol=TCP localport=8000 >nul || echo AVISO: libere a porta 8000 no firewall ^(precisa de administrador^).

rem Sobe junto com o login deste usuario, como na maquina de origem.
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Sistema de Rotas.lnk'); $s.TargetPath='%DESTINO%\sistema\iniciar.bat'; $s.WorkingDirectory='%DESTINO%\sistema'; $s.WindowStyle=7; $s.Save()" || goto erro

rem Variaveis novas so valem num processo novo: o explorer abre o iniciar.bat ja com elas.
explorer "%DESTINO%\sistema\iniciar.bat"
echo.
echo Instalado. Endereco: http://%COMPUTERNAME%:8000  (ou pelo IP desta maquina)
pause
exit /b 0
:erro
echo.
echo ERRO na instalacao. Nada do banco foi apagado.
pause
exit /b 1
