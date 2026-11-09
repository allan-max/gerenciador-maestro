@echo off
chcp 65001 >nul
title Maestro - Listar eventos do Coupa
cd /d "%~dp0"

set "PY=python"
where python >nul 2>nul || set "PY=py"

%PY% scripts\listar_eventos_coupa.py %*

echo.
pause
