@echo off
chcp 65001 >nul
title Maestro - Parar Gerenciador
cd /d "%~dp0"

echo ===================================================================
echo             MAESTRO - PARAR SERVICO DO GERENCIADOR
echo ===================================================================
echo Localizando processos ativos do Gerenciador...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$procs = Get-CimInstance Win32_Process | Where-Object { ($_.Name -like 'python*.exe') -and ($_.CommandLine -like '*gerenciador.py*') };" ^
    "if (-not $procs) { Write-Host '[INFO] Nenhum processo do Gerenciador em execucao no momento.' -ForegroundColor Yellow; exit 0 };" ^
    "foreach ($p in $procs) {" ^
    "    Write-Host ('Encerrando processo Python PID ' + $p.ProcessId + '...') -ForegroundColor Cyan;" ^
    "    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue;" ^
    "};" ^
    "Write-Host '[SUCESSO] O servico do Gerenciador foi finalizado com sucesso.' -ForegroundColor Green;"

if exist "gerenciador.estado" del /f /q "gerenciador.estado" >nul 2>&1

echo ===================================================================
ping -n 3 127.0.0.1 >nul
exit /b 0
