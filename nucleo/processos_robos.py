"""Descobre se cada robô/módulo já está rodando no servidor, olhando os processos do Windows.

Serve para duas coisas:
  1. não abrir um segundo robô por cima do que já está aberto (clique duplo, outra aba, aberto à mão);
  2. o gerenciador vigiar os processos e manter o botão do portal no estado real
     (robô que caiu ou janela fechada à mão volta o botão para "Ligar").

Robôs .NET ("dotnet run"): o processo dotnet.exe não leva o caminho do projeto na linha de comando,
então a pasta é reconhecida pelo diretório de trabalho (cwd) ou pelo executável compilado dentro dela.
"""
import os

# Processos dotnet auxiliares do build: ficam vivos alguns minutos depois do "dotnet run" e não são o robô
AUXILIARES_DOTNET = ('msbuild.dll', 'nodemode', 'vbcscompiler', 'razor', 'build-server')


def _norm(caminho):
    return os.path.normcase(os.path.abspath(caminho)) if caminho else ''


def _dentro(caminho, pasta):
    """caminho está na pasta (ou é ela)? Compara pasta inteira: 'C:\\FINDES2' não conta como 'C:\\FINDES'."""
    c, p = _norm(caminho), _norm(pasta)
    return bool(c and p) and (c == p or c.startswith(p.rstrip(os.sep) + os.sep))


def _iterar(atributos=('pid', 'name', 'exe', 'cmdline')):
    import psutil
    meu_pid = os.getpid()
    for p in psutil.process_iter(list(atributos)):
        if p.pid != meu_pid:
            yield p


def processos_dotnet(pasta, nomes_exe=(), termos_cmd=()):
    """Processos de um robô .NET da pasta: 'dotnet run' com cwd nela, executável compilado dentro dela,
    executáveis com nome conhecido (nomes_exe) ou dotnet com algum termo na linha de comando (termos_cmd)."""
    import psutil
    nomes_exe = {n.lower() for n in nomes_exe}
    termos_cmd = [t.lower() for t in termos_cmd]
    achados = []
    for p in _iterar():
        try:
            nome = (p.info.get('name') or '').lower()
            if nome in nomes_exe:
                achados.append(p)
                continue
            if nome != 'dotnet.exe':
                exe = p.info.get('exe') or ''
                # apphost compilado (bin\Debug\...\Robo.exe); chromedriver/chrome dentro da pasta não contam
                if exe and _dentro(exe, pasta) and 'driver' not in nome and nome != 'chrome.exe':
                    achados.append(p)
                continue
            cmd = ' '.join(p.info.get('cmdline') or []).lower()
            if any(a in cmd for a in AUXILIARES_DOTNET):
                continue
            if any(t in cmd for t in termos_cmd):
                achados.append(p)
                continue
            try:
                if _dentro(p.cwd(), pasta):
                    achados.append(p)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return achados


def processos_com_termo(*termos, nomes=None):
    """Processos cuja linha de comando tem algum dos termos (ex.: 'findes.py', 'mercadoeletronico.bat').
    nomes limita aos executáveis informados (ex.: {'chrome.exe'})."""
    import psutil
    termos = [t.lower() for t in termos if t]
    nomes = {n.lower() for n in nomes} if nomes else None
    achados = []
    for p in _iterar():
        try:
            nome = (p.info.get('name') or '').lower()
            if nomes is not None and nome not in nomes:
                continue
            cmd = ' '.join(p.info.get('cmdline') or []).lower()
            if 'gerenciador' in cmd and nome.startswith('python'):
                continue   # o próprio gerenciador (ou outro) não é o robô
            if any(t in cmd for t in termos):
                achados.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return achados


def matar(processos):
    """Mata os processos e os filhos deles. Nunca levanta exceção."""
    try:
        import psutil
    except Exception:
        return
    for p in processos:
        try:
            for filho in p.children(recursive=True):
                try:
                    filho.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
