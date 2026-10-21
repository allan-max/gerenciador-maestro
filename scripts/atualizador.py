"""Atualização automática do gerenciador e do portal (rodado a cada 5 min pelo Agendador de Tarefas).

Gerenciador: com os robôs parados, guarda os arquivos de dados, avança para o commit novo do GitHub, devolve os
arquivos de dados (o pull pode apagá-los, como em 21/10), roda os testes e reinicia; teste que falha volta a versão.
Portal: avança, instala dependências se mudaram, confere a sintaxe e faz pm2 restart; site que não sobe volta a versão.
Nunca reinicia com robô trabalhando (gerenciador.estado). Liga/desliga: "atualizacao_automatica" no CONFIG.json
(Configurações do dashboard2). Resultado na Atividade; erro também por e-mail.

Instalação: scripts/instalar_atualizador.ps1. Rodar à mão: python scripts/atualizador.py
"""
import json
import os
import shutil
import smtplib
import subprocess
import sys
import time
from datetime import datetime
from email.mime.text import MIMEText

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import ambiente        # noqa: E402
import eventos         # noqa: E402
import instancia_unica  # noqa: E402

DADOS = ('CONFIG.json', '.env', 'banco_usuarios.json', 'banco_ips_bloqueados.json')   # do servidor: produção vence
PORTAL_PM2 = 'Site-Maestro'
ESTADO_VALIDO_S = 120           # gerenciador.estado mais velho que isso = gerenciador não está rodando
AVISO_ESPERA_S = 2 * 3600       # atualização esperando robôs há mais que isso: avisa na Atividade (e continua esperando)
REPETIR_AVISO_S = 6 * 3600      # o mesmo erro não é avisado a cada 5 min
BACKUPS_GUARDADOS = 10
DESLIGADO = {'nao', 'não', 'n', 'false', '0', 'off', 'desligado', 'desligada'}
PM2 = 'pm2.cmd' if os.name == 'nt' else 'pm2'
NPM = 'npm.cmd' if os.name == 'nt' else 'npm'


class Falha(Exception):
    pass


def pasta():
    caminho = os.path.join(RAIZ, 'logs', 'atualizador')
    os.makedirs(caminho, exist_ok=True)
    return caminho


def log(msg):
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    if sys.stdout:   # pythonw (Agendador) não tem console; o console do Windows não aceita todo caractere
        print(linha.encode(sys.stdout.encoding or 'ascii', 'replace').decode(sys.stdout.encoding or 'ascii'))
    with open(os.path.join(pasta(), 'atualizador.log'), 'a', encoding='utf-8') as f:
        f.write(linha + '\n')


def rodar(cmd, cwd, timeout=900):
    """(código, saída). Programa que não existe ou demora demais vira código 127."""
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def ligado(cfg):
    return str(cfg.get('atualizacao_automatica', 'sim')).strip().lower() not in DESLIGADO


# ---------- controle: avisos repetidos e espera pelos robôs ----------
def _controle():
    try:
        with open(os.path.join(pasta(), 'controle.json'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _salvar_controle(dados):
    with open(os.path.join(pasta(), 'controle.json'), 'w', encoding='utf-8') as f:
        json.dump(dados, f)


def avisar(chave, nivel, msg, email=False):
    """Atividade (e e-mail no erro). O mesmo aviso não se repete antes de REPETIR_AVISO_S."""
    log(f"[{nivel}] {msg}")
    ctl = _controle()
    anterior = ctl.setdefault('avisos', {}).get(chave)
    if nivel != 'ok' and anterior and anterior['msg'] == msg and time.time() - anterior['em'] < REPETIR_AVISO_S:
        return
    ctl['avisos'][chave] = {'msg': msg, 'em': time.time()}
    _salvar_controle(ctl)
    try:
        eventos.registrar('gerenciador', 'atualizacao', nivel, msg)
    except Exception as e:
        log(f"Falha ao registrar na Atividade: {e}")
    if email:
        enviar_email(msg)


def enviar_email(msg):
    cfg = ambiente.ler_config()
    user, pwd = cfg.get('SMTP_USER'), cfg.get('SMTP_PASSWORD')
    if not user or not pwd:
        return
    try:
        email = MIMEText(f"{msg}\n\nLog: {os.path.join(pasta(), 'atualizador.log')}", 'plain', 'utf-8')
        email['Subject'] = 'Maestro: atualização automática com problema'
        email['From'], email['To'] = user, cfg.get('emails_relatorio') or user
        with smtplib.SMTP('email-ssl.com.br', 587, timeout=30) as s:
            s.starttls()
            s.login(user, pwd)
            s.send_message(email)
    except Exception as e:
        log(f"Falha ao enviar e-mail: {e}")


def ler_estado():
    try:
        with open(os.path.join(RAIZ, 'gerenciador.estado'), encoding='utf-8') as f:
            estado = json.load(f)
    except (OSError, ValueError):
        return None
    return estado if time.time() - estado.get('em', 0) <= ESTADO_VALIDO_S else None


def ocupado(estado):
    """True se há robô trabalhando (só chamado com atualização pendente). Espera longa é avisada uma vez."""
    ctl = _controle()
    if not (estado and estado.get('ocupados')):
        if ctl.pop('espera', None) is not None:
            _salvar_controle(ctl)
        return False
    espera = ctl.setdefault('espera', {'desde': time.time(), 'avisado': False})
    avisa = not espera['avisado'] and time.time() - espera['desde'] > AVISO_ESPERA_S
    espera['avisado'] = espera['avisado'] or avisa
    _salvar_controle(ctl)
    if avisa:
        avisar('espera', 'aviso', f"Atualização esperando há mais de {AVISO_ESPERA_S // 3600} h: robôs ocupados "
                                  f"({', '.join(estado['ocupados'])}).")
    return True


# ---------- git ----------
def git(repo, *args, rodar=rodar):
    return rodar(['git', *args], repo, timeout=300)


def head(repo, rodar=rodar):
    return git(repo, 'rev-parse', 'HEAD', rodar=rodar)[1].strip()


def novidade(repo, rodar=rodar):
    """(atual, remoto) se o GitHub tem commit novo que entra por fast-forward; senão None."""
    codigo, saida = git(repo, 'fetch', '--quiet', 'origin', rodar=rodar)
    if codigo:
        raise Falha(f"git fetch falhou em {repo}: {saida.strip()[:200]}")
    ramo = git(repo, 'rev-parse', '--abbrev-ref', 'HEAD', rodar=rodar)[1].strip()
    atual, remoto = head(repo, rodar), git(repo, 'rev-parse', f'origin/{ramo}', rodar=rodar)[1].strip()
    if atual == remoto or git(repo, 'merge-base', '--is-ancestor', remoto, atual, rodar=rodar)[0] == 0:
        return None   # em dia (ou o servidor está à frente)
    if git(repo, 'merge-base', '--is-ancestor', atual, remoto, rodar=rodar)[0] != 0:
        raise Falha(f"{repo}: a versão do servidor tem commits que não estão no GitHub; atualize à mão.")
    return atual, remoto


def avancar(repo, remoto, protegidos=(), rodar=rodar):
    codigo, saida = git(repo, 'merge', '--ff-only', remoto, rodar=rodar)
    if codigo:
        presos = [f for f in protegidos if f in saida]
        if not presos:
            raise Falha(f"git recusou atualizar {repo}: {saida.strip()[:300]}")
        for nome in presos:   # há cópia: devolvida logo depois do merge
            if git(repo, 'ls-files', '--error-unmatch', nome, rodar=rodar)[0] == 0:
                git(repo, 'checkout', '--', nome, rodar=rodar)    # versionado e alterado no servidor
            else:
                os.remove(os.path.join(repo, nome))               # o commit novo passou a versionar o arquivo
        codigo, saida = git(repo, 'merge', '--ff-only', remoto, rodar=rodar)
        if codigo:
            raise Falha(f"git recusou atualizar {repo}: {saida.strip()[:300]}")


def guardar_dados(repo):
    destino = os.path.join(pasta(), f"backup-{datetime.now():%Y%m%d-%H%M%S}")
    os.makedirs(destino, exist_ok=True)
    for nome in DADOS:
        if os.path.exists(os.path.join(repo, nome)):
            shutil.copy2(os.path.join(repo, nome), destino)
    for velho in sorted(d for d in os.listdir(pasta()) if d.startswith('backup-'))[:-BACKUPS_GUARDADOS]:
        shutil.rmtree(os.path.join(pasta(), velho), ignore_errors=True)
    return destino


def devolver_dados(repo, copia):
    for nome in os.listdir(copia):
        shutil.copy2(os.path.join(copia, nome), os.path.join(repo, nome))


# ---------- gerenciador ----------
def _python():
    """python.exe ao lado do pythonw.exe que roda o atualizador (pythonw não abre janela)."""
    candidato = os.path.join(os.path.dirname(sys.executable), 'python.exe' if os.name == 'nt' else 'python3')
    return candidato if os.path.exists(candidato) else sys.executable


def rodar_testes(repo, rodar=rodar):
    codigo, saida = rodar([_python(), '-m', 'unittest', 'discover', '-p', 'test_*.py'], repo)
    return codigo == 0, (saida.strip().splitlines() or [''])[-1]


def reiniciar_gerenciador(pid, rodar=rodar):
    if pid:
        rodar(['taskkill', '/PID', str(pid), '/T', '/F'], RAIZ, timeout=60)   # /T leva junto o Chrome dos robôs
        time.sleep(5)
    subprocess.Popen(f'start "Maestro Gerenciador" "{_python()}" gerenciador.py', cwd=RAIZ, shell=True)


def atualizar_gerenciador(rodar=rodar, testar=None, reiniciar=reiniciar_gerenciador):
    testar = testar or (lambda repo: rodar_testes(repo, rodar))
    estado = ler_estado()
    nova = novidade(RAIZ, rodar)
    # rodando com código velho: atualização baixada numa rodada em que um robô começou a trabalhar, ou pull à mão
    desatualizado = bool(estado and estado.get('versao') and estado['versao'] != head(RAIZ, rodar))
    if not (nova or desatualizado):
        return
    if ocupado(estado):
        log(f"Gerenciador: atualização pendente; robôs ocupados ({', '.join(estado['ocupados'])}).")
        return
    if nova:
        atual, remoto = nova
        copia = guardar_dados(RAIZ)
        try:
            avancar(RAIZ, remoto, DADOS, rodar)
        finally:
            devolver_dados(RAIZ, copia)
        passou, resumo = testar(RAIZ)
        if not passou:
            git(RAIZ, 'reset', '--hard', atual, rodar=rodar)
            devolver_dados(RAIZ, copia)
            avisar('gerenciador-testes', 'erro', f"Atualização do gerenciador cancelada: testes falharam em {remoto[:7]} "
                                                 f"({resumo[:120]}). Continua na versão {atual[:7]}.", email=True)
            return
        avisar('gerenciador', 'ok', f"Gerenciador atualizado: {atual[:7]} -> {remoto[:7]} ({resumo[:60]}).")
    if not estado:
        log("Gerenciador não está rodando: código atualizado, mas não foi iniciado.")
        return
    reiniciar(estado.get('pid'))
    avisar('gerenciador-reinicio', 'ok', f"Gerenciador reiniciado na versão {head(RAIZ, rodar)[:7]}.")


# ---------- portal ----------
def _pm2_lista(rodar):
    codigo, saida = rodar([PM2, 'jlist'], RAIZ, timeout=60)
    if codigo:
        raise Falha(f"pm2 jlist falhou: {saida.strip()[:200]}")
    inicio = saida.find('[')   # o pm2 às vezes escreve avisos antes do JSON
    try:
        return json.loads(saida[inicio:]) if inicio >= 0 else []
    except ValueError:
        raise Falha("pm2 jlist não devolveu JSON")


def _portal(rodar):
    return next((p for p in _pm2_lista(rodar) if p.get('name') == PORTAL_PM2), None)


def portal_de_pe(rodar, espera=time.sleep):
    """Online e sem reiniciar sozinho por 10 s (erro ao subir faz o pm2 reiniciar em laço)."""
    espera(15)
    antes = _portal(rodar)
    espera(10)
    depois = _portal(rodar)
    env = lambda p: (p or {}).get('pm2_env', {})
    return bool(antes and depois and env(depois).get('status') == 'online'
                and env(antes).get('restart_time') == env(depois).get('restart_time'))


def _instalar_e_reiniciar(repo, deps, rodar):
    if deps:
        codigo, saida = rodar([NPM, 'ci', '--omit=dev', '--no-audit', '--no-fund'], repo)
        if codigo:
            raise Falha(f"npm ci falhou: {saida.strip()[-200:]}")
    codigo, saida = rodar(['node', '--check', 'server.js'], repo, timeout=60)
    if codigo:
        raise Falha(f"server.js com erro de sintaxe: {saida.strip()[:200]}")
    codigo, saida = rodar([PM2, 'restart', PORTAL_PM2], repo, timeout=120)
    if codigo:
        raise Falha(f"pm2 restart falhou: {saida.strip()[:200]}")


def atualizar_portal(rodar=rodar, espera=time.sleep):
    processo = _portal(rodar)
    if not processo:
        log(f"Portal: processo {PORTAL_PM2} não encontrado no pm2.")
        return
    repo = processo.get('pm2_env', {}).get('pm_cwd')
    nova = novidade(repo, rodar)
    if not nova:
        return
    if ocupado(ler_estado()):   # o restart do portal derruba a conexão do gerenciador
        log("Portal: atualização pendente; robôs ocupados.")
        return
    atual, remoto = nova
    deps = any(n.startswith('package') for n in git(repo, 'diff', '--name-only', atual, remoto, rodar=rodar)[1].split())
    avancar(repo, remoto, (), rodar)
    try:
        _instalar_e_reiniciar(repo, deps, rodar)
        if not portal_de_pe(rodar, espera):
            raise Falha("o site não ficou de pé depois do restart")
    except Falha as e:
        git(repo, 'reset', '--hard', atual, rodar=rodar)
        motivo = str(e)
        try:
            _instalar_e_reiniciar(repo, deps, rodar)
        except Falha as e2:
            motivo += f"; e a volta para {atual[:7]} também falhou: {e2}"
        avisar('portal', 'erro', f"Atualização do portal cancelada ({motivo}). Voltou para {atual[:7]}.", email=True)
        return
    avisar('portal', 'ok', f"Portal atualizado: {atual[:7]} -> {remoto[:7]} e reiniciado ({PORTAL_PM2}).")


def main():
    trava = open(os.path.join(pasta(), 'atualizador.trava'), 'a+')
    try:
        instancia_unica._travar(trava)
    except OSError:
        return   # a rodada anterior ainda não terminou (testes ou npm demorando)
    eventos.ativar()
    if not ligado(ambiente.ler_config()):
        return
    for nome, etapa in (('gerenciador', atualizar_gerenciador), ('portal', atualizar_portal)):
        try:
            etapa()
        except Falha as e:
            avisar(nome, 'erro', f"Atualização automática ({nome}): {e}", email=True)
        except Exception as e:
            log(f"Erro inesperado ({nome}): {e!r}")
            avisar(nome, 'erro', f"Atualização automática ({nome}) com erro inesperado: {e}", email=True)


if __name__ == '__main__':
    main()
