"""Chrome de cada robô com perfil próprio, para fechar o que sobrou de um início anterior antes de abrir outro.

20/10: ligar de novo, login que falha ou gerenciador que cai deixavam Chromes do robô abertos, acumulando abas.
Só fecha o Chrome que usa o perfil deste robô: o do outro robô e o Chrome pessoal de quem usa o servidor ficam.
O perfil é apagado a cada início, então o login começa do zero, como antes (o Selenium usava um perfil temporário).
"""
import os
import shutil
import subprocess
import time

import ambiente


def perfil(nome):
    return os.path.join(ambiente.RAIZ, 'perfis_chrome', nome)


def comando_fechar(caminho):
    alvo = caminho.replace("'", "''")
    # só aspas simples: aspas duplas se perdem no caminho Python -> powershell.exe e a consulta falha
    script = ("Get-CimInstance Win32_Process | "
              f"Where-Object {{ $_.Name -eq 'chrome.exe' -and $_.CommandLine -like '*{alvo}*' }} | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    return ['powershell', '-NoProfile', '-NonInteractive', '-Command', script]


def preparar(nome, rodar=subprocess.run):
    """Fecha o Chrome que ainda usa o perfil deste robô e devolve o caminho do perfil, limpo."""
    caminho = perfil(nome)
    if os.name == 'nt':
        try:
            rodar(comando_fechar(caminho), capture_output=True, timeout=30)
            time.sleep(1)   # o Windows solta os arquivos do perfil
        except Exception:
            pass            # sem PowerShell: segue como antes
    shutil.rmtree(caminho, ignore_errors=True)
    os.makedirs(caminho, exist_ok=True)
    return caminho
