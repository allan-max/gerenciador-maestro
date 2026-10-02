@echo off
echo ==========================================
echo    INICIANDO AMBIENTE DE TESTE LOCAL
echo ==========================================
echo.

:: Define as pastas absolutas com base no local deste arquivo .bat
set "PASTA_GERENCIADOR=%~dp0"
set "PASTA_SITE=%~dp0..\site-maestro"

echo 1. Iniciando o Servidor Node.js (Site)...
cd /d "%PASTA_SITE%"
start "Servidor Node" cmd /k "node server.js"

echo Aguardando o servidor Node.js iniciar...
timeout /t 3 /nobreak >nul

echo 2. Iniciando o Gerenciador Python...
cd /d "%PASTA_GERENCIADOR%"
start "Gerenciador Python" cmd /k "set URL_SERVIDOR=http://127.0.0.1:8000&& python gerenciador.py"

echo 3. Abrindo o painel no navegador...
start http://127.0.0.1:8000/index.html

echo.
echo Tudo pronto!
pause
