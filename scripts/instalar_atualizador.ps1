# Agenda o atualizador automatico (scripts\atualizador.py) a cada 5 minutos.
# Roda na sessao de quem esta logado no servidor (/IT): o gerenciador reiniciado abre na tela, como hoje.
# Rodar uma vez, no PowerShell como Administrador, na pasta do gerenciador:  .\scripts\instalar_atualizador.ps1
# Desinstalar:  schtasks /Delete /TN "Maestro Atualizador" /F
$ErrorActionPreference = 'Stop'

$python = (Get-Command python.exe).Source
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'    # sem janela piscando a cada 5 min
if (-not (Test-Path $pythonw)) { $pythonw = $python }
$script = Join-Path $PSScriptRoot 'atualizador.py'

schtasks /Create /TN "Maestro Atualizador" /SC MINUTE /MO 5 /TR "`"$pythonw`" `"$script`"" /RL HIGHEST /IT /F
if ($LASTEXITCODE -ne 0) { throw "Nao foi possivel criar a tarefa agendada." }

Write-Host ""
Write-Host "Tarefa 'Maestro Atualizador' criada: $pythonw $script"
Write-Host "Log: $(Join-Path (Split-Path $PSScriptRoot) 'logs\atualizador\atualizador.log')"
Write-Host "Liga/desliga: 'atualizacao_automatica' (sim/nao) em Configuracoes do dashboard2."
