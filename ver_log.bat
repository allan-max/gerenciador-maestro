@echo off
chcp 65001 >nul
title Maestro - Log em Tempo Real do Gerenciador
cd /d "%~dp0"

echo ===================================================================
echo       MAESTRO - MONITORAMENTO DE LOG DO GERENCIADOR (AO VIVO)
echo ===================================================================
echo Pressione Ctrl+C para fechar esta janela (nao afeta o servico).
echo.

if not exist "logs\gerenciador.log" (
    echo [AVISO] O arquivo logs\gerenciador.log ainda nao foi criado.
    echo Aguardando inicializacao do gerenciador...
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$log = if (Test-Path '\\SERVIDOR2\Publico\ALLAN\Logs\gerenciador.txt') { '\\SERVIDOR2\Publico\ALLAN\Logs\gerenciador.txt' } elseif (Test-Path 'logs\gerenciador.log') { 'logs\gerenciador.log' } else { $null };" ^
    "if ($log) { Write-Host ('Lendo: ' + $log) -ForegroundColor Cyan; Get-Content -Path $log -Wait -Tail 40 } else { Write-Host 'Nenhum arquivo de log encontrado ainda.' -ForegroundColor Red; Start-Sleep -Seconds 5 }"

pause
