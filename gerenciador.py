# -*- coding: utf-8 -*-
import sys
if __name__ == '__main__':
    # Bots fazem "from gerenciador import ...": sem isto o Python roda este arquivo
    # de novo dentro do mesmo processo e sobe um segundo piloto automático.
    sys.modules.setdefault('gerenciador', sys.modules['__main__'])
import socketio
import threading
import logging
import base64
import tempfile
import openpyxl
import os
import time
import json
import uuid
from datetime import datetime
import subprocess
import glob
import shutil
import unicodedata
from dotenv import load_dotenv, dotenv_values
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from logging.handlers import RotatingFileHandler

# Ler só o segredo aqui preserva a ordem das outras opções e não ativa o modo de teste local por acidente.
ROBO_SECRET = os.getenv("ROBO_SECRET")
if ROBO_SECRET is None:
    ROBO_SECRET = dotenv_values(encoding='utf-8-sig').get("ROBO_SECRET") or ""
if __name__ == '__main__' and len(ROBO_SECRET) < 32:
    raise SystemExit("Configure ROBO_SECRET com pelo menos 32 caracteres, igual ao segredo do servidor Maestro.")

PASTA_LOGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(PASTA_LOGS, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [PID %(process)d] [%(threadName)s] %(name)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(), RotatingFileHandler(
        os.path.join(PASTA_LOGS, 'gerenciador.log'), maxBytes=5_000_000, backupCount=5, encoding='utf-8')],
)
logger = logging.getLogger(__name__)


def sem_acento(texto):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(texto)) if not unicodedata.combining(c))


def classificar_resposta(valor):
    """'respondida', 'nao_respondida' ou '' (pendente). Comparação exata: "NÃO RESPONDIDO" contém "RESPONDIDO"."""
    texto = sem_acento(valor if valor is not None else '').strip().upper()
    if texto in ('RESPONDIDO', 'RESPONDIDA'):
        return 'respondida'
    # startswith('N') também pega o "NÃƒO" com acento quebrado que existe na base
    if texto.startswith('N') and texto.endswith(('RESPONDIDO', 'RESPONDIDA')):
        return 'nao_respondida'
    return ''


def carregar_config():
    path = 'CONFIG.json'
    if not os.path.exists(path):
        default_config = {
            "SMTP_USER": os.environ.get("SMTP_USER", ""),
            "SMTP_PASSWORD": os.environ.get("SMTP_PASSWORD", ""),
            "pasta_me": r"\\SERVIDOR2\Publico\ALLAN\MERCADO-ELETRONICO",
            "pasta_coupa_vale": r"\\SERVIDOR2\Publico\ALLAN\eventos do coupa",
            "pasta_ariba_aegea": r"\\SERVIDOR2\Publico\ALLAN\AribaSourcing\AEGEA",
            "pasta_ariba_estacio": r"\\SERVIDOR2\Publico\ALLAN\AribaSourcing\ESTÁCIO",
            "caminho_excel": r"\\SERVIDOR2\Publico\PLANILHA DE CONTROLE VALE - ESTAGIARIOS (copia 1).xlsx",
            "caminho_bat_me": r"C:\Users\Administrator\Desktop\mercadoeletronico.bat",
            "caminho_ariba": r"C:\Users\Administrator\Desktop\AribaSourcing",
            "caminho_findes": r"C:\Users\Administrator\Desktop\FINDES",
            "emails_relatorio": os.environ.get("EMAILS_RELATORIO", "")
        }
        default_config = com_padroes(default_config)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=4, ensure_ascii=False)
        return default_config
    with open(path, 'r', encoding='utf-8-sig') as f:   # Bloco de Notas salva com BOM
        config = json.load(f)
    completa = com_padroes(config)
    if completa != config:   # grava as chaves novas para elas aparecerem no painel (Configurações do dashboard2)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(completa, f, indent=4, ensure_ascii=False)
    return completa


# Chaves que o painel sempre mostra; o valor salvo lá vale na próxima verificação (ambiente.paginas_verificacao)
PADROES_CONFIG = {'coupa_verificacao_max_paginas': 60, 'vale_verificacao_max_paginas': 60,
                  'atualizacao_automatica': 'sim'}   # scripts/atualizador.py; 'não' desliga


def com_padroes(config):
    return {**config, **{k: v for k, v in PADROES_CONFIG.items() if config.get(k) in (None, '')}}

CONFIG_GLOBAL = carregar_config()

# MODO DE TESTE LOCAL: ambiente.py decide (antes só "http://localhost:8000"; o testar_local.bat usa 127.0.0.1 e caía na produção)
import ambiente
if ambiente.modo_teste():
    os.makedirs(ambiente.pasta_banco(), exist_ok=True)
    CONFIG_GLOBAL["caminho_excel"] = os.path.join(ambiente.RAIZ, "teste.xlsx")
    CONFIG_GLOBAL["caminho_database_coupa"] = os.path.join(ambiente.pasta_database(), "eventos.jsonl")
    CONFIG_GLOBAL["caminho_pedidos_json"] = os.path.join(ambiente.pasta_database(), "pedidos.json")
    CONFIG_GLOBAL["caminho_database_vale"] = os.path.join(ambiente.pasta_database(), "eventos_nimbi.jsonl")
    CONFIG_GLOBAL["caminho_banco_dados"] = ambiente.pasta_banco()
    CONFIG_GLOBAL["caminho_backups"] = os.path.join(ambiente.pasta_database(), "backups_excel")
else:
    CONFIG_GLOBAL["caminho_banco_dados"] = r"\\SERVIDOR2\Publico\ALLAN\database\Banco-de-dados"
    CONFIG_GLOBAL["caminho_backups"] = r"\\SERVIDOR2\Publico\ALLAN\database\backups_excel"

import eventos
if __name__ == '__main__':
    eventos.ativar()   # só o gerenciador de verdade grava eventos; testes importam sem gravar
    # Dois gerenciadores na mesma base brigam pelos JSONs (13-14/10: 790 gravações recusadas)
    from instancia_unica import JaRodando, instancia_unica
    try:
        _TRAVA_INSTANCIA = instancia_unica(CONFIG_GLOBAL["caminho_banco_dados"])
    except JaRodando as e:
        logger.error(f"Já existe um gerenciador rodando ({e}). Feche-o antes de abrir outro.")
        # ponytail: o recusado acrescenta 1 linha no arquivo do que está rodando; nunca limpa nem regrava
        eventos.registrar('gerenciador', 'instancia', 'aviso', f'Segundo gerenciador recusado: já existe um rodando ({e})')
        try: input("Pressione Enter para fechar esta janela...")
        except EOFError: pass
        sys.exit(3)
    eventos.limpar()

from planilha_manager import planilha_manager
planilha_manager.iniciar(CONFIG_GLOBAL["caminho_excel"])

def salvar_config(novas_config):
    with open('CONFIG.json', 'w', encoding='utf-8') as f:
        json.dump(novas_config, f, indent=4, ensure_ascii=False)


# Estado para o atualizador automático (scripts/atualizador.py): ele só reinicia com a lista "ocupados" vazia
ARQ_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gerenciador.estado')
ESTADO_INTERVALO_S = 20


def versao_do_codigo():
    """Commit com que este processo subiu: diferente do que está no disco = falta reiniciar."""
    try:
        return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=os.path.dirname(os.path.abspath(__file__)),
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ''


VERSAO = versao_do_codigo()


def robos_ocupados():
    ocupados = []
    for nome in ('coupa', 'vale'):
        bot = globals().get(f'bot_{nome}')
        th = getattr(bot, 'thread_atual', None) if bot else None
        if th is not None and th.is_alive():
            ocupados.append(nome)
    for nome, var in (('me', 'processo_me'), ('ariba', 'bot_ariba'), ('findes', 'bot_findes')):
        proc = globals().get(var)
        if proc is not None and proc.poll() is None:
            ocupados.append(nome)
    return ocupados


def gravar_estado():
    tmp = ARQ_ESTADO + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'pid': os.getpid(), 'em': time.time(), 'versao': VERSAO, 'ocupados': robos_ocupados()}, f)
    os.replace(tmp, ARQ_ESTADO)


def loop_estado():
    while True:
        try:
            gravar_estado()
        except Exception:
            logger.exception('Falha ao gravar gerenciador.estado')
        time.sleep(ESTADO_INTERVALO_S)

if __name__ == '__main__':   # só o processo do gerenciador; import nos testes não liga
    threading.Thread(target=loop_estado, daemon=True).start()

# CREDENCIAIS DA LOCAWEB
SMTP_USER = CONFIG_GLOBAL.get("SMTP_USER", os.environ.get("SMTP_USER", ""))
SMTP_PASSWORD = CONFIG_GLOBAL.get("SMTP_PASSWORD", os.environ.get("SMTP_PASSWORD", ""))

def url_base_site():
    # Links dos e-mails apontam para o mesmo servidor ao qual o gerenciador esta conectado
    # (localhost no teste local, dominio oficial em producao). Pode ser sobrescrito com URL_SITE.
    base = os.getenv("URL_SITE") or os.getenv("URL_SERVIDOR") or "https://maestro.ventura.inf.br"
    return base.rstrip('/')

def enviar_email_python(email_destino, token, tipo="ativacao"):
    if tipo == "ativacao":
        assunto = 'MAESTRO - Confirme o seu E-mail'
        link = f"{url_base_site()}/index.html?action=verify&token={token}"
        mensagem = "Voc solicitou acesso ao sistema Maestro. Clique no link para ativar a sua conta:"
        botao = "ATIVAR A MINHA CONTA"
    else:
        assunto = 'MAESTRO - Recuperao de Senha'
        link = f"{url_base_site()}/index.html?action=reset&token={token}"
        mensagem = "Voc solicitou a recuperao da sua senha. Clique no link para criar uma nova senha:"
        botao = "REDEFINIR MINHA SENHA"

    corpo = f"""
    <div style="font-family: Arial; padding: 20px; background: #0f1115; color: #fff; text-align: center; border-radius: 8px;">
        <h2 style="color: #3b82f6;">MAESTRO CORE</h2>
        <p>{mensagem}</p>
        <a href="{link}" style="background: #3b82f6; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block; margin-top: 15px; font-weight: bold;">{botao}</a>
    </div>
    """

    try:
        logger.info(f"? Tentando enviar e-mail para {email_destino} via Locaweb ({tipo})...")

        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = email_destino
        msg['Subject'] = assunto
        msg.attach(MIMEText(corpo, 'html'))

        # A Locaweb geralmente utiliza o servidor 'email-ssl.com.br' na porta 587
        server = smtplib.SMTP('email-ssl.com.br', 587, timeout=15)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()

        logger.info(f"? E-mail ({tipo}) enviado para {email_destino}.")
        if "localhost" in link or "127.0.0.1" in link:
            logger.info(f"[TESTE LOCAL] Link do e-mail: {link}")
        return True
    except Exception as e:
        logger.error(f"? Erro no envio pela Locaweb: {e}")
        return False

def enviar_email_aviso_limpeza(vendedor, eventos_apagados):
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    emails_destino_raw = CONFIG_GLOBAL.get("emails_relatorio", os.environ.get("EMAILS_RELATORIO", ""))
    destinatarios = [e.strip() for e in emails_destino_raw.split(',') if e.strip()]
    if not destinatarios: return

    assunto = "Maestro - Limpeza de Cotações para Re-Extrao"
    corpo = f"""Ol,

Foi solicitada a atribuio do vendedor "{vendedor}" pelo painel do Maestro.
No entanto, as seguintes cotaes no estavam presentes na planilha:

{', '.join(eventos_apagados)}

Para corrigir isso, elas foram apagadas da memria do rob. 
Na prxima rodada automtica, o rob ir extra-las novamente do portal e as salvar na planilha corretamente.

Atenciosamente,
Sistema Maestro
"""

    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = ", ".join(destinatarios)
        msg['Subject'] = assunto
        msg.attach(MIMEText(corpo, 'plain'))

        server = smtplib.SMTP('email-ssl.com.br', 587, timeout=10)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"[MAESTRO] E-mail de aviso de limpeza enviado com sucesso para {', '.join(destinatarios)}")
    except Exception as e:
        print(f"[MAESTRO] Erro ao enviar e-mail de aviso de limpeza: {e}")

load_dotenv(encoding='utf-8-sig')
URL_DO_SERVIDOR = os.getenv("URL_SERVIDOR", "https://maestro.ventura.inf.br")
sio = socketio.Client()

# MONKEY-PATCH: Proteo Global contra quedas de Socket
_original_emit = sio.emit
def _safe_emit(*args, **kwargs):
    try:
        _original_emit(*args, **kwargs)
    except Exception as e:
        logger.warning(f"Falha no Socket.IO (emit): {e}")
sio.emit = _safe_emit

bot_coupa = None
bot_vale = None
portal_atual = None
bot_findes = None
processo_me = None
bot_ariba = None
rodando_monitor_findes = False
rodando_monitor_me = False
rodando_monitor_ariba = False
rodando_monitor_coupa = False
rodando_monitor_vale = False
aguardando_clique = threading.Event()
coordenadas_clique = {'x': 0, 'y': 0}

# No topo do arquivo gerenciador.py

# 1. FUNO PARA SALVAR NO MESMO FORMATO
def salvar_usuarios(usuarios):
    with open('banco_usuarios.json', 'w', encoding='utf-8') as f:
        json.dump(usuarios, f, indent=4, ensure_ascii=False)

@sio.on('registrar_usuario')
def registrar_usuario(dados):
    email = dados.get('email', '').lower()
    senha = dados.get('senha')
    ip_real = dados.get('ip_real', 'Desconhecida')
    client_id = dados.get('clientId')

    # ?? REGRA: Apenas domnio especfico
    if not email.endswith('@venturainformatica.com.br'):
        logger.warning(f"? Domnio invlido: {email}")
        sio.emit('resposta_cadastro', {
            'sucesso': False, 
            'erro': 'Utilize o seu e-mail @venturainformatica.com.br',
            'clientId': client_id
        })
        return

    usuarios = carregar_usuarios()
    if email in usuarios:
        sio.emit('resposta_cadastro', {'sucesso': False, 'erro': 'E-mail j cadastrado.', 'clientId': client_id})
        return

    token = str(uuid.uuid4())
    usuarios[email] = {
        "senha": senha,
        "verificado": False, # ??? Fica False at clicar no link
        "token_verificacao": token,
        "token_recuperacao": None,
        "ultima_localizacao": f"IP: {ip_real}",
        "ultimo_login": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "admin": False
    }

    # ... (cdigo acima continua igual, com a trava do domnio e a criao do json) ...

    salvar_usuarios(usuarios)
    logger.info(f"? Conta pr-criada: {email}. Acionando o Carteiro Python!")

    # ?? AGORA O PRPRIO PYTHON ENVIA O E-MAIL ??
    sucesso_email = enviar_email_python(email, token)

    if sucesso_email:
        logger.info("? E-mail enviado com sucesso pelo rob!")
        # Avisa a Nuvem que tudo correu bem, para ela avisar o site
        sio.emit('resposta_cadastro', {'sucesso': True, 'clientId': client_id})
    else:
        # Se falhar (ex: senha bloqueada), apaga o utilizador fantasma do json
        del usuarios[email]
        salvar_usuarios(usuarios)
        sio.emit('resposta_cadastro', {
            'sucesso': False, 
            'erro': 'Erro ao enviar o e-mail pelo Rob Local.', 
            'clientId': client_id
        })
# ====================================================
# MOTOR DE CRIPTOGRAFIA (E2EE) NO PYTHON
# ====================================================
def cifra_python(texto, chave):
    try:
        text_bytes = texto.encode('utf-8')
        key_bytes = chave.encode('utf-8')
        cifrado = bytearray(len(text_bytes))
        for i in range(len(text_bytes)):
            cifrado[i] = text_bytes[i] ^ key_bytes[i % len(key_bytes)]

        # Converte para Base64 para poder enviar pela rede com segurana
        return base64.b64encode(cifrado).decode('utf-8')
    except Exception as e:
        logger.error(f"Erro ao cifrar: {e}")
        return None

def decifra_python(b64_texto, chave):
    try:
        binary = base64.b64decode(b64_texto)
        key_bytes = chave.encode('utf-8')
        original = bytearray(len(binary))
        for i in range(len(binary)):
            original[i] = binary[i] ^ key_bytes[i % len(key_bytes)]

        return original.decode('utf-8')
    except Exception:
        return None

def _registrar_evento_linha_log(portal, linha_limpa):
    """Analisa a linha do arquivo TXT com try/except para registrar erros ou fim de sessão no portal."""
    try:
        t = str(linha_limpa or '').strip()
        if not t or 'Fechando modal' in t or 'Procurando bot' in t:
            return
        tl = t.lower()
        if any(p in tl for p in ('[error]', '[critical]', 'traceback', 'exception', 'erro crítico', 'erro critico', 'erro na ', 'erro no ', 'erro ao ', 'falha:', 'falha no login', 'tempo esgotado!', 'invalid session id')):
            eventos.registrar(portal, 'erro_log', 'erro', t[:200])
        elif any(p in tl for p in ('sessão finalizada', 'sesso finalizada', 'aplicação finalizada', 'aplicao finalizada', 'extração concluída', 'extracao concluida', 'processamento concluído', 'processamento concluido')):
            eventos.registrar(portal, 'sessao_finalizada', 'ok', t[:200])
    except Exception as e:
        logger.debug(f"Falha ao analisar linha de log ({portal}): {e}")

def monitorar_log_me():
    global rodando_monitor_me, processo_me
    caminho_log = r"\\SERVIDOR2\Publico\ALLAN\Logs\mercadoeletronico.txt"
    try:
        with open(caminho_log, 'r', encoding='utf-8', errors='ignore') as f:
            f.seek(0, os.SEEK_END)
            while rodando_monitor_me:
                linha = f.readline()
                if linha and linha.strip():
                    linha_limpa = linha.strip()

                    # 1. Envia a linha para o mini-terminal do site
                    sio.emit('relatar_progresso_me', {'mensagem': linha_limpa})
                    _registrar_evento_linha_log('me', linha_limpa)

                    # 2. Verifica se  a mensagem de finalizao
                    if "=== APLICAO FINALIZADA ===" in linha_limpa or "=== SESSO FINALIZADA ===" in linha_limpa:
                        logger.info("O rob do ME finalizou a rotina naturalmente.")

                        rodando_monitor_me = False
                        processo_me = None

                        # 3. Avisa o frontend (me.html) para voltar o boto para INICIAR
                        sio.emit('sincronizar_estado_me', {'status': 'ocioso'})

                        # 4. Avisa o Servidor Global (server.js) para liberar o rob para outras tarefas
                        sio.emit('tarefa_concluida', {'evento': 'Extrao Mercado Eletrnico', 'sucesso': True})

                        break # Sai do loop de monitoramento

                else: 
                    time.sleep(0.5)
    except Exception as e:
        logger.error(f"Erro ao monitorar log do ME: {e}")
def monitorar_log_findes():
    global bot_findes, rodando_monitor_findes
    caminho_log = r"\\SERVIDOR2\Publico\ALLAN\Logs\log-findes.txt" 

    # O VIGIA ESPERA PACIENTEMENTE O ARQUIVO NASCER
    while rodando_monitor_findes and not os.path.exists(caminho_log):
        time.sleep(1)

    if not rodando_monitor_findes:
        return

    # ?? A "MOCHILA" QUE VAI GUARDAR OS TEXTOS PARA O BOTO COPIAR ??
    textos_acumulados = ""

    try:
        with open(caminho_log, 'r', encoding='utf-8', errors='ignore') as f:
            f.seek(0, os.SEEK_END)
            while rodando_monitor_findes:
                linha = f.readline()
                if linha and linha.strip():
                    linha_limpa = linha.strip()

                    # 1. Envia a linha normalmente para o mini-terminal do site
                    sio.emit('relatar_progresso_findes', {'mensagem': linha_limpa})
                    _registrar_evento_linha_log('findes', linha_limpa)

                    # ========================================================
                    # 2. A MGICA DO COLECIONADOR: Filtra e junta os dados!
                    # ========================================================
                    if linha_limpa.startswith("CDE:"):
                        # Se j tiver alguma coisa na mochila (ou seja,  o 2 evento), d 2 enters para separar
                        if textos_acumulados: 
                            textos_acumulados += "\n\n"
                        textos_acumulados += linha_limpa + "\n"

                    elif linha_limpa.startswith("MATERIAL:") or linha_limpa.startswith("TRMINO") or linha_limpa.startswith("TERMINO"):
                        textos_acumulados += linha_limpa + "\n"

                    # ========================================================
                    # 3. FINALIZAO: Entrega a mochila ao site!
                    # ========================================================
                    if "=== SESSO FINALIZADA ===" in linha_limpa:

                        # ... dentro do IF do === SESSO FINALIZADA === ...
                        if textos_acumulados.strip():
                            logger.info("Enviando textos gerados para o site...")
                            io.emit('findes_textos_gerados', {'texto': textos_acumulados.strip()})

                        sio.emit('relatar_progresso_findes', {'mensagem': '?? Sinal de Fim recebido! Fechando o rob automaticamente...'})

                        if bot_findes:
                            os.system(f"taskkill /F /T /PID {bot_findes.pid}")
                            bot_findes = None

                        rodando_monitor_findes = False
                        sio.emit('relatar_progresso_findes', {'comando_interno': 'desligar_botoes'})
                        break 
                else:
                    time.sleep(0.5)
    except Exception as e:
        logger.error(f"Erro no monitor do Findes: {e}")

def monitorar_log_ariba():
    global rodando_monitor_ariba
    caminho_log = r"\\SERVIDOR2\Publico\ALLAN\Logs\aribasourcing.txt"

    # ?? Aumentamos para 10 segundos porque o 'dotnet run'  demorado!
    time.sleep(10) 

    try:
        with open(caminho_log, 'r', encoding='utf-8', errors='ignore') as f:
            f.seek(0, os.SEEK_END)
            while rodando_monitor_ariba:
                linha = f.readline()
                if linha and linha.strip():
                    sio.emit('relatar_progresso_ariba', {'mensagem': linha.strip()})
                    _registrar_evento_linha_log('ariba', linha.strip())
                else:
                    time.sleep(0.5)
    except Exception as e: 
        logger.error(f"Erro no monitor do Ariba: {e}")

def _linha_do_robo(portal, linha):
    """Linha nova do log do robo: vai para o painel preto e vira evento da Atividade quando for erro ou fim."""
    sio.emit(f'relatar_progresso_{portal}', {'mensagem': linha})
    _registrar_evento_linha_log(portal, linha)

def monitorar_log_coupa():
    import log_robos
    log_robos.acompanhar(log_robos.caminho_log('log-coupa.txt'),
                         lambda linha: _linha_do_robo('coupa', linha),
                         lambda: rodando_monitor_coupa)

def monitorar_log_vale():
    import log_robos
    log_robos.acompanhar(log_robos.caminho_log('log-vale.txt'),
                         lambda linha: _linha_do_robo('vale', linha),
                         lambda: rodando_monitor_vale)

def monitorar_pastas_impressao():
    """Vigia as pastas de todos os portais e conta os arquivos prontos para impressão"""
    pasta_me = CONFIG_GLOBAL.get("pasta_me", r"\\SERVIDOR2\Publico\ALLAN\MERCADO-ELETRONICO")
    pasta_coupa_vale = CONFIG_GLOBAL.get("pasta_coupa_vale", r"\\SERVIDOR2\Publico\ALLAN\eventos do coupa")
    pasta_ariba_aegea = CONFIG_GLOBAL.get("pasta_ariba_aegea", r"\\SERVIDOR2\Publico\ALLAN\AribaSourcing\AEGEA")
    pasta_ariba_estacio = CONFIG_GLOBAL.get("pasta_ariba_estacio", r"\\SERVIDOR2\Publico\ALLAN\AribaSourcing\ESTÁCIO")

    while True:
        try:
            if sio.connected:
                # 1. Contagem ME (PDFs)
                if os.path.exists(pasta_me):
                    qtd_me = len(glob.glob(os.path.join(pasta_me, '*.pdf')))
                    sio.emit('contagem_me', {'quantidade': qtd_me})

                # 2. Contagem Coupa/Vale (DOCX - ignorando arquivos temporrios do Word que comeam com ~)
                if os.path.exists(pasta_coupa_vale):
                    arquivos_cv = [f for f in glob.glob(os.path.join(pasta_coupa_vale, '*.docx')) if not os.path.basename(f).startswith("~")]
                    sio.emit('contagem_coupa_vale', {'quantidade': len(arquivos_cv)})

                # 3. Contagem Ariba (DOC/DOCX - ignorando temporrios)
                qtd_ariba = 0
                for pasta in [pasta_ariba_aegea, pasta_ariba_estacio]:
                    if os.path.exists(pasta):
                        arquivos_a = [f for f in glob.glob(os.path.join(pasta, '*.doc*')) if not os.path.basename(f).startswith("~")]
                        qtd_ariba += len(arquivos_a)
                sio.emit('contagem_ariba', {'quantidade': qtd_ariba})

        except Exception as e:
            logger.error(f"Erro no monitor de pastas: {e}")

        time.sleep(3) # Atualiza a contagem no site a cada 3 segundos

def monitorar_pasta_me():
    """Vigia a pasta do Mercado Eletrnico e conta os PDFs em tempo real"""
    pasta_me = CONFIG_GLOBAL.get("pasta_me", r"\\SERVIDOR2\Publico\ALLAN\MERCADO-ELETRONICO")
    while True:
        try:
            if os.path.exists(pasta_me):
                # Conta apenas os arquivos .pdf
                qtd = len(glob.glob(os.path.join(pasta_me, '*.pdf')))

                # ?? S ENVIA SE O ROB ESTIVER CONECTADO AO SERVIDOR ??
                if sio.connected:
                    sio.emit('contagem_me', {'quantidade': qtd})
            else:
                # Se a pasta no for encontrada pela rede, tenta novamente no prximo ciclo
                pass
        except Exception as e:
            logger.error(f"Erro no monitor de pasta do ME: {e}")

        time.sleep(3) # Atualiza a cada 3 segundos

def carregar_usuarios():
    path = 'banco_usuarios.json'
    if not os.path.exists(path): return {}
    with open(path, 'r', encoding='utf-8-sig') as f: return json.load(f)

@sio.on("validar_login")
def validar_login(dados):
    usuarios = carregar_usuarios()
    email, senha, cid = dados.get('user'), dados.get('pass'), dados.get('clientId')

    if email in usuarios and str(usuarios[email]['senha']) == str(senha):

        # ?? TRAVA DE E-MAIL NO VERIFICADO
        if not usuarios[email].get('verificado', False):
            sio.emit('resultado_login', {'sucesso': False, 'erro': 'Voc precisa ativar sua conta! Verifique o link enviado para o seu e-mail.', 'clientId': cid})
            return

        if usuarios[email].get('bloqueado'): 
            sio.emit('resultado_login', {'sucesso': False, 'erro': 'Conta suspensa.', 'clientId': cid})
        else: 
            sio.emit('resultado_login', {
                'sucesso': True, 
                'user': email, 
                'isAdmin': usuarios[email].get('admin', False), 
                'isDev': usuarios[email].get('dev', False), 
                'hasDashboardAccess': usuarios[email].get('dashboard_access', False),
                'hasPlanilhaAccess': usuarios[email].get('planilha_access', False), # <-- NOVO
                'hasPortaisAccess': usuarios[email].get('portais_access', False),   # <-- NOVO
                'vendedor': usuarios[email].get('vendedor', ''),
                'clientId': cid
            })
    else: 
        sio.emit('resultado_login', {'sucesso': False, 'erro': 'E-mail ou Senha incorretos.', 'clientId': cid})
  
@sio.on('pedir_dados_dev_seguro')
def pedir_dados_dev_seguro(dados):
    payload_cifrado = dados.get('payload_cifrado')
    online_users = dados.get('online_users', [])
    client_id = dados.get('clientId')

    # 1. Tenta abrir a caixa com a chave mestre (Se falhar, a senha que o Admin digitou estava errada)
    # IMPORTANTE: A chave mestre do desenvolvedor deve estar no seu .env ou ser uma constante
    chave_dev = os.getenv("DEV_MASTER_TOKEN", "123456") 

    comando_json = decifra_python(payload_cifrado, chave_dev)

    if not comando_json:
        # A senha estava errada ou o pacote foi adulterado! Devolve 'False' para o site
        sio.emit('resposta_painel_dev_cifrado', {'payload': False, 'clientId': client_id})
        return

    # 2. A senha estava certa! Prepara a lista de usurios
    usuarios = carregar_usuarios()

    # Atualiza quem est online no momento
    for email in usuarios:
        usuarios[email]['online'] = (email in online_users)

    # Prepara o pacote final
    pacote = {
        "usuarios": usuarios,
        "ips_bloqueados": [] # Pode implementar lista negra de IPs futuramente se quiser
    }

    # 3. Tranca a lista novamente antes de enviar pela rede!
    pacote_str = json.dumps(pacote)
    pacote_cifrado = cifra_python(pacote_str, chave_dev)

    sio.emit('resposta_painel_dev_cifrado', {'payload': pacote_cifrado, 'clientId': client_id})

VALIDADE_TOKEN_RECUPERACAO_SEG = 60 * 60  # link de redefinicao vale 1 hora

def _token_recuperacao_valido(info, token):
    if not token or info.get('token_recuperacao') != token:
        return False
    expira = info.get('token_recuperacao_expira')
    return expira is None or time.time() <= float(expira)

@sio.on('verificar_token_python')
def verificar_token_python(dados):
    token = dados.get('token')
    client_id = dados.get('clientId')
    tipo = dados.get('tipo', 'ativacao')
    usuarios = carregar_usuarios()

    # Token vazio nunca e valido (evita casar com usuarios cujo token e None)
    if not token:
        sio.emit('resultado_verificacao_token', {'sucesso': False, 'erro': 'Link invalido.', 'clientId': client_id})
        return

    # Link de redefinicao de senha: apenas confere se o token existe e nao expirou (nao consome)
    if tipo == 'verificar_recuperacao':
        valido = any(_token_recuperacao_valido(info, token) for info in usuarios.values())
        if valido:
            sio.emit('resultado_verificacao_token', {'sucesso': True, 'clientId': client_id})
        else:
            sio.emit('resultado_verificacao_token', {'sucesso': False, 'erro': 'Link invalido ou expirado.', 'clientId': client_id})
        return

    sucesso = False
    email_validado = None

    for email, info in usuarios.items():
        if info.get('token_verificacao') == token:
            # Encontrou o token! Ativa a conta e destri o token
            info['verificado'] = True
            info['token_verificacao'] = None 
            sucesso = True
            email_validado = email
            break

    if sucesso:
        salvar_usuarios(usuarios)
        logger.info(f"?? E-mail validado com sucesso: {email_validado}")
        sio.emit('resultado_verificacao_token', {'sucesso': True, 'clientId': client_id})
    else:
        sio.emit('resultado_verificacao_token', {'sucesso': False, 'erro': 'Link invlido ou j utilizado.', 'clientId': client_id})

@sio.on('gerar_token_recuperacao')
def gerar_token_recuperacao(dados):
    email = (dados.get('email') or '').strip().lower()
    client_id = dados.get('clientId')
    usuarios = carregar_usuarios()

    if email in usuarios:
        token = str(uuid.uuid4())
        usuarios[email]['token_recuperacao'] = token
        usuarios[email]['token_recuperacao_expira'] = time.time() + VALIDADE_TOKEN_RECUPERACAO_SEG
        salvar_usuarios(usuarios)

        sucesso = enviar_email_python(email, token, tipo="recuperacao")
        if sucesso:
            sio.emit('resposta_recuperacao_solicitada', {'sucesso': True, 'clientId': client_id})
        else:
            sio.emit('resposta_recuperacao_solicitada', {'sucesso': False, 'erro': 'Erro ao enviar o e-mail de recuperação. Tente novamente em instantes.', 'clientId': client_id})
    else:
        logger.warning(f"Recuperacao de senha pedida para e-mail NAO cadastrado: {email} (nenhum e-mail enviado)")
        sio.emit('resposta_recuperacao_solicitada', {
            'sucesso': False,
            'erro': 'Este e-mail não está cadastrado no Maestro. Verifique o endereço ou crie uma conta.',
            'clientId': client_id
        })

@sio.on('processar_nova_senha')
def processar_nova_senha(dados):
    token = dados.get('token')
    nova_senha = dados.get('senha')
    client_id = dados.get('clientId')
    usuarios = carregar_usuarios()

    if not token or not nova_senha:
        sio.emit('resultado_nova_senha', {'sucesso': False, 'erro': 'Link invalido ou expirado.', 'clientId': client_id})
        return

    sucesso = False
    for email, info in usuarios.items():
        if _token_recuperacao_valido(info, token):
            info['senha'] = nova_senha
            info['token_recuperacao'] = None
            info.pop('token_recuperacao_expira', None)
            sucesso = True
            logger.info(f"Senha redefinida com sucesso para {email}")
            break

    if sucesso:
        salvar_usuarios(usuarios)
        sio.emit('resultado_nova_senha', {'sucesso': True, 'clientId': client_id})
    else:
        sio.emit('resultado_nova_senha', {'sucesso': False, 'erro': 'Link invlido ou expirado.', 'clientId': client_id})

class SioDoRobo:
    """Repassa tudo ao sio e guarda o último 'tarefa_concluida' do robô: o piloto sabe se a tarefa deu certo."""
    def __init__(self, sio_real):
        self._sio = sio_real
        self.ultima_conclusao = None

    def emit(self, evento, *args, **kwargs):
        if evento == 'tarefa_concluida':
            self.ultima_conclusao = (args[0] if args else kwargs.get('data')) or {}
        return self._sio.emit(evento, *args, **kwargs)

    def __getattr__(self, nome):
        return getattr(self._sio, nome)


def comandar(modo, portal):
    """Comando do piloto: pelo Node quando conectado (a tela acompanha o estado); sem conexão, executa aqui
    (antes o emit falhava calado e o ciclo seguia como se o robô tivesse recebido)."""
    dados = {'modo': modo, 'portal': portal}
    if sio.connected:
        sio.emit('comando_direto', dados)
    else:
        logger.warning(f"Sem conexão com o portal: '{modo}' do {portal} executado direto no gerenciador")
        comando_para_robo(dados)


def forcar_troca(portal, espera_s=30):
    """Botão "Assumir controle" do portal: desliga o outro robô e liga este.
    Passa pelo Node (comando_direto), como o piloto automático, para a tela acompanhar o estado."""
    outro = 'vale' if portal == 'coupa' else 'coupa'
    logger.info(f"Forçar troca: desligando {outro} para ligar {portal}")
    sio.emit('comando_direto', {'modo': 'desligar_robo', 'portal': outro})
    limite = time.time() + espera_s
    while time.time() < limite and (bot_vale if outro == 'vale' else bot_coupa) is not None:
        time.sleep(1)
    sio.emit('comando_direto', {'modo': 'ligar_robo', 'portal': portal})


@sio.on('comando_para_robo')
def comando_para_robo(dados):
    global bot_coupa, bot_vale, bot_findes, portal_atual, processo_me, bot_ariba, rodando_monitor_me, rodando_monitor_ariba, rodando_monitor_coupa, rodando_monitor_vale

    modo, portal = dados.get('modo'), dados.get('portal')

    if portal == 'me':
        if modo == 'extrair':
            rodando_monitor_me = True
            eventos.registrar('me', 'ciclo', 'info', 'Extração do Mercado Eletrônico iniciada')
            sio.emit('sincronizar_estado_me', {'status': 'extraindo'})
            threading.Thread(target=monitorar_log_me, daemon=True).start()
            caminho_bat = CONFIG_GLOBAL.get("caminho_bat_me", r"C:\Users\Administrator\Desktop\mercadoeletronico.bat")
            processo_me = subprocess.Popen([caminho_bat])

        elif modo == 'solicitar_parada':
            rodando_monitor_me = False
            if processo_me: os.system(f"taskkill /F /T /PID {processo_me.pid}")
            processo_me = None
            sio.emit('sincronizar_estado_me', {'status': 'ocioso'})

    elif portal == 'ariba':
        if modo == 'ligar_robo':
            prioridades_str = dados.get('prioridades', '').strip()
            if not prioridades_str: prioridades_str = "1" 

            eventos.registrar('ariba', 'ciclo', 'info', f'Robô SAP Ariba iniciado (prioridades: {prioridades_str})')
            sio.emit('sincronizar_estado_ariba', {'status': 'logando'})
            sio.emit('relatar_progresso_ariba', {'mensagem': '?? Comando recebido! Preparando o Ariba...'})

            def run_ariba():
                global bot_ariba, rodando_monitor_ariba
                try:
                    sio.emit('relatar_progresso_ariba', {'mensagem': 'Limpando processos antigos...'})
                    os.system("taskkill /F /IM AribaSourcing.exe >nul 2>&1")
                    os.system('wmic process where "name=\'dotnet.exe\' and commandline like \'%AribaSourcing%\'" call terminate >nul 2>&1')
                    time.sleep(1) 

                    rodando_monitor_ariba = True
                    threading.Thread(target=monitorar_log_ariba, daemon=True).start()

                    sio.emit('relatar_progresso_ariba', {'mensagem': 'Abrindo terminal e iniciando .NET...'})
                    env_win = os.environ.copy()
                    env_win["NODE_SKIP_PLATFORM_CHECK"] = "1"

                    # Abre o processo
                    comando = 'cmd.exe /c "title ROBO_ARIBA & dotnet run --no-build"'
                    caminho_ariba_local = CONFIG_GLOBAL.get("caminho_ariba", r"C:\Users\Administrator\Desktop\AribaSourcing")
                    bot_ariba = subprocess.Popen(
                        comando, 
                        cwd=caminho_ariba_local, 
                        creationflags=subprocess.CREATE_NEW_CONSOLE, 
                        env=env_win
                    )

                    sio.emit('relatar_progresso_ariba', {'mensagem': 'Digitando os dados da empresa...'})

                    prioridades_escapado = prioridades_str.replace("{", "{{").replace("}", "}}")

                    # ?? Fantasma Rpido: Sem travas de segurana que bloqueiam a digitao!
                    vbs_code = f"""
Set WshShell = WScript.CreateObject("WScript.Shell")

' D uma pausa de 4 segundos para o .NET carregar a tela de digitao
WScript.Sleep 4000

' Tenta "puxar" a janela pelo PID e Ttulo s por garantia, mas NO ABORTA se o Windows esconder
On Error Resume Next
WshShell.AppActivate({bot_ariba.pid})
WshShell.AppActivate("ROBO_ARIBA")
WshShell.AppActivate("Administrador: ROBO_ARIBA")
On Error GoTo 0

WScript.Sleep 500

' Digita os dados assumindo que a janela recm-criada  a que est na frente (Comportamento Original Rpido)
WshShell.SendKeys "{prioridades_escapado}"
WScript.Sleep 500
WshShell.SendKeys "~"
WScript.Sleep 1500
WshShell.SendKeys "~"
"""
                    vbs_path = os.path.join(tempfile.gettempdir(), "digitador_ariba.vbs")
                    with open(vbs_path, "w", encoding="utf-8") as f: 
                        f.write(vbs_code)

                    vbs_proc = subprocess.Popen(["wscript.exe", vbs_path])
                    vbs_proc.wait()

                    try:
                        os.unlink(vbs_path)
                    except:
                        pass

                    sio.emit('sincronizar_estado_ariba', {'status': 'ocioso'})
                    bot_ariba.wait()
                except Exception as e:
                    logger.error(f"Erro na execuo do Ariba: {e}")
                    eventos.registrar('ariba', 'ciclo', 'erro', f'Erro na execução do SAP Ariba: {e}')
                    sio.emit('relatar_progresso_ariba', {'mensagem': f'Erro ao iniciar: {e}'})
                finally:
                    rodando_monitor_ariba = False
                    bot_ariba = None
                    sio.emit('sincronizar_estado_ariba', {'status': 'desligado'})

            threading.Thread(target=run_ariba, daemon=True).start()

        elif modo == 'desligar_robo':
            rodando_monitor_ariba = False
            sio.emit('relatar_progresso_ariba', {'mensagem': '?? Encerrando o mdulo SAP Ariba...'})
            if bot_ariba: 
                os.system(f"taskkill /F /T /PID {bot_ariba.pid}")
                bot_ariba = None
            else: 
                os.system("taskkill /F /IM AribaSourcing.exe >nul 2>&1")

    elif portal in ['coupa', 'vale']:
        if modo == 'ligar_robo':
            existente = bot_coupa if portal == 'coupa' else bot_vale
            if existente is not None and getattr(existente, 'is_ready', False):
                # Não cria um segundo navegador por cima do primeiro (antes sobrescrevia sem fechar o driver)
                logger.warning(f"{portal}: já está ligado; ignorando o novo 'ligar'")
                sio.emit(f'relatar_progresso_{portal}', {'mensagem': f'⚠️ O robô {portal.upper()} já está ligado.'})
                sio.emit('tarefa_concluida', {'evento': 'Login do Robo', 'sucesso': True})   # a tela volta ao estado certo
                return
            if existente is not None:   # ficou sem login (falhou ou travou): fecha antes de abrir outro
                logger.warning(f"{portal}: robô anterior sem login; fechando e ligando de novo")
                existente.solicitacao_parada = True
                try: existente.driver.quit()
                except Exception: pass
            portal_atual = portal
            if portal == 'coupa':
                from bots.coupa import CoupaScraper
                bot_coupa = CoupaScraper(SioDoRobo(sio), aguardando_clique, coordenadas_clique)
                rodando_monitor_coupa = True
                threading.Thread(target=monitorar_log_coupa, daemon=True).start()
                threading.Thread(target=bot_coupa.iniciar_e_logar, daemon=True).start()
            else:
                from bots.vale_bot import ValeScraper
                bot_vale = ValeScraper(SioDoRobo(sio), aguardando_clique, coordenadas_clique)
                rodando_monitor_vale = True
                threading.Thread(target=monitorar_log_vale, daemon=True).start()
                threading.Thread(target=bot_vale.iniciar_e_logar, daemon=True).start()

        elif modo == 'desligar_robo':
            if portal == 'coupa':
                rodando_monitor_coupa = False
                if bot_coupa:
                    bot_coupa.solicitacao_parada = True 
                    time.sleep(2)
                    try: bot_coupa.driver.quit()
                    except: pass
                    bot_coupa = None
            elif portal == 'vale':
                rodando_monitor_vale = False 
                if bot_vale:
                    bot_vale.solicitacao_parada = True 
                    time.sleep(2)
                    try: bot_vale.driver.quit()
                    except: pass
                    bot_vale = None
            sio.emit('sou_o_robo')

        elif modo == 'forcar_troca':
            threading.Thread(target=forcar_troca, args=(portal,), daemon=True).start()

        # ?? INICIA TAREFAS NORMAIS ??
        elif modo in ['extrair', 'verificar', 'responder']:
            bot_alvo = bot_coupa if portal == 'coupa' else bot_vale
            if bot_alvo:
                anterior = getattr(bot_alvo, 'thread_atual', None)

                def rodar(bot=bot_alvo, anterior=anterior):
                    # Uma tarefa por vez no mesmo navegador: a nova espera a anterior terminar
                    if anterior is not None and anterior.is_alive():
                        logger.info(f"{portal}: '{modo}' aguardando a tarefa anterior terminar")
                        anterior.join()
                    if globals().get('bot_' + portal) is not bot:
                        logger.warning(f"{portal}: '{modo}' descartado: o robô foi desligado enquanto esperava")
                        return
                    bot.iniciar_tarefa(modo, dados)
                th = threading.Thread(target=rodar, daemon=True)
                th.start()
                bot_alvo.thread_atual = th   # depois do start: quem espera nunca vê uma thread ainda não iniciada
            else:
                sio.emit('relatar_progresso', {'mensagem': "? Erro: O rob no est ligado no Servidor!"})

        # ?? O FREIO DE MO AGORA EST ISOLADO E RECEBE QUALQUER VARIAO DO COMANDO ??
        elif modo in ['solicitar_parada', 'parar_extracao']:
            bot_alvo = bot_coupa if portal == 'coupa' else bot_vale
            if bot_alvo:
                bot_alvo.solicitacao_parada = True 

                logger.warning(f"?? ATENO: Comando de PARADA recebido do portal {portal.upper()}!")
                canal = f'relatar_progresso_{portal}' if portal else 'relatar_progresso'
                sio.emit(canal, {'mensagem': "?? Freio acionado! Concluindo o evento atual com segurana antes de parar..."})
            else:
                logger.warning("?? Comando de parada recebido, mas nenhum rob estava extraindo no momento.")

    # ========================================================
    # ?? ROTA .NET DO FINDES (INICIA COMO O ARIBA) ??
    # ========================================================
    elif portal == 'findes':
        if modo == 'ligar_robo':
            eventos_str = dados.get('evento', '')
            sio.emit('relatar_progresso_findes', {'mensagem': f"?? Iniciando Mdulo .NET Findes para os eventos: {eventos_str}"})

            def run_findes():
                global bot_findes, rodando_monitor_findes
                try:
                    # 1. Abre APENAS a janela preta vazia com o nome ROBO_FINDES
                    comando = 'cmd.exe /k "title ROBO_FINDES"'
                    bot_findes = subprocess.Popen(comando, creationflags=subprocess.CREATE_NEW_CONSOLE)

                    # INICIA O VIGIA DE LOGS
                    rodando_monitor_findes = True
                    threading.Thread(target=monitorar_log_findes, daemon=True).start()

                    # 2. O Digitador Fantasma agora faz TODO o trabalho
                    sio.emit('relatar_progresso_findes', {'mensagem': '?? Fantasma assumiu o teclado! Preparando ambiente...'})

                    # ?? USANDO O SMBOLO '~' QUE  O 'ENTER' INFALVEL DO VBSCRIPT ??
                    caminho_findes_local = CONFIG_GLOBAL.get("caminho_findes", r"C:\Users\Administrator\Desktop\FINDES")
                    vbs_code = f"""
Set WshShell = WScript.CreateObject("WScript.Shell")
WScript.Sleep 2000 

' Puxa a janela para a frente
For i = 1 To 5
    Success = WshShell.AppActivate("ROBO_FINDES")
    If Success Then Exit For
    WScript.Sleep 500
Next
WScript.Sleep 800

' --- PASSO 1: ENTRAR NA PASTA ---
WshShell.SendKeys "cd {caminho_findes_local}"
WScript.Sleep 500
WshShell.SendKeys "~"
WScript.Sleep 1500

' --- PASSO 2: APLICAR A REGRA NODE ---
WshShell.SendKeys "set NODE_SKIP_PLATFORM_CHECK=1"
WScript.Sleep 500
WshShell.SendKeys "~"
WScript.Sleep 1500

' --- PASSO 3: INICIAR O ROB ---
WshShell.SendKeys "dotnet run"
WScript.Sleep 500
WshShell.SendKeys "~"

' D 10 segundos para o .NET carregar antes de comear a mandar os eventos
WScript.Sleep 10000
"""
                    # --- PASSO 4: DIGITAR OS EVENTOS E SAIR ---
                    if eventos_str:
                        eventos_lista = [e.strip() for e in eventos_str.split(',') if e.strip()]

                        for evento in eventos_lista:
                            vbs_code += f"""
WshShell.SendKeys "{evento}"
WScript.Sleep 500
WshShell.SendKeys "~"
WScript.Sleep 2000
"""
                        vbs_code += """
WshShell.SendKeys "sair"
WScript.Sleep 500
WshShell.SendKeys "~"
"""
                    # Guarda e executa o Fantasma
                    vbs_path = os.path.join(tempfile.gettempdir(), "digitador_findes.vbs")
                    with open(vbs_path, "w", encoding="utf-8") as f: 
                        f.write(vbs_code)
                    subprocess.Popen(["wscript.exe", vbs_path])

                    # O Python fica  espera que a janela feche
                    bot_findes.wait()
                    eventos.registrar('findes', 'ciclo', 'ok', 'Módulo Findes concluído com sucesso', ref=eventos_str or None)

                except Exception as e:
                    logger.error(f"Erro na execuo do Findes: {e}")
                    sio.emit('relatar_progresso_findes', {'mensagem': f'? Erro Crtico ao iniciar: {e}'})
                finally:
                    if bot_findes:
                        bot_findes = None
                        sio.emit('relatar_progresso_findes', {'mensagem': "? Mdulo Findes concludo/fechado.", 'comando_interno': 'desligar_botoes'})

            # Roda o Findes em paralelo para no travar o gerenciador
            threading.Thread(target=run_findes, daemon=True).start()

        elif modo == 'desligar_robo':
            if bot_findes:
                sio.emit('relatar_progresso_findes', {'mensagem': '?? Freio acionado! Encerrando o Findes...'})
                os.system(f"taskkill /F /T /PID {bot_findes.pid}")
                bot_findes = None
            else:
                sio.emit('relatar_progresso_findes', {'mensagem': "?? O rob j estava desligado."})
            sio.emit('relatar_progresso_findes', {'comando_interno': 'desligar_botoes'})

        elif modo == 'mover_anexos_findes':
            sio.emit('relatar_progresso_findes', {'mensagem': "?? Funo de mover anexos precisa ser acoplada  lgica do .NET."})

@sio.on('executar_clique')
def receber_clique(dados):
    coordenadas_clique.update({'x': dados['x'], 'y': dados['y']})
    aguardando_clique.set()

@sio.on('pedir_config_admin')
def pedir_config_admin(dados):
    client_id = dados.get('clientId')
    sio.emit('receber_config_admin', {'config': CONFIG_GLOBAL, 'clientId': client_id})

@sio.on('salvar_config_admin')
def salvar_config_admin(dados):
    global CONFIG_GLOBAL, SMTP_USER, SMTP_PASSWORD
    client_id = dados.get('clientId')
    nova_config = dados.get('config')
    if not isinstance(nova_config, dict) or not nova_config:
        # painel sem dados (ex.: gerenciador reconectando) apagaria o CONFIG.json inteiro
        sio.emit('receber_config_admin', {'config': CONFIG_GLOBAL, 'clientId': client_id, 'sucesso': False,
                                          'erro': 'Configuração vazia: nada foi salvo.'})
        return
    nova_config = com_padroes(nova_config)
    salvar_config(nova_config)
    CONFIG_GLOBAL = nova_config
    SMTP_USER = CONFIG_GLOBAL.get("SMTP_USER", os.environ.get("SMTP_USER", ""))
    SMTP_PASSWORD = CONFIG_GLOBAL.get("SMTP_PASSWORD", os.environ.get("SMTP_PASSWORD", ""))
    sio.emit('receber_config_admin', {'config': CONFIG_GLOBAL, 'clientId': client_id, 'sucesso': True})

# ==================== PAINEL ADMIN: CONTAS E PRIVILEGIOS ====================
CAMPOS_PUBLICOS_USUARIO = (
    'verificado', 'admin', 'dev', 'dashboard_access', 
    'planilha_access', 'portais_access', 'bloqueado', 'vendedor'
)

@sio.on('admin_listar_usuarios')
def admin_listar_usuarios(dados):
    client_id = dados.get('clientId')
    try:
        usuarios = carregar_usuarios()
        lista = {
            email: {k: info.get(k, '' if k == 'vendedor' else False) for k in CAMPOS_PUBLICOS_USUARIO}
            for email, info in usuarios.items()
        }
        sio.emit('admin_resposta_usuarios', {'sucesso': True, 'usuarios': lista, 'clientId': client_id})
    except Exception as e:
        logger.error(f"Erro ao listar usuarios para o admin: {e}")
        sio.emit('admin_resposta_usuarios', {'sucesso': False, 'erro': 'Erro ao ler o banco de usuários.', 'clientId': client_id})

@sio.on('admin_alterar_acesso_dashboard')
def admin_alterar_acesso_dashboard(dados):
    client_id = dados.get('clientId')
    email = (dados.get('email') or '').strip().lower()
    acesso = bool(dados.get('acesso'))
    campo = dados.get('campo') or 'dashboard_access' # <-- NOVO: aceita dashboard_access, planilha_access ou portais_access

    if campo not in ('dashboard_access', 'planilha_access', 'portais_access'):
        sio.emit('admin_resposta_acesso', {'sucesso': False, 'erro': 'Permissão inválida.', 'clientId': client_id})
        return

    try:
        usuarios = carregar_usuarios()
        if email not in usuarios:
            sio.emit('admin_resposta_acesso', {'sucesso': False, 'erro': 'Usuário não encontrado.', 'clientId': client_id})
            return
        usuarios[email][campo] = acesso
        salvar_usuarios(usuarios)
        logger.info(f"Admin alterou {campo} de {email} para {acesso}")
        sio.emit('admin_resposta_acesso', {'sucesso': True, 'email': email, 'campo': campo, 'acesso': acesso, 'clientId': client_id})
    except Exception as e:
        logger.error(f"Erro ao alterar acesso de {email}: {e}")
        sio.emit('admin_resposta_acesso', {'sucesso': False, 'erro': 'Erro ao salvar o banco de usuários.', 'clientId': client_id})

@sio.on('admin_alterar_vendedor_usuario')
def admin_alterar_vendedor_usuario(dados):
    client_id = dados.get('clientId')
    email = (dados.get('email') or '').strip().lower()
    vendedor = (dados.get('vendedor') or '').strip()
    try:
        usuarios = carregar_usuarios()
        if email not in usuarios:
            sio.emit('admin_resposta_vendedor', {'sucesso': False, 'erro': 'Usuário não encontrado.', 'clientId': client_id})
            return
        usuarios[email]['vendedor'] = vendedor
        salvar_usuarios(usuarios)
        logger.info(f"Admin vinculou vendedor '{vendedor}' ao usuario {email}")
        sio.emit('admin_resposta_vendedor', {'sucesso': True, 'email': email, 'vendedor': vendedor, 'clientId': client_id})
    except Exception as e:
        logger.error(f"Erro ao alterar vendedor de {email}: {e}")
        sio.emit('admin_resposta_vendedor', {'sucesso': False, 'erro': 'Erro ao salvar o banco de usuários.', 'clientId': client_id})

@sio.on('connect')
def connect():
    logger.info("?? Conectado!")
    sio.emit('sou_o_robo')

@sio.on('comando_imprimir')
def comando_imprimir(dados):
    portal = dados.get('portal')
    impressora = dados.get('impressora')
    sio.emit('relatar_progresso', {'mensagem': f"??? A preparar impressão no portal {portal.upper()}..."})

    def job_imprimir():
        try:
            # 1. Define a impressora padro do Windows
            subprocess.run(f'RUNDLL32 PRINTUI.DLL,PrintUIEntry /y /n "{impressora}"', shell=True)
            time.sleep(2)

            arquivos_encontrados = []

            # 2. Mapeia as pastas corretas dependendo do portal
            if portal == 'me':
                pasta_origem = r"\\SERVIDOR2\Publico\ALLAN\MERCADO-ELETRONICO"
                pasta_destino = os.path.join(pasta_origem, "J imprimiu")
                os.makedirs(pasta_destino, exist_ok=True)
                for f in glob.glob(os.path.join(pasta_origem, "*.pdf")):
                    arquivos_encontrados.append((f, pasta_destino))

            elif portal in ['coupa', 'vale']:
                pasta_origem = r"\\SERVIDOR2\Publico\ALLAN\eventos do coupa"
                pasta_destino = r"\\SERVIDOR2\Publico\RFQS VALE PARA IMPRIMIR"
                os.makedirs(pasta_destino, exist_ok=True)
                for f in glob.glob(os.path.join(pasta_origem, "*.docx")):
                    if not os.path.basename(f).startswith("~"):
                        arquivos_encontrados.append((f, pasta_destino))

            elif portal == 'ariba':
                # ?? AGORA O CDIGO APONTA PARA AS SUBPASTAS EXISTENTES ??
                fontes = [
                    (r"\\SERVIDOR2\Publico\ALLAN\AribaSourcing\AEGEA", r"\\SERVIDOR2\Publico\ALLAN\AribaSourcing\AEGEA\J imprimiu"),
                    (r"\\SERVIDOR2\Publico\ALLAN\AribaSourcing\ESTÁCIO", r"\\SERVIDOR2\Publico\ALLAN\AribaSourcing\ESTÁCIO\j imprimiu")
                ]
                for orig, dest in fontes:
                    # Verifica se a pasta principal existe antes de procurar arquivos
                    if os.path.exists(orig):
                        # Garante que a subpasta de destino seja reconhecida
                        os.makedirs(dest, exist_ok=True)
                        for f in glob.glob(os.path.join(orig, "*.doc*")):
                            # Ignora arquivos temporrios (aqueles que comeam com ~)
                            if not os.path.basename(f).startswith("~"):
                                arquivos_encontrados.append((f, dest))

            # 3. Executa a impressão e move
            if not arquivos_encontrados:
                sio.emit('relatar_progresso', {'mensagem': f"?? Nenhuma cotação encontrada nas pastas do {portal.upper()}."})
                sio.emit('tarefa_concluida', {'evento': 'Impresso Lote', 'sucesso': True})
                return

            for arq, dest in arquivos_encontrados:
                nome = os.path.basename(arq)
                try:
                    os.startfile(arq, "print")
                    time.sleep(12) # Tempo para o Word/Acrobat processar a pgina
                    destino_final = os.path.join(dest, nome)
                    if os.path.exists(destino_final): destino_final = os.path.join(dest, f"{int(time.time())}_{nome}")
                    shutil.move(arq, destino_final)
                    sio.emit('relatar_progresso', {'mensagem': f"?? {nome} impresso e movido para a pasta final."})
                except Exception as e:
                    logger.error(f"Erro ao imprimir {nome}: {e}")

            if dados.get('avisar_email'):
                try:
                    import smtplib
                    from email.mime.text import MIMEText
                    from email.mime.multipart import MIMEMultipart
                    user = CONFIG_GLOBAL.get("SMTP_USER", os.environ.get("SMTP_USER", ""))
                    pwd = CONFIG_GLOBAL.get("SMTP_PASSWORD", os.environ.get("SMTP_PASSWORD", ""))
                    to_raw = CONFIG_GLOBAL.get("emails_relatorio", os.environ.get("EMAILS_RELATORIO", ""))
                    to_list = [e.strip() for e in to_raw.split(',') if e.strip()]
                    
                    if user and pwd and to_list:
                        msg = MIMEMultipart("alternative")
                        msg['From'] = user
                        msg['To'] = ", ".join(to_list)
                        msg['Subject'] = f"Impressao Concluida - Portal {portal.upper()}"
                        
                        corpo = f"O robo finalizou a impressao de {len(arquivos_encontrados)} arquivos para o portal {portal.upper()}.\n\nArquivos impressos:\n"
                        for arq, dest in arquivos_encontrados:
                            corpo += f"- {os.path.basename(arq)}\n"
                            
                        msg.attach(MIMEText(corpo, "plain"))
                        server = smtplib.SMTP('email-ssl.com.br', 587)
                        server.starttls()
                        server.login(user, pwd)
                        server.send_message(msg)
                        server.quit()
                        logger.info("Email de impressao enviado com sucesso!")
                except Exception as e:
                    logger.error(f"Erro ao enviar email de impressao: {e}")

            sio.emit('tarefa_concluida', {'evento': 'Impresso Lote', 'sucesso': True, 'portal': portal})

        except Exception as e:
            logger.error(f"Erro Geral na Impresso: {e}")
            sio.emit('tarefa_concluida', {'evento': 'Impresso Lote', 'sucesso': False, 'erro': str(e), 'portal': portal})

    threading.Thread(target=job_imprimir, daemon=True).start() 

    import openpyxl

@sio.on('comando_carregar_planilha_json')
def comando_carregar_planilha_json(dados):
    dados = dados or {}
    client_id = dados.get('clientId')
    origem = dados.get('origem', 'todas')
    vendedor_filtro = (dados.get('vendedor_filtro') or '').strip().upper() # <-- NOVO
    try:
        from planilha_manager import planilha_manager
        planilha_manager.iniciar()

        cotacoes = planilha_manager.cotacoes.get("COTAÇÃO", [])
        pedidos = planilha_manager.pedidos.get("PEDIDOS", [])

        if origem in ['coupa', 'vale']:
            import os, json
            caminho_db = CONFIG_GLOBAL.get("caminho_database_coupa") if origem == 'coupa' else CONFIG_GLOBAL.get("caminho_database_vale")
            chaves_validas = set()
            if caminho_db and os.path.exists(caminho_db):
                with open(caminho_db, 'r', encoding='utf-8') as f:
                    for linha in f:
                        if linha.strip():
                            try:
                                d = json.loads(linha)
                                chave = str(d.get('titulo') or d.get('numero') or d.get('num') or '')
                                if chave: chaves_validas.add(chave)
                            except: pass

            cotacoes = [c for c in cotacoes if str(c.get('COTAÇÃO', '')).strip() in chaves_validas]

        # <-- NOVO: Filtra apenas as cotações do vendedor logado
        if vendedor_filtro:
            cotacoes = [
                c for c in cotacoes 
                if str(c.get('VENDEDOR', '')).strip().upper() == vendedor_filtro
            ]
            pedidos = [] # Vendedor na tela de cotações não precisa receber a base inteira de pedidos

        sio.emit('retorno_planilha_json', {
            'sucesso': True,
            'cotacoes': cotacoes[::-1], 
            'pedidos': pedidos[::-1],
            'clientId': client_id
        })
    except Exception as e:
        sio.emit('retorno_planilha_json', {'sucesso': False, 'erro': str(e), 'clientId': client_id})

@sio.on('comando_ler_excel_dashboard')
def comando_ler_excel_dashboard(dados):
    client_id = dados.get('clientId')
    filtros = dados.get('filtros') or {}

    try:
        from datetime import datetime

        data_inicio = None
        data_fim = None

        if filtros.get('dataInicio'):
            try: data_inicio = datetime.strptime(filtros['dataInicio'], '%Y-%m-%d')
            except: pass

        if filtros.get('dataFim'):
            try: data_fim = datetime.strptime(filtros['dataFim'], '%Y-%m-%d').replace(hour=23, minute=59, second=59)
            except: pass

        from planilha_manager import planilha_manager
        planilha_manager.iniciar()

        hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        totais = {"total": 0, "respondidas": 0, "pendentes": 0, "nao_respondidas": 0}
        dic_marcas = {}
        dic_vendedores = {}

        linhas = planilha_manager.cotacoes.get("COTAÇÃO", [])

        for row in linhas:
            if not row.get("COTAÇÃO"): continue 

            vencimento = row.get("VENCIMENTO", "")

            data_venc = None
            if isinstance(vencimento, str):
                try: data_venc = datetime.strptime(vencimento.strip(), '%m/%d/%Y')
                except:
                    try: data_venc = datetime.strptime(vencimento.strip(), '%d/%m/%Y')
                    except: pass

            if data_venc:
                if data_inicio and data_venc < data_inicio: continue
                if data_fim and data_venc > data_fim: continue
            else:
                if data_inicio or data_fim: continue

            vendedor_str = str(row.get("VENDEDOR", "")).replace('\n', ' ').replace('\r', ' ').strip()
            if not vendedor_str or vendedor_str == "None": vendedor_str = "Sem Vendedor"
            import re
            vendedor_str = re.sub(r'\s+', ' ', vendedor_str)
            vendedor = vendedor_str.title() if vendedor_str.lower() != "sem vendedor" else "Sem Vendedor"

            marca_bruta = str(row.get("MARCAS ", "")).strip() or str(row.get("MARCAS", "")).strip()
            if not marca_bruta or marca_bruta == "None": marca_bruta = "Outros"
            situacao = classificar_resposta(row.get("RESPOSTA"))

            totais["total"] += 1
            is_respondida = situacao == "respondida"
            is_nao_respondida = situacao == "nao_respondida"

            esta_vencida = False
            if data_venc:
                esta_vencida = data_venc < hoje

            if is_respondida: totais["respondidas"] += 1
            elif is_nao_respondida: totais["nao_respondidas"] += 1
            elif not esta_vencida: totais["pendentes"] += 1

            import re as local_re
            marcas_list = local_re.split(r'(?i)\(\s*item\s*\d+\s*\)\s*:?\s*|item\s*\d+\s*:?\s*|/|\n', marca_bruta)

            marcas_processadas = set()
            for m in marcas_list:
                m_limpa = m.strip()
                if not m_limpa or m_limpa == '-': continue
                marcas_processadas.add(m_limpa)

            if not marcas_processadas: marcas_processadas.add('Outros')

            for marca in marcas_processadas:
                if marca not in dic_marcas:
                    dic_marcas[marca] = {'nome': marca, 'total': 0, 'respondidas': 0, 'pendentes': 0, 'nao_respondidas': 0}
                dic_marcas[marca]['total'] += 1
                if is_respondida: dic_marcas[marca]['respondidas'] += 1
                elif is_nao_respondida: dic_marcas[marca]['nao_respondidas'] += 1
                elif not esta_vencida: dic_marcas[marca]['pendentes'] += 1

            ignorar_vendedores = ["informacoes insuficientes para orcar", "pouco tempo habil", "vencida", "provavel vencida", "w"]
            if sem_acento(vendedor).lower() not in ignorar_vendedores:
                if vendedor not in dic_vendedores:
                    dic_vendedores[vendedor] = {'nome': vendedor, 'total': 0, 'respostas': 0, 'pendentes': 0, 'nao_respondidas': 0}
                dic_vendedores[vendedor]['total'] += 1
                if is_respondida: dic_vendedores[vendedor]['respostas'] += 1
                elif is_nao_respondida: dic_vendedores[vendedor]['nao_respondidas'] += 1
                elif not esta_vencida: dic_vendedores[vendedor]['pendentes'] += 1

        sio.emit('retorno_dados_dashboard', {
            'sucesso': True,
            'totais': totais,
            'marcas': list(dic_marcas.values()),
            'vendedores': list(dic_vendedores.values()),
            'clientId': client_id
        })

    except Exception as e:
        logger.error(f"Erro ao ler JSON pro dashboard: {e}")
        sio.emit('retorno_dados_dashboard', {'sucesso': False, 'erro': str(e), 'clientId': client_id})

@sio.on('comando_registrar_vendedor_cotacao')
def comando_registrar_vendedor_cotacao(dados):

    client_id = dados.get('clientId')
    vendedor = dados.get('vendedor', '').strip()
    cotacoes_raw = dados.get('cotacoes', '')

    if not vendedor or not cotacoes_raw:
        sio.emit('retorno_registro_vendedor_cotacao', {'sucesso': False, 'erro': 'Vendedor ou cotaes vazias', 'clientId': client_id})
        return

    import re
    # Separa as cotaes por vrgula ou espao
    lista_cotacoes = [c.strip() for c in re.split(r'[,\s]+', cotacoes_raw) if c.strip()]

    if not lista_cotacoes:
        sio.emit('retorno_registro_vendedor_cotacao', {'sucesso': False, 'erro': 'Nenhuma cotação vlida encontrada', 'clientId': client_id})
        return

    caminho_excel = CONFIG_GLOBAL.get("caminho_excel", r"\\SERVIDOR2\Publico\PLANILHA DE CONTROLE VALE - ESTAGIARIOS (copia 1).xlsx")

    import time

    sucesso_save = False
    try:
        from planilha_manager import planilha_manager
        planilha_manager.iniciar()
        with planilha_manager.lock:
            encontradas_set = set()
            cotacoes_lista = planilha_manager.cotacoes.get("COTAÇÃO", [])
            for cota in cotacoes_lista:
                val_str = str(cota.get("COTAÇÃO", "")).strip()
                if val_str in lista_cotacoes:
                    cota["VENDEDOR"] = vendedor
                    encontradas_set.add(val_str)

            if not planilha_manager.salvar():
                raise RuntimeError("Falha ao salvar a base; consulte logs/gerenciador.log.")
            sucesso_save = True

            nao_encontradas = [c for c in lista_cotacoes if c not in encontradas_set]
            eventos_banco = []
            eventos_invalidos = []

            if nao_encontradas:
                import json
                import os
                import threading

                path_coupa_db = CONFIG_GLOBAL.get("caminho_database_coupa", r"\\SERVIDOR2\Publico\ALLAN\database\eventos.jsonl")
                path_vale_db = CONFIG_GLOBAL.get("caminho_database_vale", r"\\SERVIDOR2\Publico\ALLAN\database\eventos_nimbi.jsonl")

                def load_db(caminho):
                    db = {}
                    if os.path.exists(caminho):
                        with open(caminho, 'r', encoding='utf-8') as f:
                            for linha in f:
                                if linha.strip():
                                    try:
                                        d = json.loads(linha)
                                        chave = str(d.get('titulo') or d.get('numero') or d.get('num') or '')
                                        if chave: db[chave] = d
                                    except: pass
                    return db

                db_coupa = load_db(path_coupa_db)
                db_vale = load_db(path_vale_db)

                eventos_coupa = []
                eventos_vale = []

                for evento in nao_encontradas:
                    if evento in db_coupa:
                        del db_coupa[evento]
                        eventos_coupa.append(evento)
                        eventos_banco.append(evento)
                    elif evento in db_vale:
                        del db_vale[evento]
                        eventos_vale.append(evento)
                        eventos_banco.append(evento)
                    else:
                        eventos_invalidos.append(evento)

                def save_db(caminho, db_novo):
                    if not os.path.exists(os.path.dirname(caminho)): return
                    ambiente.exigir_trava(caminho)
                    # temporário único + os.replace: dois salvamentos ao mesmo tempo não usam o mesmo .tmp
                    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=os.path.dirname(caminho),
                                                     prefix='maestro-', suffix='.tmp', delete=False) as f:
                        for k, v in db_novo.items(): f.write(json.dumps(v) + '\n')
                    os.replace(f.name, caminho)

                if eventos_coupa: save_db(path_coupa_db, db_coupa)
                if eventos_vale: save_db(path_vale_db, db_vale)
                # Ocultar painel da fila se houver algum
                sio.emit('atualizar_fila_extracao', {'fila': []})

                if eventos_banco:
                    try:
                        import threading
                        threading.Thread(target=enviar_email_aviso_limpeza, args=(vendedor, eventos_banco)).start()
                    except:
                        pass

            if encontradas_set:
                msg = f"Vendedor {vendedor} registrado em {len(encontradas_set)} cotação(es) com sucesso!"
                if eventos_banco:
                    msg += f"<br><br><span style='color: #eab308;'><i class='fa-solid fa-broom'></i> <b>ATENO:</b> As cotaes <b>{', '.join(eventos_banco)}</b> no esto na planilha. Elas foram apagadas da memria do rob para serem extradas novamente na prxima rodada automtica.</span>"
                if eventos_invalidos:
                    msg += f"<br><br><span style='color: #f87171;'><i class='fa-solid fa-triangle-exclamation'></i> <b>ERRO:</b> As cotaes <b>{', '.join(eventos_invalidos)}</b> no constam nem na planilha nem na memria do rob.</span>"

                sio.emit('retorno_registro_vendedor_cotacao', {
                    'sucesso': True, 
                    'mensagem': msg, 
                    'clientId': client_id
                })
            else:
                msg = f"Nenhuma das cotaes informadas foi encontrada na planilha.<br><br>Cotações procuradas: {', '.join(lista_cotacoes)}"
                if eventos_banco:
                    msg += f"<br><br><span style='color: #3b82f6;'><i class='fa-solid fa-broom'></i> <b>LIMPEZA:</b> As cotaes <b>{', '.join(eventos_banco)}</b> foram apagadas da memria para serem re-extradas na prxima rodada.</span>"
                if eventos_invalidos:
                    msg += f"<br><br><span style='color: #f87171;'><i class='fa-solid fa-triangle-exclamation'></i> <b>ERRO:</b> As cotaes <b>{', '.join(eventos_invalidos)}</b> no constam nem na planilha nem na memria do rob.</span>"

                sio.emit('retorno_registro_vendedor_cotacao', {
                    'sucesso': True,
                    'mensagem': msg,
                    'clientId': client_id
                })
    except Exception as e:
        sio.emit('retorno_registro_vendedor_cotacao', {'sucesso': False, 'erro': str(e), 'clientId': client_id})

@sio.on('comando_registrar_cotacao_manual')
def comando_registrar_cotacao_manual(dados):
    client_id = dados.get('clientId')
    cotacoes = dados.get('cotacoes', [])

    if not cotacoes:
        sio.emit('retorno_registro_cotacao_manual', {'sucesso': False, 'erro': 'Nenhuma cotação recebida', 'clientId': client_id})
        return

    caminho_excel = CONFIG_GLOBAL.get("caminho_excel", r"\\SERVIDOR2\Publico\PLANILHA DE CONTROLE VALE - ESTAGIARIOS (copia 1).xlsx")
    try:
        from planilha_manager import planilha_manager
        planilha_manager.iniciar(caminho_excel)
        linhas = []
        for cota in cotacoes:
            linhas.append([
                cota.get('cotacao', ''),
                cota.get('vencimento', ''),
                cota.get('item', ''),
                cota.get('quantidade', ''),
                cota.get('localidade', ''),
                cota.get('vendedor', ''),
                cota.get('modelos', ''),
                cota.get('marcas', ''),
                ''
            ])
        if not planilha_manager.adicionar_linhas("COTAÇÃO", linhas):
            raise RuntimeError("Falha ao salvar cotacoes; consulte logs/gerenciador.log.")
        sucesso_save = True
        numeros = [str(l[0]) for l in linhas]
        eventos.registrar('gerenciador', 'cotacao_nova', 'ok', f'{len(numeros)} cotação(ões) cadastrada(s) à mão no portal',
                          ref=numeros[0], detalhe={'cotacoes': numeros[:20]})

        sio.emit('retorno_registro_cotacao_manual', {'sucesso': True, 'mensagem': f'{len(cotacoes)} cotaes registradas com sucesso!', 'clientId': client_id})

    except Exception as e:
        sio.emit('retorno_registro_cotacao_manual', {'sucesso': False, 'erro': str(e), 'clientId': client_id})

@sio.on('solicitar_registro_pedido_manual')
def comando_registrar_pedido_manual(dados):
    client_id = dados.get('clientId')
    pedidos = dados.get('pedidos', [])

    if not pedidos:
        sio.emit('resposta_registro_pedido_manual', {'sucesso': False, 'erro': 'Nenhum pedido enviado.', 'clientId': client_id})
        return

    try:
        from planilha_manager import planilha_manager
        # Iniciar no gerenciador pode ser bom, embora teoricamente j deva estar
        caminho_excel = CONFIG_GLOBAL.get("caminho_excel", r"\\SERVIDOR2\Publico\PLANILHA DE CONTROLE VALE - ESTAGIARIOS (copia 1).xlsx")
        planilha_manager.iniciar(caminho_excel)

        sucesso = planilha_manager.adicionar_linhas_pedidos("PEDIDO", pedidos)
        if sucesso:
            sio.emit('resposta_registro_pedido_manual', {'sucesso': True, 'mensagem': f'{len(pedidos)} pedidos registrados com sucesso!', 'clientId': client_id})
        else:
            sio.emit('resposta_registro_pedido_manual', {'sucesso': False, 'erro': 'Aba de Pedidos no encontrada ou erro ao gravar.', 'clientId': client_id})
    except Exception as e:
        logger.error(f"Erro ao registrar pedido manual: {e}")
        sio.emit('resposta_registro_pedido_manual', {'sucesso': False, 'erro': str(e), 'clientId': client_id})

@sio.on('obter_fila_extracao')
def obter_fila_extracao(dados):
    client_id = dados.get('clientId')
    path_fila = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fila_extracao.json")
    fila = []
    if os.path.exists(path_fila):
        with open(path_fila, 'r', encoding='utf-8') as f:
            try: fila = json.load(f)
            except: pass
    sio.emit('atualizar_fila_extracao', {'fila': fila})

@sio.on('iniciar_extracao_fila')
def iniciar_extracao_fila(dados):
    try:
        client_id = dados.get('clientId')
        path_fila = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fila_extracao.json")
        fila = []
        if os.path.exists(path_fila):
            with open(path_fila, 'r', encoding='utf-8') as f:
                fila = json.load(f)
        if not fila:
            sio.emit('retorno_extracao_fila', {'sucesso': False, 'erro': 'Fila vazia.'})
            return

        threading.Thread(target=processar_fila_extracao, args=(fila, client_id), daemon=True).start()
    except Exception as e:
        logger.error(f"Erro no iniciar_extracao_fila: {e}")

def processar_fila_extracao(fila, client_id):
    import time, os

    eventos_coupa = list(set([item['cotacao'] for item in fila if item['portal'] == 'coupa']))
    eventos_vale = list(set([item['cotacao'] for item in fila if item['portal'] == 'vale']))

    mapa_vendedores = {item['cotacao']: item['vendedor'] for item in fila}
    pend = set(mapa_vendedores.keys())

    def emit_progresso(msg, cor="#fbbf24"):
        sio.emit('progresso_fila_extracao', {'mensagem': f'<span style="color: {cor};">{msg}</span>'})

    def garantir_robo_online(portal_nome):
        bot = globals().get(f'bot_{portal_nome}')
        if not bot or not getattr(bot, 'is_ready', False):
            emit_progresso(f'Ligando rob do {portal_nome.title()}...')
            comando_para_robo({'modo': 'ligar_robo', 'portal': portal_nome})
            espera = 0
            while not (globals().get(f'bot_{portal_nome}') and getattr(globals().get(f'bot_{portal_nome}'), 'is_ready', False)):
                time.sleep(1)
                espera += 1
                if espera > 60:
                    return False
        return True

    def executar_fluxo(portal_nome):
        pass

    try:
        if eventos_coupa:
            emit_progresso('Iniciando processamento Coupa...')
            if garantir_robo_online('coupa'):
                executar_fluxo('coupa')

        if eventos_vale:
            emit_progresso('Iniciando processamento Vale...')
            if garantir_robo_online('vale'):
                executar_fluxo('vale')

        # Ler a planilha e marcar como extrado
        from planilha_manager import planilha_manager
        planilha_manager.iniciar()
        with planilha_manager.lock:
            cotacoes_lista = planilha_manager.cotacoes.get("COTAÇÃO", [])
            salvou = False
            for cota in cotacoes_lista:
                val_str = str(cota.get("COTAÇÃO", "")).strip()
                if val_str in pend:
                    cota["VENDEDOR"] = mapa_vendedores.get(val_str, '')
                    salvou = True

            if salvou:
                if not planilha_manager.salvar():
                    raise RuntimeError("Falha ao salvar a base; consulte logs/gerenciador.log.")

        emit_progresso('Processamento da fila concludo com sucesso!', cor="#22c55e")
    except Exception as e:
        logger.error(f"Erro conexao: {e}")
        emit_progresso(f'Erro no processamento da fila: {e}', cor="#ef4444")

@sio.on('comando_eventos')
def comando_eventos(dados):
    """Dashboard de Atividade: últimos eventos do gerenciador (só leitura)."""
    dados = dados if isinstance(dados, dict) else {}
    try:
        resultado = eventos.ultimos(dados.get('limite', 200), dados.get('desde'))
        sio.emit('retorno_eventos', {'sucesso': True, 'resultado': resultado, 'clientId': dados.get('clientId')})
    except Exception as e:
        sio.emit('retorno_eventos', {'sucesso': False, 'erro': str(e), 'clientId': dados.get('clientId')})

# --- Monitoramento do servidor (CPU/RAM/GPU/VRAM) via winserver.py -----------------------------
HISTORICO_SERVIDOR = []          # amostras {t, cpu, ram, gpu, vram}; ~1h a cada 5 s
_LIM_HISTORICO_SERVIDOR = 720
_ultima_metrica_servidor = {}
_erro_metrica_servidor = None

def loop_metricas_servidor():
    """Amostra o hardware do servidor em segundo plano para o painel servidor.html."""
    global _ultima_metrica_servidor, _erro_metrica_servidor
    winserver = None
    while True:
        try:
            if winserver is None:
                import winserver   # tenta de novo a cada ciclo: basta instalar/copiar o que faltou
            m = winserver.coletar_metricas()
            _erro_metrica_servidor = None
            _ultima_metrica_servidor = m
            HISTORICO_SERVIDOR.append({'t': datetime.now().isoformat(timespec='seconds'),
                                       'cpu': m['cpu'], 'ram': m['ram'], 'gpu': m['gpu'], 'vram': m['vram']})
            del HISTORICO_SERVIDOR[:-_LIM_HISTORICO_SERVIDOR]
        except Exception as e:
            msg = f'{type(e).__name__}: {e}'
            if msg != _erro_metrica_servidor:
                logger.warning(f'Métricas do servidor indisponíveis: {msg}')
            _erro_metrica_servidor = msg
            winserver = None
            time.sleep(25)
        time.sleep(4.5)

@sio.on('comando_servidor_metricas')
def comando_servidor_metricas(dados):
    """Dashboard Servidor: consumo atual e histórico recente (só leitura)."""
    dados = dados if isinstance(dados, dict) else {}
    cid = dados.get('clientId')
    if not _ultima_metrica_servidor:
        sio.emit('retorno_servidor_metricas', {'sucesso': False, 'clientId': cid,
                 'erro': 'Métricas indisponíveis no gerenciador' + (f': {_erro_metrica_servidor}' if _erro_metrica_servidor else ' (ainda coletando a primeira leitura).')})
        return
    sio.emit('retorno_servidor_metricas', {'sucesso': True, 'clientId': cid,
             'metricas': {'atual': _ultima_metrica_servidor, 'historico': list(HISTORICO_SERVIDOR)}})

MAPA_LOGS_TXT = {
    'coupa': ('Robô Coupa', 'log-coupa.txt', [r'\\SERVIDOR2\Publico\ALLAN\Logs\log-coupa.txt']),
    'vale': ('Robô Vale', 'log-vale.txt', [r'\\SERVIDOR2\Publico\ALLAN\Logs\log-vale.txt']),
    'me': ('Mercado Eletrônico', 'mercadoeletronico.txt', [r'\\SERVIDOR2\Publico\ALLAN\Logs\mercadoeletronico.txt']),
    'ariba': ('Robô SAP Ariba', 'aribasourcing.txt', [r'\\SERVIDOR2\Publico\ALLAN\Logs\aribasourcing.txt']),
    'findes': ('Robô Findes', 'findes.txt', [r'\\SERVIDOR2\Publico\ALLAN\Logs\findes.txt', r'\\SERVIDOR2\Publico\ALLAN\Logs\log-findes.txt']),
    'gerenciador': ('Gerenciador Central', 'gerenciador.log', [os.path.join('logs', 'gerenciador.log'), r'\\SERVIDOR2\Publico\ALLAN\Logs\console-log.txt']),
    'console_produtos': ('API Cadastro de Produtos', 'console-log.txt', [r'\\SERVIDOR2\Publico\ALLAN\Logs\console-log.txt']),
    'zapzap': ('Bot WhatsApp / Gateway', 'log_zapzap.txt', [r'\\SERVIDOR2\Publico\ALLAN\Logs\log_zapzap.txt']),
    'acessos': ('Registro de Acessos', 'registro_acessos.txt', [r'\\SERVIDOR2\Publico\ALLAN\Logs\registro_acessos.txt']),
}

@sio.on('comando_ler_log_txt')
def comando_ler_log_txt(dados):
    r"""Lê as últimas linhas de um arquivo .txt em \\SERVIDOR2\Publico\ALLAN\Logs (só leitura)."""
    dados = dados if isinstance(dados, dict) else {}
    chave = str(dados.get('id') or dados.get('modulo') or '').strip().lower()
    max_linhas = max(20, min(int(dados.get('linhas') or 400), 3000))
    pasta_rede = r'\\SERVIDOR2\Publico\ALLAN\Logs'
    info = MAPA_LOGS_TXT.get(chave)
    if not info:
        for k, v in MAPA_LOGS_TXT.items():
            if v[1].lower() == chave:
                chave, info = k, v
                break
    if not info and os.path.isdir(pasta_rede):
        try:
            for nome_arq in os.listdir(pasta_rede):
                baixo = nome_arq.lower()
                if not (baixo.endswith('.txt') or baixo.endswith('.log')):
                    continue
                sem_ext = os.path.splitext(baixo)[0]
                slug = re.sub(r'[^a-z0-9]+', '_', re.sub(r'^(log[-_\s]*|robo[-_\s]*)', '', sem_ext)).strip('_')
                if chave in (baixo, sem_ext, slug, f'robo_{slug}'):
                    caminho_auto = os.path.join(pasta_rede, nome_arq)
                    if os.path.isfile(caminho_auto):
                        titulo = f"Robô {slug.replace('_', ' ').title()}" if slug else nome_arq
                        info = (titulo, nome_arq, [caminho_auto])
                        break
        except Exception:
            pass
    if not info:
        sio.emit('retorno_ler_log_txt', {'sucesso': False, 'erro': f'Log não mapeado: {chave}', 'clientId': dados.get('clientId'), 'requestId': dados.get('requestId')})
        return
    nome, arq_padrao, caminhos = info
    caminho = next((c for c in caminhos if os.path.isfile(c)), None)
    if not caminho:
        sio.emit('retorno_ler_log_txt', {'sucesso': False, 'id': chave, 'nome': nome, 'arquivo': arq_padrao, 'caminho': caminhos[0], 'erro': f'Arquivo {arq_padrao} não encontrado em {caminhos[0]}', 'clientId': dados.get('clientId'), 'requestId': dados.get('requestId')})
        return
    try:
        st = os.stat(caminho)
        tam = st.st_size
        ler_bytes = min(tam, max(32768, max_linhas * 350))
        with open(caminho, 'rb') as f:
            if tam > ler_bytes:
                f.seek(tam - ler_bytes)
            bloco = f.read(ler_bytes)
        brutas = bloco.splitlines()
        if tam > ler_bytes and len(brutas) > 1:
            brutas = brutas[1:]
        linhas = []
        for b in brutas[-max_linhas:]:
            try:
                linhas.append(b.decode('utf-8'))
            except UnicodeDecodeError:
                linhas.append(b.decode('latin-1', errors='replace'))
        sio.emit('retorno_ler_log_txt', {
            'sucesso': True, 'id': chave, 'modulo': chave, 'nome': nome,
            'arquivo': os.path.basename(caminho), 'caminho': caminho,
            'tamanho': tam, 'modificado_em': datetime.fromtimestamp(st.st_mtime).isoformat(),
            'truncado': tam > ler_bytes or len(brutas) > max_linhas, 'linhas': linhas,
            'clientId': dados.get('clientId'), 'requestId': dados.get('requestId'),
        })
    except Exception as e:
        sio.emit('retorno_ler_log_txt', {'sucesso': False, 'id': chave, 'nome': nome, 'arquivo': os.path.basename(caminho), 'caminho': caminho, 'erro': str(e), 'clientId': dados.get('clientId'), 'requestId': dados.get('requestId')})

@sio.on('comando_apagar_linha')
def comando_apagar_linha(dados):
    client_id = dados.get('clientId')
    tipo = dados.get('tipo')
    chave = dados.get('chave')

    try:
        from planilha_manager import planilha_manager
        planilha_manager.iniciar()

        with planilha_manager.lock:
            lista = planilha_manager.cotacoes["COTAÇÃO"] if tipo == 'cotacoes' else planilha_manager.pedidos["PEDIDOS"]
            chave_busca = "COTAÇÃO" if tipo == 'cotacoes' else "PEDIDO"

            # Find and remove
            for i, item in enumerate(lista):
                if item is None: continue
                if str(item.get(chave_busca, "")).strip() == str(chave).strip():
                    del lista[i]
                    break

            if not planilha_manager.salvar():
                raise RuntimeError("Falha ao salvar a base; consulte logs/gerenciador.log.")

        sio.emit('resposta_acao_linha', {'sucesso': True, 'mensagem': 'Linha apagada com sucesso!', 'clientId': client_id})
    except Exception as e:
        sio.emit('resposta_acao_linha', {'sucesso': False, 'erro': str(e), 'clientId': client_id})

@sio.on('comando_editar_linha')
@sio.on('comando_marcar_cotacao_respondida')
def comando_editar_linha(dados):
    client_id = dados.get('clientId')
    tipo = dados.get('tipo')
    chave = dados.get('chave')
    novos_dados = dados.get('novos_dados', {})

    try:
        from planilha_manager import planilha_manager
        if 'identidade' in dados:
            if tipo != 'cotacoes' or novos_dados != {'RESPOSTA': 'RESPONDIDO'}:
                raise ValueError('Alteração de resposta inválida.')
            from resposta_cotacao import marcar_respondida
            marcar_respondida(planilha_manager, dados['identidade'])
            sio.emit('resposta_acao_linha', {'sucesso': True, 'mensagem': 'Resposta salva no JSON.', 'clientId': client_id})
            return
        planilha_manager.iniciar()

        with planilha_manager.lock:
            lista = planilha_manager.cotacoes["COTAÇÃO"] if tipo == 'cotacoes' else planilha_manager.pedidos["PEDIDOS"]
            chave_busca = "COTAÇÃO" if tipo == 'cotacoes' else "PEDIDO"

            # Find and update
            for i, item in enumerate(lista):
                if item is None: continue
                if str(item.get(chave_busca, "")).strip() == str(chave).strip():
                    # Update all keys provided
                    for k, v in novos_dados.items():
                        lista[i][k] = v
                    break

            if not planilha_manager.salvar():
                raise RuntimeError("Falha ao salvar a base; consulte logs/gerenciador.log.")

        sio.emit('resposta_acao_linha', {'sucesso': True, 'mensagem': 'Linha atualizada com sucesso!', 'clientId': client_id})
    except Exception as e:
        sio.emit('resposta_acao_linha', {'sucesso': False, 'erro': str(e), 'clientId': client_id})

# --- ROTINA DE BACKUP AUTOMATIZADO ---
import shutil
import schedule
import time
import threading
from datetime import datetime

def rotina_backup():
    try:
        from planilha_manager import planilha_manager
        caminho_backup_dir = CONFIG_GLOBAL.get("caminho_backups", "backups")
        if not os.path.exists(caminho_backup_dir):
            try: os.makedirs(caminho_backup_dir, exist_ok=True)
            except: pass

        hoje_str = datetime.now().strftime("%Y-%m-%d_%H-%M")

        # Backup COTACOES
        if os.path.exists(planilha_manager.caminho_cotacoes):
            shutil.copy2(planilha_manager.caminho_cotacoes, os.path.join(caminho_backup_dir, f"COTAES {hoje_str}.json"))

        # Backup PEDIDOS
        if os.path.exists(planilha_manager.caminho_pedidos):
            shutil.copy2(planilha_manager.caminho_pedidos, os.path.join(caminho_backup_dir, f"PEDIDOS {hoje_str}.json"))

        logger.info(f"Backup realizado com sucesso as {hoje_str}")
    except Exception as e:
        logger.error(f"Erro ao realizar backup: {e}")

# Agendar para toda manha as 07:00
schedule.every().day.at("07:00").do(rotina_backup)
schedule.every().day.at("03:00").do(eventos.limpar)

def rotina_impressao_segura():
    def _impressao_thread():
        logger.info("? Horrio de impressão atingido! Verificando se os robs esto ocupados...")
        sio.emit('relatar_progresso_coupa', {'mensagem': '? Horrio de impressão (11h/15h). Verificando disponibilidade...'})
        while True:
            coupa_ocupado = globals().get('bot_coupa') and getattr(globals().get('bot_coupa'), 'thread_atual', None) and globals().get('bot_coupa').thread_atual.is_alive()
            vale_ocupado = globals().get('bot_vale') and getattr(globals().get('bot_vale'), 'thread_atual', None) and globals().get('bot_vale').thread_atual.is_alive()
            if coupa_ocupado or vale_ocupado:
                logger.info("? Robs esto trabalhando. Aguardando 30 segundos para imprimir...")
                time.sleep(30)
            else:
                break
        
        logger.info("??? Robs livres! Iniciando impressão agendada...")
        sio.emit('relatar_progresso_coupa', {'mensagem': '??? Robs livres! Enviando cotaes para a impressora HP...'})
        comando_imprimir({'portal': 'coupa', 'impressora': 'HP LaserJet P205X series PCL6 Class Driver', 'avisar_email': True})

    threading.Thread(target=_impressao_thread, daemon=True).start()


def enviar_email_erro(detalhes_erro):
    try:
        user = CONFIG_GLOBAL.get("SMTP_USER")
        pwd = CONFIG_GLOBAL.get("SMTP_PASSWORD")
        to = CONFIG_GLOBAL.get("emails_relatorio", user)
        if not user or not pwd: return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "?? ALERTA CRTICO - Rob Maestro ??"
        msg["From"] = user
        msg["To"] = to

        corpo = f"""
        <div style="font-family: Arial; padding: 20px; background: #0f1115; color: #fff; border-radius: 8px;">
            <h2 style="color: #e5534b;">?? Erro na Automao</h2>
            <p>O rob encontrou um problema crtico durante a execuo do ciclo automtico (Auto-Pilot).</p>
            <pre style="background: #1c2128; padding: 15px; color: #ff7b72; border-radius: 5px; overflow-x: auto;">{detalhes_erro}</pre>
        </div>
        """
        msg.attach(MIMEText(corpo, "html"))

        server = smtplib.SMTP('email-ssl.com.br', 587)
        server.starttls()
        server.login(user, pwd)
        server.send_message(msg)
        server.quit()
    except Exception as e:
        logger.error(f"Erro ao enviar email de erro crtico: {e}")

DURACAO_MAX_TAREFA_S = 3 * 3600   # tarefa travada além disso derruba o ciclo (e o próximo recomeça)
INATIVIDADE_ALERTA_S = 6 * 3600   # robô sem extração concluída por mais que isso gera um alerta (uma vez)
ultima_extracao_ok = {'coupa': time.time(), 'vale': time.time()}
inatividade_alertada = {'coupa': False, 'vale': False}
ultimo_dia_verificacao_vale = None


def deve_verificar_vale(agora, ultimo_dia, hora=9):
    """Uma verificação da Vale por dia, no primeiro ciclo a partir das 09:00 (ela pede o OTP a alguém no painel)."""
    return agora.hour >= hora and ultimo_dia != agora.date()


def esperar_robo_pronto(portal, timeout=120):
    limite = time.time() + timeout
    while True:
        bot = globals().get('bot_' + portal)
        if bot is not None and getattr(bot, 'is_ready', False):
            return bot
        fim = getattr(getattr(bot, 'sio', None), 'ultima_conclusao', None)
        if fim and fim.get('sucesso') is False:
            raise Exception(f"Login do {portal} falhou: {fim.get('erro')}")
        if time.time() > limite:
            raise Exception(f"Timeout ao ligar o robô {portal} (captcha ou bloqueio?)")
        time.sleep(2)


def executar_tarefa(portal, modo, timeout_inicio=60):
    """Manda a tarefa e espera terminar. Erro se o comando não chegar ao robô ou se ele não confirmar sucesso
    (antes, um comando perdido ou uma extração com erro davam o ciclo como "ok")."""
    bot = globals().get('bot_' + portal)
    if bot is None:
        raise Exception(f"Robô {portal} não está ligado")
    anterior = getattr(bot, 'thread_atual', None)
    if isinstance(bot.sio, SioDoRobo):
        bot.sio.ultima_conclusao = None
    comandar(modo, portal)
    limite = time.time() + timeout_inicio
    while getattr(bot, 'thread_atual', None) is anterior:
        if time.time() > limite:
            raise Exception(f"O comando '{modo}' não chegou ao robô {portal} em {timeout_inicio}s")
        time.sleep(1)
    tarefa = bot.thread_atual
    tarefa.join(DURACAO_MAX_TAREFA_S)
    if tarefa.is_alive():
        raise Exception(f"'{modo}' do {portal} travado há mais de {DURACAO_MAX_TAREFA_S // 3600} h")
    fim = getattr(bot.sio, 'ultima_conclusao', None)
    if not fim or not fim.get('sucesso'):
        raise Exception(f"'{modo}' do {portal} não terminou bem: {(fim or {}).get('erro') or 'sem confirmação do robô'}")


def precisa_alertar_inatividade(agora, ultimo_ok, ja_alertado, limite_s=INATIVIDADE_ALERTA_S):
    return not ja_alertado and agora - ultimo_ok > limite_s


_trava_inatividade = threading.Lock()


def checar_inatividade(portal):
    with _trava_inatividade:
        if not precisa_alertar_inatividade(time.time(), ultima_extracao_ok[portal], inatividade_alertada[portal]):
            return
        inatividade_alertada[portal] = True
    msg = f"Robô {portal} sem extração concluída há {(time.time() - ultima_extracao_ok[portal]) / 3600:.0f} h"
    logger.error(msg)
    eventos.registrar(portal, 'robo_inativo', 'erro', msg)
    enviar_email_erro(msg)


def extracao_ok(portal):
    ultima_extracao_ok[portal] = time.time()
    inatividade_alertada[portal] = False


def loop_automacao():
    global ultimo_dia_verificacao_vale
    logger.info("Auto-Pilot: Coupa (extração + verificação) -> 1h30 -> Vale (extração + verificação diária) -> 1h30")
    time.sleep(10)

    while True:
        # === COUPA ===
        checar_inatividade('coupa')
        try:
            eventos.registrar('coupa', 'ciclo', 'info', 'Ciclo automático do Coupa começou')
            sio.emit('relatar_progresso_coupa', {'mensagem': 'Auto-Pilot: iniciando o ciclo de extração do COUPA...'})
            comandar('ligar_robo', 'coupa')
            esperar_robo_pronto('coupa')
            executar_tarefa('coupa', 'extrair')
            extracao_ok('coupa')
            sio.emit('relatar_progresso_coupa', {'mensagem': 'Auto-Pilot: extração do Coupa finalizada. Iniciando a verificação...'})
            executar_tarefa('coupa', 'verificar')
            eventos.registrar('coupa', 'ciclo', 'ok', 'Ciclo automático do Coupa terminou')
        except Exception as e_coupa:
            import traceback
            enviar_email_erro(f"Auto-Pilot falhou no COUPA:\n{e_coupa}\n\n{traceback.format_exc()}")
            logger.error(f"Erro no Coupa: {e_coupa}")
            eventos.registrar('coupa', 'ciclo', 'erro', f'Ciclo automático do Coupa falhou: {e_coupa}')
            sio.emit('relatar_progresso_coupa', {'mensagem': f'Erro no ciclo do Coupa: {e_coupa}'})
        finally:
            sio.emit('planilha_atualizada')
            sio.emit('relatar_progresso_coupa', {'mensagem': 'Auto-Pilot: desligando o robô Coupa; pausa de 1h30...'})
            try:
                comandar('desligar_robo', 'coupa')
            except Exception:
                logger.exception('Falha ao desligar o robô coupa no fim do ciclo')
            logger.info("Auto-Pilot: pausa de 1h30...")
            time.sleep(90 * 60)

        # === VALE ===
        checar_inatividade('vale')
        try:
            eventos.registrar('vale', 'ciclo', 'info', 'Ciclo automático da Vale começou')
            sio.emit('relatar_progresso_vale', {'mensagem': 'Auto-Pilot: iniciando o ciclo da VALE...'})
            comandar('ligar_robo', 'vale')
            esperar_robo_pronto('vale')
            executar_tarefa('vale', 'extrair')
            extracao_ok('vale')
            if deve_verificar_vale(datetime.now(), ultimo_dia_verificacao_vale):
                ultimo_dia_verificacao_vale = datetime.now().date()   # uma tentativa por dia, mesmo se o OTP não vier
                sio.emit('relatar_progresso_vale', {'mensagem': 'Auto-Pilot: verificação diária da Vale (vai pedir o OTP no painel)...'})
                try:
                    executar_tarefa('vale', 'verificar')
                except Exception as e_verif:   # OTP não informado não derruba o ciclo
                    logger.warning(f"Verificação diária da Vale não concluída: {e_verif}")
            eventos.registrar('vale', 'ciclo', 'ok', 'Ciclo automático da Vale terminou')
        except Exception as e_vale:
            import traceback
            enviar_email_erro(f"Auto-Pilot falhou na VALE:\n{e_vale}\n\n{traceback.format_exc()}")
            logger.error(f"Erro na Vale: {e_vale}")
            eventos.registrar('vale', 'ciclo', 'erro', f'Ciclo automático da Vale falhou: {e_vale}')
            sio.emit('relatar_progresso_vale', {'mensagem': f'Erro no ciclo da Vale: {e_vale}'})
        finally:
            sio.emit('planilha_atualizada')
            sio.emit('relatar_progresso_vale', {'mensagem': 'Auto-Pilot: desligando o robô Vale; pausa de 1h30...'})
            try:
                comandar('desligar_robo', 'vale')
            except Exception:
                logger.exception('Falha ao desligar o robô vale no fim do ciclo')
            logger.info("Auto-Pilot: pausa de 1h30...")
            time.sleep(90 * 60)


def loop_agendamento():
    while True:
        for portal in ('coupa', 'vale'):
            try:
                checar_inatividade(portal)
            except Exception:
                logger.exception('Falha ao checar inatividade')
        schedule.run_pending()
        time.sleep(60)


def backup_inicial():
    """Backup ao ligar, se ainda não houver um de hoje."""
    try:
        caminho_backup_dir = CONFIG_GLOBAL.get("caminho_backups", "backups")
        if os.path.exists(caminho_backup_dir):
            hoje = datetime.now().strftime("%Y-%m-%d")
            if not any(hoje in f for f in os.listdir(caminho_backup_dir)):
                rotina_backup()
    except Exception:
        logger.exception('Falha no backup inicial')

RECONECTAR_S = 10

if __name__ == '__main__':
    # Threads só no gerenciador de verdade: importar este arquivo (testes, bots) não liga o piloto
    threading.Thread(target=loop_automacao, daemon=True).start()
    threading.Thread(target=monitorar_pastas_impressao, daemon=True).start()
    threading.Thread(target=loop_agendamento, daemon=True).start()
    threading.Thread(target=loop_metricas_servidor, daemon=True).start()
    backup_inicial()
    # Portal fora do ar na partida (reinício do Site-Maestro, reboot) não fecha o gerenciador: tenta até conectar.
    # Depois de conectado, quedas são reconectadas pelo próprio socketio.
    while True:
        try:
            logger.info(f"Conectando ao servidor: {URL_DO_SERVIDOR} ...")
            sio.connect(URL_DO_SERVIDOR, auth={"robo_secret": ROBO_SECRET})
            break
        except Exception as e:
            logger.error(f"Erro ao conectar com o servidor: {e}. Nova tentativa em {RECONECTAR_S} s.")
            time.sleep(RECONECTAR_S)
    sio.wait()


