"""Atualização automática do gerenciador e do portal (rodado a cada 5 min pelo Agendador de Tarefas).

Gerenciador: sem nada em andamento, guarda os arquivos de dados, avança para o commit novo do GitHub, devolve os
arquivos de dados (o pull pode apagá-los, como em 21/10), roda os testes e pede o reinício; teste que falha volta a versão.
Reinício suave (reinicio.py): o atualizador não mata nem abre o gerenciador. Grava o pedido e o gerenciador troca
a si mesmo quando fica livre, no mesmo contexto em que foi aberto. Robô externo ligado 24/7 (ME, Ariba, Findes,
ComprasGov) não segura a atualização nem é fechado por ela; só tarefa do Coupa/Vale ou comando de usuário no meio.
Portal: avança, instala dependências se mudaram, confere a sintaxe e faz pm2 restart; site que não sobe volta a versão.
Só reinicia o portal sem usuário mexendo nos últimos minutos (portal.estado). Liga/desliga: "atualizacao_automatica"
no CONFIG.json (Configurações do dashboard2). Resultado na Atividade; erro também por e-mail.

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
import reinicio        # noqa: E402

DADOS = ('CONFIG.json', '.env', 'banco_usuarios.json', 'banco_ips_bloqueados.json')   # do servidor: produção vence
DADOS_PORTAL = ('.env', 'dados/prints_respostas.json', 'dados/erros_respostas.json')
PORTAL_PM2 = 'Site-Maestro'
ESTADO_VALIDO_S = 120           # gerenciador.estado mais velho que isso = gerenciador não está rodando
AVISO_ESPERA_S = 2 * 3600       # atualização esperando robôs há mais que isso: avisa na Atividade (e continua esperando)
REPETIR_AVISO_S = 6 * 3600      # o mesmo erro não é avisado a cada 5 min
ESPERA_TROCA_S = 60             # quanto esta rodada espera o gerenciador trocar; ocupado, ele troca depois sozinho
PASSO_TROCA_S = 2
PORTAL_QUIETO_S = 180           # portal só reinicia sem ação de usuário há mais que isso
ROBOS_INTERNOS = ('coupa', 'vale')   # rodam dentro do gerenciador; os outros têm janela própria e sobrevivem ao reinício
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


def em_andamento(estado):
    """O que segura a atualização. O gerenciador novo ('suave') já lista só isso; o antigo listava também os robôs
    externos enquanto ligados (24/7: a atualização nunca entrava), que aqui não contam."""
    nomes = list((estado or {}).get('ocupados') or [])
    return nomes if (estado or {}).get('suave') else [n for n in nomes if n in ROBOS_INTERNOS]


def ocupado(estado):
    """True se há algo em andamento (só chamado com atualização pendente). Espera longa é avisada uma vez."""
    ctl = _controle()
    nomes = em_andamento(estado)
    if not nomes:
        if ctl.pop('espera', None) is not None:
            _salvar_controle(ctl)
        return False
    espera = ctl.setdefault('espera', {'desde': time.time(), 'avisado': False})
    avisa = not espera['avisado'] and time.time() - espera['desde'] > AVISO_ESPERA_S
    espera['avisado'] = espera['avisado'] or avisa
    _salvar_controle(ctl)
    if avisa:
        avisar('espera', 'aviso', f"Atualização esperando há mais de {AVISO_ESPERA_S // 3600} h: em andamento "
                                  f"({', '.join(nomes)}).")
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
        presos = [f for f in protegidos if f in saida or f.replace('/', os.sep) in saida]
        if not presos:
            raise Falha(f"git recusou atualizar {repo}: {saida.strip()[:300]}")
        for nome in presos:   # há cópia: devolvida logo depois do merge
            if git(repo, 'ls-files', '--error-unmatch', nome, rodar=rodar)[0] == 0:
                git(repo, 'checkout', '--', nome, rodar=rodar)    # versionado e alterado no servidor
            else:
                caminho_preso = os.path.join(repo, *nome.split('/'))
                if os.path.exists(caminho_preso):
                    os.remove(caminho_preso)                      # o commit novo passou a versionar o arquivo
        codigo, saida = git(repo, 'merge', '--ff-only', remoto, rodar=rodar)
        if codigo:
            raise Falha(f"git recusou atualizar {repo}: {saida.strip()[:300]}")


def guardar_dados(repo, nomes=DADOS):
    destino = os.path.join(pasta(), f"backup-{datetime.now():%Y%m%d-%H%M%S}-{os.path.basename(str(repo).rstrip('/\\')) or 'repo'}")
    os.makedirs(destino, exist_ok=True)
    for nome in nomes:
        origem_arq = os.path.join(repo, *nome.split('/'))
        if os.path.exists(origem_arq):
            dest_arq = os.path.join(destino, *nome.split('/'))
            os.makedirs(os.path.dirname(dest_arq), exist_ok=True)
            shutil.copy2(origem_arq, dest_arq)
    for velho in sorted(d for d in os.listdir(pasta()) if d.startswith('backup-'))[:-BACKUPS_GUARDADOS]:
        shutil.rmtree(os.path.join(pasta(), velho), ignore_errors=True)
    return destino


def devolver_dados(repo, copia):
    for raiz_atual, _, arquivos in os.walk(copia):
        rel_dir = os.path.relpath(raiz_atual, copia)
        for nome in arquivos:
            origem_arq = os.path.join(raiz_atual, nome)
            dest_arq = os.path.join(repo, nome) if rel_dir == '.' else os.path.join(repo, rel_dir, nome)
            os.makedirs(os.path.dirname(dest_arq), exist_ok=True)
            shutil.copy2(origem_arq, dest_arq)


# ---------- gerenciador ----------
def _python():
    """python.exe ao lado do pythonw.exe que roda o atualizador (pythonw não abre janela)."""
    candidato = os.path.join(os.path.dirname(sys.executable), 'python.exe' if os.name == 'nt' else 'python3')
    return candidato if os.path.exists(candidato) else sys.executable


def rodar_testes(repo, rodar=rodar):
    pasta_testes = 'teste' if os.path.isdir(os.path.join(repo, 'teste')) else '.'
    codigo, saida = rodar([_python(), '-m', 'unittest', 'discover', '-s', pasta_testes, '-p', 'test_*.py'], repo)
    return codigo == 0, (saida.strip().splitlines() or [''])[-1]


# Log central do gerenciador (gerenciador.py grava nele): o reinício aparece entre as linhas dele, com hora
LOG_CENTRAL = r"\\SERVIDOR2\Publico\ALLAN\Logs\gerenciador.txt"


def log_central(msg):
    """Linha no gerenciador.txt da rede e no logs/gerenciador.log local; sem rede só fica no log do atualizador."""
    log(msg)
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S},000 [PID {os.getpid()}] [atualizador] atualizador WARNING {msg}\n"
    for caminho in (LOG_CENTRAL, os.path.join(RAIZ, 'logs', 'gerenciador.log')):
        try:
            with open(caminho, 'a', encoding='utf-8') as f:
                f.write(linha)
        except OSError as e:
            log(f"Falha ao gravar em {caminho}: {e}")


def iniciar_gerenciador():
    """Reserva: abre o gerenciador a partir do atualizador (nasce no contexto da tarefa agendada, não no de quem o
    abriu). Só para o gerenciador antigo, que não reinicia sozinho, ou para um que não voltou depois do pedido."""
    # python.exe em console próprio: volta na tela como quem abriu à mão (pythonw sumia em segundo plano)
    if os.name == 'nt':
        subprocess.Popen([_python(), 'gerenciador.py'], cwd=RAIZ, creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        subprocess.Popen([_python(), 'gerenciador.py'], cwd=RAIZ, start_new_session=True)


def reiniciar_gerenciador(pid, rodar=rodar, iniciar=iniciar_gerenciador, espera=time.sleep):
    """À força, só para o gerenciador antigo. Sem /T: os robôs externos (ME, Ariba, Findes) seguem ligados; o Chrome
    do Coupa/Vale que sobrar é fechado pelo navegador.preparar no próximo ligar."""
    if pid:
        rodar(['taskkill', '/PID', str(pid), '/F'], RAIZ, timeout=60)
        espera(5)
    iniciar()


def pedir_reinicio(estado, versao, ler=ler_estado, espera=time.sleep):
    """Grava o pedido e espera um pouco. True se o gerenciador já trocou de processo para a versão nova; False se
    segue ocupado (ele troca sozinho quando ficar livre; a rodada seguinte confere)."""
    reinicio.pedir(RAIZ, versao)
    for _ in range(ESPERA_TROCA_S // PASSO_TROCA_S):
        espera(PASSO_TROCA_S)
        novo = ler()
        if novo and novo.get('pid') != estado.get('pid') and novo.get('versao') == versao:
            return True
    return False


def _conferir_pedido(estado, iniciar):
    """Pedido de reinício de uma rodada anterior: voltou na versão nova (avisa), sumiu (abre e avisa) ou ainda espera.
    True se a rodada acaba aqui."""
    ctl = _controle()
    pedido = ctl.get('pedido')
    if not pedido:
        return False
    if estado and estado.get('versao') != pedido['versao']:
        return False                                   # ainda ocupado na versão antiga: o pedido continua valendo
    ctl.pop('pedido')
    _salvar_controle(ctl)
    if estado:
        avisar('gerenciador-reinicio', 'ok', f"Gerenciador reiniciado na versão {pedido['versao'][:7]}.")
        return False
    reinicio.limpar_pedido(RAIZ)
    log_central(f"Gerenciador não voltou depois do pedido de reinício para {pedido['versao'][:7]}: aberto pelo atualizador.")
    iniciar()
    avisar('gerenciador-reinicio', 'erro', f"O gerenciador fechou para atualizar para {pedido['versao'][:7]} e não voltou "
                                           f"sozinho; foi aberto pelo atualizador. Confira o logs/gerenciador.log.", email=True)
    return True


def atualizar_gerenciador(rodar=rodar, testar=None, reiniciar=reiniciar_gerenciador, pedir=pedir_reinicio,
                          iniciar=iniciar_gerenciador):
    testar = testar or (lambda repo: rodar_testes(repo, rodar))
    estado = ler_estado()
    if _conferir_pedido(estado, iniciar):
        return
    nova = novidade(RAIZ, rodar)
    # rodando com código velho: atualização baixada numa rodada em que um robô começou a trabalhar, ou pull à mão
    desatualizado = bool(estado and estado.get('versao') and estado['versao'] != head(RAIZ, rodar))
    if not (nova or desatualizado):
        return
    suave = bool(estado and estado.get('suave'))
    if (nova or not suave) and ocupado(estado):   # baixar código com tarefa no meio mistura versões; o antigo é morto
        log(f"Gerenciador: atualização pendente; em andamento ({', '.join(em_andamento(estado))}).")
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
    versao = head(RAIZ, rodar)
    versoes = f"versão {str(estado.get('versao') or '?')[:7]} -> {versao[:7]}"
    if not suave:   # gerenciador de antes do reinício suave: não lê pedido; esta é a última vez à força
        log_central(f"Gerenciador PID {estado.get('pid')} encerrado pelo atualizador automático para reiniciar: {versoes}.")
        reiniciar(estado.get('pid'))
        avisar('gerenciador-reinicio', 'ok', f"Gerenciador reiniciado na versão {versao[:7]}.")
        return
    ctl = _controle()
    if (ctl.get('pedido') or {}).get('versao') == versao:
        reinicio.pedir(RAIZ, versao)   # já pedido numa rodada anterior: só garante o arquivo, sem esperar de novo
        return
    log_central(f"Gerenciador PID {estado.get('pid')}: reinício pedido pelo atualizador automático ({versoes}). "
                f"Ele troca sozinho quando nada estiver em andamento; os robôs externos seguem ligados.")
    if pedir(estado, versao):
        avisar('gerenciador-reinicio', 'ok', f"Gerenciador reiniciado na versão {versao[:7]}.")
        return
    ctl = _controle()
    ctl['pedido'] = {'versao': versao, 'em': time.time()}
    _salvar_controle(ctl)
    log("Gerenciador: pedido de reinício entregue; ele troca quando ficar livre.")


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


def portal_em_uso(repo):
    """Alguém mexeu no site nos últimos PORTAL_QUIETO_S? O portal grava portal.estado (lib/atividade-usuarios.js).
    Sem o arquivo, ou com ele velho (portal de antes disso, ou caído), não segura a atualização."""
    try:
        with open(os.path.join(repo, 'portal.estado'), encoding='utf-8') as f:
            estado = json.load(f)
        agora = time.time() * 1000
        if agora - estado['em'] > ESTADO_VALIDO_S * 1000:
            return False
        return agora - estado['ultima_acao_em'] < PORTAL_QUIETO_S * 1000
    except (OSError, ValueError, KeyError, TypeError):
        return False


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
        log("Portal: atualização pendente; gerenciador com tarefa em andamento.")
        return
    if portal_em_uso(repo):     # o restart derruba quem está no meio de uma ação no site
        log("Portal: atualização pendente; usuário usando o site agora.")
        return
    atual, remoto = nova
    deps = any(n.startswith('package') for n in git(repo, 'diff', '--name-only', atual, remoto, rodar=rodar)[1].split())
    copia = guardar_dados(repo, DADOS_PORTAL)
    try:
        avancar(repo, remoto, DADOS_PORTAL, rodar)
    finally:
        devolver_dados(repo, copia)
    try:
        _instalar_e_reiniciar(repo, deps, rodar)
        if not portal_de_pe(rodar, espera):
            raise Falha("o site não ficou de pé depois do restart")
    except Falha as e:
        git(repo, 'reset', '--hard', atual, rodar=rodar)
        devolver_dados(repo, copia)
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
