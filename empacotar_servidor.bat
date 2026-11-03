@echo off
rem Monta na Area de Trabalho o RotasServidor.zip para levar o sistema a outra maquina (o servidor da empresa):
rem codigo, banco e arquivos de C:\RotasExpedicao e as variaveis TOTVS/SSW/ALCIS deste usuario.
rem Depois de rodar, feche o sistema desta maquina: o que for lancado aqui depois nao vai para o servidor.
cd /d "%~dp0"
setlocal
set "PACOTE=%USERPROFILE%\Desktop\RotasServidor"
if exist "%PACOTE%" rmdir /s /q "%PACOTE%"
mkdir "%PACOTE%" || goto erro
robocopy . "%PACOTE%\sistema" /E /NFL /NDL /NJH /NJS /XD .venv .git .superpowers __pycache__ backups build dist dist-novo staticfiles /XF *.exe *.spec *.sqlite3 chave.txt pasta_atualizacao.txt RotasExpedicao.vbs >nul
if errorlevel 8 goto erro
move "%PACOTE%\sistema\instalar_servidor.bat" "%PACOTE%\" >nul || goto erro
robocopy C:\RotasExpedicao "%PACOTE%\dados" /E /NFL /NDL /NJH /NJS /XD atualizacao /XF db.sqlite3 pasta_atualizacao.txt ultima_atualizacao.txt instalacao.log >nul
if errorlevel 8 goto erro
rem Copia do banco pela propria SQLite: sai inteira mesmo com o sistema no ar.
.venv\Scripts\python -c "import sqlite3,sys; d=sqlite3.connect(sys.argv[2]); sqlite3.connect(sys.argv[1]).backup(d); d.close()" C:\RotasExpedicao\db.sqlite3 "%PACOTE%\dados\db.sqlite3" || goto erro
powershell -NoProfile -Command "$e=[Environment]::GetEnvironmentVariables('User'); $e.Keys | ? { $_ -match '^(TOTVS|SSW|ALCIS)_' } | %% { $_ + '=' + $e[$_] } | Set-Content -Encoding UTF8 '%PACOTE%\variaveis.txt'" || goto erro
if exist "%PACOTE%.zip" del "%PACOTE%.zip"
powershell -NoProfile -Command "Compress-Archive -Path '%PACOTE%\*' -DestinationPath '%PACOTE%.zip'" || goto erro
rmdir /s /q "%PACOTE%"
echo.
echo Pronto: %PACOTE%.zip  (tem as senhas TOTVS/SSW: apague depois de instalar)
exit /b 0
:erro
echo ERRO ao montar o pacote.
exit /b 1
