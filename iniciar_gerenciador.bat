@echo off
chcp 65001 >nul
title Maestro - Iniciar Gerenciador
cd /d "%~dp0"

echo ===================================================================
echo             MAESTRO - SERVICO DO GERENCIADOR CENTRAL
echo ===================================================================
echo Verificando se o gerenciador ja esta em execucao...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$proc = Get-CimInstance Win32_Process | Where-Object { ($_.Name -like 'python*.exe') -and ($_.CommandLine -like '*gerenciador.py*') };" ^
    "if ($proc) { Write-Host '[AVISO] O Gerenciador ja esta rodando em segundo plano! PID:' $proc.ProcessId -ForegroundColor Yellow; exit 1 } else { exit 0 }"

if errorlevel 1 (
    echo.
    echo O servico ja esta ativo. Use parar_gerenciador.bat se quiser reiniciar.
    ping -n 3 127.0.0.1 >nul
    exit /b 0
)

echo Iniciando o Gerenciador em segundo plano como servico...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$py = (Get-Command pythonw -ErrorAction SilentlyContinue).Source;" ^
    "if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source };" ^
    "if (-not $py) { Write-Host '[ERRO] Python nao foi encontrado no PATH do sistema!' -ForegroundColor Red; exit 2 };" ^
    "Start-Process -FilePath $py -ArgumentList 'gerenciador.py' -WorkingDirectory '%~dp0' -WindowStyle Hidden;"

if errorlevel 2 (
    echo.
    echo [ERRO] Nao foi possivel localizar o Python. Verifique a instalacao.
    pause
    exit /b 2
)

ping -n 3 127.0.0.1 >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$proc = Get-CimInstance Win32_Process | Where-Object { ($_.Name -like 'python*.exe') -and ($_.CommandLine -like '*gerenciador.py*') };" ^
    "if ($proc) {" ^
    "    Write-Host '[SUCESSO] Gerenciador iniciado com sucesso em segundo plano!' -ForegroundColor Green;" ^
    "    Write-Host ('          PID do Processo: ' + $proc.ProcessId) -ForegroundColor Cyan;" ^
    "    Write-Host '          Log Central: \\SERVIDOR2\Publico\ALLAN\Logs\gerenciador.txt' -ForegroundColor Cyan;
    Write-Host '          Log Local: logs\gerenciador.log' -ForegroundColor Cyan;" ^
    "} else {" ^
    "    Write-Host '[INFO] Processo disparado. Consulte logs\gerenciador.log para acompanhar.' -ForegroundColor Yellow;" ^
    "}"

echo ===================================================================
echo Nenhuma janela ficara aberta na tela. O sistema continuara ativo.
ping -n 3 127.0.0.1 >nul
exit /b 0
