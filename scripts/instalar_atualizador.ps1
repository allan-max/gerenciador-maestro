# Agenda o atualizador automatico (scripts\atualizador.py) a cada 5 minutos.
# Roda na sessao de quem esta logado no servidor (/IT): o gerenciador reiniciado abre na tela, como hoje.
# Rodar uma vez, no PowerShell como Administrador, na pasta do gerenciador:  .\scripts\instalar_atualizador.ps1
# Desinstalar:  schtasks /Delete /TN "Maestro Atualizador" /F
$ErrorActionPreference = 'Stop'

$python = (Get-Command python.exe).Definition    # .Source so existe no PowerShell 5+
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'    # sem janela piscando a cada 5 min
if (-not (Test-Path $pythonw)) { $pythonw = $python }
$pasta = Split-Path -Parent $MyInvocation.MyCommand.Path    # $PSScriptRoot nao existe no PowerShell 2
$script = Join-Path $pasta 'atualizador.py'

# \" : o PowerShell (ate o 5.1) nao repassa aspas internas para programas externos
schtasks /Create /TN "Maestro Atualizador" /SC MINUTE /MO 5 /TR "\`"$pythonw\`" \`"$script\`"" /RL HIGHEST /IT /F
if ($LASTEXITCODE -ne 0) { throw "Nao foi possivel criar a tarefa agendada." }

Write-Host ""
Write-Host "Tarefa 'Maestro Atualizador' criada: $pythonw $script"
Write-Host "Log: $(Join-Path (Split-Path $pasta) 'logs\atualizador\atualizador.log')"
Write-Host "Liga/desliga: 'atualizacao_automatica' (sim/nao) em Configuracoes do dashboard2."
