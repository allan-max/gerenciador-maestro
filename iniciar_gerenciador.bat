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

echo Localizando o executavel do Python...

set "PYTHON_EXE="

:: 1. Tenta pythonw no PATH
for /f "tokens=*" %%i in ('where pythonw.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%i"
:: 2. Tenta python no PATH
if not defined PYTHON_EXE for /f "tokens=*" %%i in ('where python.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%i"
:: 3. Tenta pyw / py no PATH (Python Launcher)
if not defined PYTHON_EXE for /f "tokens=*" %%i in ('where pyw.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%i"
if not defined PYTHON_EXE for /f "tokens=*" %%i in ('where py.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%i"

:: 4. Pastas comuns de instalacao do Python no Windows (pythonw.exe)
if not defined PYTHON_EXE for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python*") do if exist "%%d\pythonw.exe" set "PYTHON_EXE=%%d\pythonw.exe"
if not defined PYTHON_EXE for /d %%d in ("C:\Users\Administrator\AppData\Local\Programs\Python\Python*") do if exist "%%d\pythonw.exe" set "PYTHON_EXE=%%d\pythonw.exe"
if not defined PYTHON_EXE for /d %%d in ("C:\Users\*\AppData\Local\Programs\Python\Python*") do if exist "%%d\pythonw.exe" set "PYTHON_EXE=%%d\pythonw.exe"
if not defined PYTHON_EXE for /d %%d in ("C:\Program Files\Python*") do if exist "%%d\pythonw.exe" set "PYTHON_EXE=%%d\pythonw.exe"
if not defined PYTHON_EXE for /d %%d in ("C:\Program Files (x86)\Python*") do if exist "%%d\pythonw.exe" set "PYTHON_EXE=%%d\pythonw.exe"
if not defined PYTHON_EXE for /d %%d in ("C:\Python*") do if exist "%%d\pythonw.exe" set "PYTHON_EXE=%%d\pythonw.exe"

:: 5. Pastas comuns com python.exe tradicional
if not defined PYTHON_EXE for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python*") do if exist "%%d\python.exe" set "PYTHON_EXE=%%d\python.exe"
if not defined PYTHON_EXE for /d %%d in ("C:\Users\Administrator\AppData\Local\Programs\Python\Python*") do if exist "%%d\python.exe" set "PYTHON_EXE=%%d\python.exe"
if not defined PYTHON_EXE for /d %%d in ("C:\Users\*\AppData\Local\Programs\Python\Python*") do if exist "%%d\python.exe" set "PYTHON_EXE=%%d\python.exe"
if not defined PYTHON_EXE for /d %%d in ("C:\Program Files\Python*") do if exist "%%d\python.exe" set "PYTHON_EXE=%%d\python.exe"
if not defined PYTHON_EXE for /d %%d in ("C:\Python*") do if exist "%%d\python.exe" set "PYTHON_EXE=%%d\python.exe"
if not defined PYTHON_EXE if exist "C:\Windows\pyw.exe" set "PYTHON_EXE=C:\Windows\pyw.exe"
if not defined PYTHON_EXE if exist "C:\Windows\py.exe" set "PYTHON_EXE=C:\Windows\py.exe"

:: 6. Fallback final consultando o Registro do Windows via PowerShell
if not defined PYTHON_EXE (
    for /f "tokens=*" %%p in ('powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$reg = Get-ItemProperty -Path 'HKCU:\Software\Python\PythonCore\*\InstallPath','HKLM:\Software\Python\PythonCore\*\InstallPath' -ErrorAction SilentlyContinue | Select-Object -First 1;" ^
        "if ($reg) { $p1 = Join-Path $reg.'(default)' 'pythonw.exe'; if (Test-Path $p1) { $p1 } else { Join-Path $reg.'(default)' 'python.exe' } }"') do (
        if exist "%%p" set "PYTHON_EXE=%%p"
    )
)

if not defined PYTHON_EXE (
    echo.
    echo ===================================================================
    echo [ERRO] Nao foi possivel localizar o Python no computador.
    echo Certifique-se de que o Python esteja instalado.
    echo ===================================================================
    pause
    exit /b 2
)

echo Python encontrado: %PYTHON_EXE%
echo Iniciando o Gerenciador em segundo plano como servico...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Start-Process -FilePath '%PYTHON_EXE%' -ArgumentList 'gerenciador.py' -WorkingDirectory '%~dp0' -WindowStyle Hidden;"

ping -n 3 127.0.0.1 >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$proc = Get-CimInstance Win32_Process | Where-Object { ($_.Name -like 'python*.exe') -and ($_.CommandLine -like '*gerenciador.py*') };" ^
    "if ($proc) {" ^
    "    Write-Host '[SUCESSO] Gerenciador iniciado com sucesso em segundo plano!' -ForegroundColor Green;" ^
    "    Write-Host ('          PID do Processo: ' + $proc.ProcessId) -ForegroundColor Cyan;" ^
    "    Write-Host '          Log Central: \\SERVIDOR2\Publico\ALLAN\Logs\gerenciador.txt' -ForegroundColor Cyan;" ^
    "    Write-Host '          Log Local:   logs\gerenciador.log' -ForegroundColor Cyan;" ^
    "} else {" ^
    "    Write-Host '[INFO] Processo disparado. Consulte logs\gerenciador.log para acompanhar.' -ForegroundColor Yellow;" ^
    "}"

echo ===================================================================
echo Nenhuma janela ficara aberta na tela. O sistema continuara ativo.
ping -n 3 127.0.0.1 >nul
exit /b 0
