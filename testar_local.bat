@echo off
echo ==========================================
echo    INICIANDO AMBIENTE DE TESTE LOCAL
echo ==========================================
echo.


echo 1. Iniciando o Servidor Node.js (Site)...
cd Users\USER\Desktop\maestro\site-maestro
start "Servidor Node" cmd /k "node server.js"

echo Aguardando o servidor Node.js iniciar...
timeout /t 3 /nobreak >nul

echo 2. Iniciando o Gerenciador Python...
cd ..
start "Gerenciador Python" cmd /k "set URL_SERVIDOR=http://localhost:8000&& python gerenciador.py"

echo 3. Abrindo o painel no navegador...
start http://localhost:8000/index.html

echo.
echo Tudo pronto!
pause
