# -*- coding: utf-8 -*-
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
from dotenv import load_dotenv
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

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
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=4, ensure_ascii=False)
        return default_config
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

CONFIG_GLOBAL = carregar_config()

# MODO DE TESTE LOCAL
if os.environ.get("URL_SERVIDOR") == "http://localhost:8000":
    CONFIG_GLOBAL["caminho_excel"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "teste.xlsx")
    CONFIG_GLOBAL["caminho_database_coupa"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "banco_teste.jsonl")
    CONFIG_GLOBAL["caminho_pedidos_json"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pedidos_teste.json")
    CONFIG_GLOBAL["caminho_database_vale"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eventos_nimbi_teste.jsonl")
    CONFIG_GLOBAL["caminho_banco_dados"] = os.path.dirname(os.path.abspath(__file__))
    CONFIG_GLOBAL["caminho_backups"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups_teste")
else:
    CONFIG_GLOBAL["caminho_banco_dados"] = r"\\SERVIDOR2\Publico\ALLAN\database\Banco-de-dados"
    CONFIG_GLOBAL["caminho_backups"] = r"\\SERVIDOR2\Publico\ALLAN\database\backups_excel"

from planilha_manager import planilha_manager
planilha_manager.iniciar(CONFIG_GLOBAL["caminho_excel"])

def salvar_config(novas_config):
    with open('CONFIG.json', 'w', encoding='utf-8') as f:
        json.dump(novas_config, f, indent=4, ensure_ascii=False)

# CREDENCIAIS DA LOCAWEB
SMTP_USER = CONFIG_GLOBAL.get("SMTP_USER", os.environ.get("SMTP_USER", ""))
SMTP_PASSWORD = CONFIG_GLOBAL.get("SMTP_PASSWORD", os.environ.get("SMTP_PASSWORD", ""))

def enviar_email_python(email_destino, token, tipo="ativacao"):
    if tipo == "ativacao":
        assunto = 'MAESTRO - Confirme o seu E-mail'
        link = f"https://portaismaestro-nb5v.onrender.com/index.html?action=verify&token={token}"
        mensagem = "Voc� solicitou acesso ao sistema Maestro. Clique no link para ativar a sua conta:"
        botao = "ATIVAR A MINHA CONTA"
    else:
        assunto = 'MAESTRO - Recupera��o de Senha'
        link = f"https://portaismaestro-nb5v.onrender.com/index.html?action=reset&token={token}"
        mensagem = "Voc� solicitou a recupera��o da sua senha. Clique no link para criar uma nova senha:"
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
        server = smtplib.SMTP('email-ssl.com.br', 587)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()

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

    assunto = "Maestro - Limpeza de Cota��es para Re-Extra��o"
    corpo = f"""Ol�,

Foi solicitada a atribui��o do vendedor "{vendedor}" pelo painel do Maestro.
No entanto, as seguintes cota��es n�o estavam presentes na planilha:

{', '.join(eventos_apagados)}

Para corrigir isso, elas foram apagadas da mem�ria do rob�. 
Na pr�xima rodada autom�tica, o rob� ir� extra�-las novamente do portal e as salvar� na planilha corretamente.

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

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s [MAESTRO] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

URL_DO_SERVIDOR = os.getenv("URL_SERVIDOR", "https://portaismaestro-nb5v.onrender.com")
ROBO_SECRET = os.getenv("ROBO_SECRET", "VEMKAUAN")
sio = socketio.Client()

# MONKEY-PATCH: Prote��o Global contra quedas de Socket
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

# 1. FUN��O PARA SALVAR NO MESMO FORMATO
def salvar_usuarios(usuarios):
    with open('banco_usuarios.json', 'w', encoding='utf-8') as f:
        json.dump(usuarios, f, indent=4, ensure_ascii=False)

@sio.on('registrar_usuario')
def registrar_usuario(dados):
    email = dados.get('email', '').lower()
    senha = dados.get('senha')
    ip_real = dados.get('ip_real', 'Desconhecida')
    client_id = dados.get('clientId')

    # ?? REGRA: Apenas dom�nio espec�fico
    if not email.endswith('@venturainformatica.com.br'):
        logger.warning(f"? Dom�nio inv�lido: {email}")
        sio.emit('resposta_cadastro', {
            'sucesso': False, 
            'erro': 'Utilize o seu e-mail @venturainformatica.com.br',
            'clientId': client_id
        })
        return

    usuarios = carregar_usuarios()
    if email in usuarios:
        sio.emit('resposta_cadastro', {'sucesso': False, 'erro': 'E-mail j� cadastrado.', 'clientId': client_id})
        return

    token = str(uuid.uuid4())
    usuarios[email] = {
        "senha": senha,
        "verificado": False, # ??? Fica False at� clicar no link
        "token_verificacao": token,
        "token_recuperacao": None,
        "ultima_localizacao": f"IP: {ip_real}",
        "ultimo_login": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "admin": False
    }

    # ... (c�digo acima continua igual, com a trava do dom�nio e a cria��o do json) ...

    salvar_usuarios(usuarios)
    logger.info(f"? Conta pr�-criada: {email}. Acionando o Carteiro Python!")

    # ?? AGORA O PR�PRIO PYTHON ENVIA O E-MAIL ??
    sucesso_email = enviar_email_python(email, token)

    if sucesso_email:
        logger.info("? E-mail enviado com sucesso pelo rob�!")
        # Avisa a Nuvem que tudo correu bem, para ela avisar o site
        sio.emit('resposta_cadastro', {'sucesso': True, 'clientId': client_id})
    else:
        # Se falhar (ex: senha bloqueada), apaga o utilizador fantasma do json
        del usuarios[email]
        salvar_usuarios(usuarios)
        sio.emit('resposta_cadastro', {
            'sucesso': False, 
            'erro': 'Erro ao enviar o e-mail pelo Rob� Local.', 
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

        # Converte para Base64 para poder enviar pela rede com seguran�a
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

                    # 2. Verifica se � a mensagem de finaliza��o
                    if "=== APLICA��O FINALIZADA ===" in linha_limpa or "=== SESS�O FINALIZADA ===" in linha_limpa:
                        logger.info("O rob� do ME finalizou a rotina naturalmente.")

                        rodando_monitor_me = False
                        processo_me = None

                        # 3. Avisa o frontend (me.html) para voltar o bot�o para INICIAR
                        sio.emit('sincronizar_estado_me', {'status': 'ocioso'})

                        # 4. Avisa o Servidor Global (server.js) para liberar o rob� para outras tarefas
                        sio.emit('tarefa_concluida', {'evento': 'Extra��o Mercado Eletr�nico', 'sucesso': True})

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

    # ?? A "MOCHILA" QUE VAI GUARDAR OS TEXTOS PARA O BOT�O COPIAR ??
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

                    # ========================================================
                    # 2. A M�GICA DO COLECIONADOR: Filtra e junta os dados!
                    # ========================================================
                    if linha_limpa.startswith("CDE:"):
                        # Se j� tiver alguma coisa na mochila (ou seja, � o 2� evento), d� 2 enters para separar
                        if textos_acumulados: 
                            textos_acumulados += "\n\n"
                        textos_acumulados += linha_limpa + "\n"

                    elif linha_limpa.startswith("MATERIAL:") or linha_limpa.startswith("T�RMINO") or linha_limpa.startswith("TERMINO"):
                        textos_acumulados += linha_limpa + "\n"

                    # ========================================================
                    # 3. FINALIZA��O: Entrega a mochila ao site!
                    # ========================================================
                    if "=== SESS�O FINALIZADA ===" in linha_limpa:

                        # ... dentro do IF do === SESS�O FINALIZADA === ...
                        if textos_acumulados.strip():
                            logger.info("Enviando textos gerados para o site...")
                            io.emit('findes_textos_gerados', {'texto': textos_acumulados.strip()})

                        sio.emit('relatar_progresso_findes', {'mensagem': '?? Sinal de Fim recebido! Fechando o rob� automaticamente...'})

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

    # ?? Aumentamos para 10 segundos porque o 'dotnet run' � demorado!
    time.sleep(10) 

    try:
        with open(caminho_log, 'r', encoding='utf-8', errors='ignore') as f:
            f.seek(0, os.SEEK_END)
            while rodando_monitor_ariba:
                linha = f.readline()
                if linha and linha.strip():
                    sio.emit('relatar_progresso_ariba', {'mensagem': linha.strip()})
                else:
                    time.sleep(0.5)
    except Exception as e: 
        logger.error(f"Erro no monitor do Ariba: {e}")

def monitorar_log_coupa():
    global rodando_monitor_coupa
    caminho_log = r"\\SERVIDOR2\Publico\ALLAN\Logs\log-coupa.txt"

    # D� 2 segundos para o rob� do Coupa iniciar e criar o arquivo
    time.sleep(2) 

    try:
        with open(caminho_log, 'r', encoding='utf-8', errors='ignore') as f:
            # Pula direto para o final para n�o ler logs velhos do dia anterior
            f.seek(0, os.SEEK_END) 
            while rodando_monitor_coupa:
                linha = f.readline()
                if linha and linha.strip():
                    # Envia tudo o que for escrito no TXT direto para o painel preto do Coupa!
                    sio.emit('relatar_progresso_coupa', {'mensagem': linha.strip()})
                else:
                    time.sleep(0.5)
    except Exception as e: 
        logger.error(f"Erro no monitor do Coupa: {e}")

def monitorar_log_vale():
    global rodando_monitor_vale
    caminho_log = r"\\SERVIDOR2\Publico\ALLAN\Logs\log-vale.txt"

    while rodando_monitor_vale and not os.path.exists(caminho_log):
        time.sleep(1)

    if not rodando_monitor_vale: 
        return 

    try:
        with open(caminho_log, 'r', encoding='utf-8', errors='ignore') as f:
            f.seek(0, os.SEEK_END) 
            while rodando_monitor_vale:
                linha = f.readline()

                if not linha:
                    time.sleep(0.5)
                    # ?? A CHAVE M�GICA: Obriga o Windows a verificar se h� texto novo! ??
                    f.seek(f.tell()) 
                elif linha.strip():
                    # Envia a linha direto para o terminal do site
                    sio.emit('relatar_progresso_vale', {'mensagem': linha.strip()})

    except Exception as e: 
        logger.error(f"Erro no monitor da Vale: {e}")

def monitorar_pastas_impressao():
    """Vigia as pastas de todos os portais e conta os arquivos prontos para impress�o"""
    pasta_me = CONFIG_GLOBAL.get("pasta_me", r"\\SERVIDOR2\Publico\ALLAN\MERCADO-ELETRONICO")
    pasta_coupa_vale = CONFIG_GLOBAL.get("pasta_coupa_vale", r"\\SERVIDOR2\Publico\ALLAN\eventos do coupa")
    pasta_ariba_aegea = CONFIG_GLOBAL.get("pasta_ariba_aegea", r"\\SERVIDOR2\Publico\ALLAN\AribaSourcing\AEGEA")
    pasta_ariba_estacio = CONFIG_GLOBAL.get("pasta_ariba_estacio", r"\\SERVIDOR2\Publico\ALLAN\AribaSourcing\EST�CIO")

    while True:
        try:
            if sio.connected:
                # 1. Contagem ME (PDFs)
                if os.path.exists(pasta_me):
                    qtd_me = len(glob.glob(os.path.join(pasta_me, '*.pdf')))
                    sio.emit('contagem_me', {'quantidade': qtd_me})

                # 2. Contagem Coupa/Vale (DOCX - ignorando arquivos tempor�rios do Word que come�am com ~)
                if os.path.exists(pasta_coupa_vale):
                    arquivos_cv = [f for f in glob.glob(os.path.join(pasta_coupa_vale, '*.docx')) if not os.path.basename(f).startswith("~")]
                    sio.emit('contagem_coupa_vale', {'quantidade': len(arquivos_cv)})

                # 3. Contagem Ariba (DOC/DOCX - ignorando tempor�rios)
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
    """Vigia a pasta do Mercado Eletr�nico e conta os PDFs em tempo real"""
    pasta_me = CONFIG_GLOBAL.get("pasta_me", r"\\SERVIDOR2\Publico\ALLAN\MERCADO-ELETRONICO")
    while True:
        try:
            if os.path.exists(pasta_me):
                # Conta apenas os arquivos .pdf
                qtd = len(glob.glob(os.path.join(pasta_me, '*.pdf')))

                # ?? S� ENVIA SE O ROB� ESTIVER CONECTADO AO SERVIDOR ??
                if sio.connected:
                    sio.emit('contagem_me', {'quantidade': qtd})
            else:
                # Se a pasta n�o for encontrada pela rede, tenta novamente no pr�ximo ciclo
                pass
        except Exception as e:
            logger.error(f"Erro no monitor de pasta do ME: {e}")

        time.sleep(3) # Atualiza a cada 3 segundos

def carregar_usuarios():
    path = 'banco_usuarios.json'
    if not os.path.exists(path): return {}
    with open(path, 'r', encoding='utf-8') as f: return json.load(f)

@sio.on("validar_login")
def validar_login(dados):
    usuarios = carregar_usuarios()
    email, senha, cid = dados.get('user'), dados.get('pass'), dados.get('clientId')

    if email in usuarios and str(usuarios[email]['senha']) == str(senha):

        # ?? TRAVA DE E-MAIL N�O VERIFICADO
        if not usuarios[email].get('verificado', False):
            sio.emit('resultado_login', {'sucesso': False, 'erro': 'Voc� precisa ativar sua conta! Verifique o link enviado para o seu e-mail.', 'clientId': cid})
            return

        if usuarios[email].get('bloqueado'): 
            sio.emit('resultado_login', {'sucesso': False, 'erro': 'Conta suspensa.', 'clientId': cid})
        else: 
            sio.emit('resultado_login', {'sucesso': True, 'user': email, 'isAdmin': usuarios[email].get('admin', False), 'isDev': usuarios[email].get('dev', False), 'hasDashboardAccess': usuarios[email].get('dashboard_access', False), 'clientId': cid})
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

    # 2. A senha estava certa! Prepara a lista de usu�rios
    usuarios = carregar_usuarios()

    # Atualiza quem est� online no momento
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

@sio.on('verificar_token_python')
def verificar_token_python(dados):
    token = dados.get('token')
    client_id = dados.get('clientId')
    usuarios = carregar_usuarios()

    sucesso = False
    email_validado = None

    for email, info in usuarios.items():
        if info.get('token_verificacao') == token:
            # Encontrou o token! Ativa a conta e destr�i o token
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
        sio.emit('resultado_verificacao_token', {'sucesso': False, 'erro': 'Link inv�lido ou j� utilizado.', 'clientId': client_id})

@sio.on('gerar_token_recuperacao')
def gerar_token_recuperacao(dados):
    email = dados.get('email', '').lower()
    client_id = dados.get('clientId')
    usuarios = carregar_usuarios()

    if email in usuarios:
        token = str(uuid.uuid4())
        usuarios[email]['token_recuperacao'] = token
        salvar_usuarios(usuarios)

        sucesso = enviar_email_python(email, token, tipo="recuperacao")
        if sucesso:
            sio.emit('resposta_recuperacao_solicitada', {'sucesso': True, 'clientId': client_id})
        else:
            sio.emit('resposta_recuperacao_solicitada', {'sucesso': False, 'erro': 'Erro ao enviar e-mail de recupera��o.', 'clientId': client_id})
    else:
        # Por seguran�a, n�o confirmamos se o e-mail existe ou n�o, mas enviamos 'sucesso' para a interface
        sio.emit('resposta_recuperacao_solicitada', {'sucesso': True, 'clientId': client_id})

@sio.on('processar_nova_senha')
def processar_nova_senha(dados):
    token = dados.get('token')
    nova_senha = dados.get('senha')
    client_id = dados.get('clientId')
    usuarios = carregar_usuarios()

    sucesso = False
    for email, info in usuarios.items():
        if info.get('token_recuperacao') == token:
            info['senha'] = nova_senha
            info['token_recuperacao'] = None
            sucesso = True
            break

    if sucesso:
        salvar_usuarios(usuarios)
        sio.emit('resultado_nova_senha', {'sucesso': True, 'clientId': client_id})
    else:
        sio.emit('resultado_nova_senha', {'sucesso': False, 'erro': 'Link inv�lido ou expirado.', 'clientId': client_id})

@sio.on('comando_para_robo')
def comando_para_robo(dados):
    global bot_coupa, bot_vale, bot_findes, portal_atual, processo_me, bot_ariba, rodando_monitor_me, rodando_monitor_ariba, rodando_monitor_coupa, rodando_monitor_vale

    modo, portal = dados.get('modo'), dados.get('portal')

    if portal == 'me':
        if modo == 'extrair':
            rodando_monitor_me = True
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

                    # ?? Fantasma R�pido: Sem travas de seguran�a que bloqueiam a digita��o!
                    vbs_code = f"""
Set WshShell = WScript.CreateObject("WScript.Shell")

' D� uma pausa de 4 segundos para o .NET carregar a tela de digita��o
WScript.Sleep 4000

' Tenta "puxar" a janela pelo PID e T�tulo s� por garantia, mas N�O ABORTA se o Windows esconder
On Error Resume Next
WshShell.AppActivate({bot_ariba.pid})
WshShell.AppActivate("ROBO_ARIBA")
WshShell.AppActivate("Administrador: ROBO_ARIBA")
On Error GoTo 0

WScript.Sleep 500

' Digita os dados assumindo que a janela rec�m-criada � a que est� na frente (Comportamento Original R�pido)
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
                    logger.error(f"Erro na execu��o do Ariba: {e}")
                    sio.emit('relatar_progresso_ariba', {'mensagem': f'Erro ao iniciar: {e}'})
                finally:
                    rodando_monitor_ariba = False
                    bot_ariba = None
                    sio.emit('sincronizar_estado_ariba', {'status': 'desligado'})

            threading.Thread(target=run_ariba, daemon=True).start()

        elif modo == 'desligar_robo':
            rodando_monitor_ariba = False
            sio.emit('relatar_progresso_ariba', {'mensagem': '?? Encerrando o m�dulo SAP Ariba...'})
            if bot_ariba: 
                os.system(f"taskkill /F /T /PID {bot_ariba.pid}")
                bot_ariba = None
            else: 
                os.system("taskkill /F /IM AribaSourcing.exe >nul 2>&1")

    elif portal in ['coupa', 'vale']:
        if modo == 'ligar_robo':
            portal_atual = portal
            if portal == 'coupa':
                from bots.coupa import CoupaScraper
                bot_coupa = CoupaScraper(sio, aguardando_clique, coordenadas_clique)
                rodando_monitor_coupa = True
                threading.Thread(target=monitorar_log_coupa, daemon=True).start()
                threading.Thread(target=bot_coupa.iniciar_e_logar, daemon=True).start()
            else:
                from bots.vale_bot import ValeScraper
                bot_vale = ValeScraper(sio, aguardando_clique, coordenadas_clique)
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

        # ?? INICIA TAREFAS NORMAIS ??
        elif modo in ['extrair', 'verificar', 'responder', 'forcar_troca']:
            bot_alvo = bot_coupa if portal == 'coupa' else bot_vale
            if bot_alvo:
                th = threading.Thread(target=bot_alvo.iniciar_tarefa, args=(modo, dados), daemon=True)
                bot_alvo.thread_atual = th
                th.start()
            else:
                sio.emit('relatar_progresso', {'mensagem': "? Erro: O rob� n�o est� ligado no Servidor!"})

        # ?? O FREIO DE M�O AGORA EST� ISOLADO E RECEBE QUALQUER VARIA��O DO COMANDO ??
        elif modo in ['solicitar_parada', 'parar_extracao']:
            bot_alvo = bot_coupa if portal == 'coupa' else bot_vale
            if bot_alvo:
                bot_alvo.solicitacao_parada = True 

                logger.warning(f"?? ATEN��O: Comando de PARADA recebido do portal {portal.upper()}!")
                canal = f'relatar_progresso_{portal}' if portal else 'relatar_progresso'
                sio.emit(canal, {'mensagem': "?? Freio acionado! Concluindo o evento atual com seguran�a antes de parar..."})
            else:
                logger.warning("?? Comando de parada recebido, mas nenhum rob� estava extraindo no momento.")

    # ========================================================
    # ?? ROTA .NET DO FINDES (INICIA COMO O ARIBA) ??
    # ========================================================
    elif portal == 'findes':
        if modo == 'ligar_robo':
            eventos_str = dados.get('evento', '')
            sio.emit('relatar_progresso_findes', {'mensagem': f"?? Iniciando M�dulo .NET Findes para os eventos: {eventos_str}"})

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

                    # ?? USANDO O S�MBOLO '~' QUE � O 'ENTER' INFAL�VEL DO VBSCRIPT ??
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

' --- PASSO 3: INICIAR O ROB� ---
WshShell.SendKeys "dotnet run"
WScript.Sleep 500
WshShell.SendKeys "~"

' D� 10 segundos para o .NET carregar antes de come�ar a mandar os eventos
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

                    # O Python fica � espera que a janela feche
                    bot_findes.wait()

                except Exception as e:
                    logger.error(f"Erro na execu��o do Findes: {e}")
                    sio.emit('relatar_progresso_findes', {'mensagem': f'? Erro Cr�tico ao iniciar: {e}'})
                finally:
                    if bot_findes:
                        bot_findes = None
                        sio.emit('relatar_progresso_findes', {'mensagem': "? M�dulo Findes conclu�do/fechado.", 'comando_interno': 'desligar_botoes'})

            # Roda o Findes em paralelo para n�o travar o gerenciador
            threading.Thread(target=run_findes, daemon=True).start()

        elif modo == 'desligar_robo':
            if bot_findes:
                sio.emit('relatar_progresso_findes', {'mensagem': '?? Freio acionado! Encerrando o Findes...'})
                os.system(f"taskkill /F /T /PID {bot_findes.pid}")
                bot_findes = None
            else:
                sio.emit('relatar_progresso_findes', {'mensagem': "?? O rob� j� estava desligado."})
            sio.emit('relatar_progresso_findes', {'comando_interno': 'desligar_botoes'})

        elif modo == 'mover_anexos_findes':
            sio.emit('relatar_progresso_findes', {'mensagem': "?? Fun��o de mover anexos precisa ser acoplada � l�gica do .NET."})

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
    salvar_config(nova_config)
    CONFIG_GLOBAL = nova_config
    SMTP_USER = CONFIG_GLOBAL.get("SMTP_USER", os.environ.get("SMTP_USER", ""))
    SMTP_PASSWORD = CONFIG_GLOBAL.get("SMTP_PASSWORD", os.environ.get("SMTP_PASSWORD", ""))
    sio.emit('receber_config_admin', {'config': CONFIG_GLOBAL, 'clientId': client_id, 'sucesso': True})

@sio.on('connect')
def connect():
    logger.info("?? Conectado!")
    sio.emit('sou_o_robo')

@sio.on('comando_imprimir')
def comando_imprimir(dados):
    portal = dados.get('portal')
    impressora = dados.get('impressora')
    sio.emit('relatar_progresso', {'mensagem': f"??? A preparar impress�o no portal {portal.upper()}..."})

    def job_imprimir():
        try:
            # 1. Define a impressora padr�o do Windows
            subprocess.run(f'RUNDLL32 PRINTUI.DLL,PrintUIEntry /y /n "{impressora}"', shell=True)
            time.sleep(2)

            arquivos_encontrados = []

            # 2. Mapeia as pastas corretas dependendo do portal
            if portal == 'me':
                pasta_origem = r"\\SERVIDOR2\Publico\ALLAN\MERCADO-ELETRONICO"
                pasta_destino = os.path.join(pasta_origem, "J� imprimiu")
                os.makedirs(pasta_destino, exist_ok=True)
                for f in glob.glob(os.path.join(pasta_origem, "*.pdf")):
                    arquivos_encontrados.append((f, pasta_destino))

            elif portal in ['coupa', 'vale']:
                pasta_origem = r"\\SERVIDOR2\Publico\ALLAN\eventos do coupa"
                pasta_destino = r"\\SERVIDOR2\Publico\RFQ�S VALE PARA IMPRIMIR"
                os.makedirs(pasta_destino, exist_ok=True)
                for f in glob.glob(os.path.join(pasta_origem, "*.docx")):
                    if not os.path.basename(f).startswith("~"):
                        arquivos_encontrados.append((f, pasta_destino))

            elif portal == 'ariba':
                # ?? AGORA O C�DIGO APONTA PARA AS SUBPASTAS EXISTENTES ??
                fontes = [
                    (r"\\SERVIDOR2\Publico\ALLAN\AribaSourcing\AEGEA", r"\\SERVIDOR2\Publico\ALLAN\AribaSourcing\AEGEA\J� imprimiu"),
                    (r"\\SERVIDOR2\Publico\ALLAN\AribaSourcing\EST�CIO", r"\\SERVIDOR2\Publico\ALLAN\AribaSourcing\EST�CIO\j� imprimiu")
                ]
                for orig, dest in fontes:
                    # Verifica se a pasta principal existe antes de procurar arquivos
                    if os.path.exists(orig):
                        # Garante que a subpasta de destino seja reconhecida
                        os.makedirs(dest, exist_ok=True)
                        for f in glob.glob(os.path.join(orig, "*.doc*")):
                            # Ignora arquivos tempor�rios (aqueles que come�am com ~)
                            if not os.path.basename(f).startswith("~"):
                                arquivos_encontrados.append((f, dest))

            # 3. Executa a impress�o e move
            if not arquivos_encontrados:
                sio.emit('relatar_progresso', {'mensagem': f"?? Nenhuma cota��o encontrada nas pastas do {portal.upper()}."})
                sio.emit('tarefa_concluida', {'evento': 'Impress�o Lote', 'sucesso': True})
                return

            for arq, dest in arquivos_encontrados:
                nome = os.path.basename(arq)
                try:
                    os.startfile(arq, "print")
                    time.sleep(12) # Tempo para o Word/Acrobat processar a p�gina
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

            sio.emit('tarefa_concluida', {'evento': 'Impress�o Lote', 'sucesso': True, 'portal': portal})

        except Exception as e:
            logger.error(f"Erro Geral na Impress�o: {e}")
            sio.emit('tarefa_concluida', {'evento': 'Impress�o Lote', 'sucesso': False, 'erro': str(e), 'portal': portal})

    threading.Thread(target=job_imprimir, daemon=True).start() 

    import openpyxl

@sio.on('comando_carregar_planilha_json')
def comando_carregar_planilha_json(dados):
    dados = dados or {}
    client_id = dados.get('clientId')
    origem = dados.get('origem', 'todas')
    try:
        from planilha_manager import planilha_manager
        planilha_manager.iniciar()

        cotacoes = planilha_manager.cotacoes.get("COTA��O", [])
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

            # Filtrar
            cotacoes = [c for c in cotacoes if str(c.get('COTA��O', '')).strip() in chaves_validas]

        # Enviar os �ltimos 500 registros invertidos
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

        linhas = planilha_manager.cotacoes.get("COTA��O", [])

        for row in linhas:
            if not row.get("COTA��O"): continue 

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
            situacao = str(row.get("RESPOSTA", "")).strip().upper()
            if situacao == "NONE": situacao = ""

            totais["total"] += 1
            is_respondida = ("RESPONDIDA" in situacao or "RESPONDIDO" in situacao) and not ("N�O" in situacao or "NAO" in situacao or "NO" in situacao)
            is_nao_respondida = ("N�O RESPONDID" in situacao or "NAO RESPONDID" in situacao or "NO RESPONDID" in situacao)

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

            ignorar_vendedores = ["informa�oes insuficientes para or�ar", "pouco tempo h�bil", "vencida", "provavel vencida", "w"]
            if vendedor.lower() not in ignorar_vendedores:
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
        sio.emit('retorno_registro_vendedor_cotacao', {'sucesso': False, 'erro': 'Vendedor ou cota��es vazias', 'clientId': client_id})
        return

    import re
    # Separa as cota��es por v�rgula ou espa�o
    lista_cotacoes = [c.strip() for c in re.split(r'[,\s]+', cotacoes_raw) if c.strip()]

    if not lista_cotacoes:
        sio.emit('retorno_registro_vendedor_cotacao', {'sucesso': False, 'erro': 'Nenhuma cota��o v�lida encontrada', 'clientId': client_id})
        return

    caminho_excel = CONFIG_GLOBAL.get("caminho_excel", r"\\SERVIDOR2\Publico\PLANILHA DE CONTROLE VALE - ESTAGIARIOS (copia 1).xlsx")

    import time

    sucesso_save = False
    try:
        from planilha_manager import planilha_manager
        planilha_manager.iniciar()
        with planilha_manager.lock:
            encontradas_set = set()
            cotacoes_lista = planilha_manager.cotacoes.get("COTA��O", [])
            for cota in cotacoes_lista:
                val_str = str(cota.get("COTA��O", "")).strip()
                if val_str in lista_cotacoes:
                    cota["VENDEDOR"] = vendedor
                    encontradas_set.add(val_str)

            planilha_manager.salvar()
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
                    with open(caminho + ".tmp", 'w', encoding='utf-8') as f:
                        for k, v in db_novo.items(): f.write(json.dumps(v) + '\n')
                    import shutil
                    shutil.move(caminho + ".tmp", caminho)

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
                msg = f"Vendedor {vendedor} registrado em {len(encontradas_set)} cota��o(�es) com sucesso!"
                if eventos_banco:
                    msg += f"<br><br><span style='color: #eab308;'><i class='fa-solid fa-broom'></i> <b>ATEN��O:</b> As cota��es <b>{', '.join(eventos_banco)}</b> n�o est�o na planilha. Elas foram apagadas da mem�ria do rob� para serem extra�das novamente na pr�xima rodada autom�tica.</span>"
                if eventos_invalidos:
                    msg += f"<br><br><span style='color: #f87171;'><i class='fa-solid fa-triangle-exclamation'></i> <b>ERRO:</b> As cota��es <b>{', '.join(eventos_invalidos)}</b> n�o constam nem na planilha nem na mem�ria do rob�.</span>"

                sio.emit('retorno_registro_vendedor_cotacao', {
                    'sucesso': True, 
                    'mensagem': msg, 
                    'clientId': client_id
                })
            else:
                msg = f"Nenhuma das cota��es informadas foi encontrada na planilha.<br><br>Cota��es procuradas: {', '.join(lista_cotacoes)}"
                if eventos_banco:
                    msg += f"<br><br><span style='color: #3b82f6;'><i class='fa-solid fa-broom'></i> <b>LIMPEZA:</b> As cota��es <b>{', '.join(eventos_banco)}</b> foram apagadas da mem�ria para serem re-extra�das na pr�xima rodada.</span>"
                if eventos_invalidos:
                    msg += f"<br><br><span style='color: #f87171;'><i class='fa-solid fa-triangle-exclamation'></i> <b>ERRO:</b> As cota��es <b>{', '.join(eventos_invalidos)}</b> n�o constam nem na planilha nem na mem�ria do rob�.</span>"

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
        sio.emit('retorno_registro_cotacao_manual', {'sucesso': False, 'erro': 'Nenhuma cota��o recebida', 'clientId': client_id})
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
        planilha_manager.adicionar_linhas("COTA��O", linhas)
        sucesso_save = True

        sio.emit('retorno_registro_cotacao_manual', {'sucesso': True, 'mensagem': f'{len(cotacoes)} cota��es registradas com sucesso!', 'clientId': client_id})

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
        # Iniciar no gerenciador pode ser bom, embora teoricamente j� deva estar
        caminho_excel = CONFIG_GLOBAL.get("caminho_excel", r"\SERVIDOR2\Publico\PLANILHA DE CONTROLE VALE - ESTAGIARIOS (copia 1).xlsx")
        planilha_manager.iniciar(caminho_excel)

        sucesso = planilha_manager.adicionar_linhas_pedidos("PEDIDO", pedidos)
        if sucesso:
            sio.emit('resposta_registro_pedido_manual', {'sucesso': True, 'mensagem': f'{len(pedidos)} pedidos registrados com sucesso!', 'clientId': client_id})
        else:
            sio.emit('resposta_registro_pedido_manual', {'sucesso': False, 'erro': 'Aba de Pedidos n�o encontrada ou erro ao gravar.', 'clientId': client_id})
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
            emit_progresso(f'Ligando rob� do {portal_nome.title()}...')
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

        # Ler a planilha e marcar como extra�do
        from planilha_manager import planilha_manager
        planilha_manager.iniciar()
        with planilha_manager.lock:
            cotacoes_lista = planilha_manager.cotacoes.get("COTA��O", [])
            salvou = False
            for cota in cotacoes_lista:
                val_str = str(cota.get("COTA��O", "")).strip()
                if val_str in pend:
                    cota["VENDEDOR"] = mapa_vendedores.get(val_str, '')
                    salvou = True

            if salvou:
                planilha_manager.salvar()

        emit_progresso('Processamento da fila conclu�do com sucesso!', cor="#22c55e")
    except Exception as e:
        logger.error(f"Erro conexao: {e}")
        emit_progresso(f'Erro no processamento da fila: {e}', cor="#ef4444")

@sio.on('comando_apagar_linha')
def comando_apagar_linha(dados):
    client_id = dados.get('clientId')
    tipo = dados.get('tipo')
    chave = dados.get('chave')

    try:
        from planilha_manager import planilha_manager
        planilha_manager.iniciar()

        with planilha_manager.lock:
            lista = planilha_manager.cotacoes["COTA��O"] if tipo == 'cotacoes' else planilha_manager.pedidos["PEDIDOS"]
            chave_busca = "COTA��O" if tipo == 'cotacoes' else "PEDIDO"

            # Find and remove
            for i, item in enumerate(lista):
                if item is None: continue
                if str(item.get(chave_busca, "")).strip() == str(chave).strip():
                    del lista[i]
                    break

            planilha_manager.salvar()

        sio.emit('resposta_acao_linha', {'sucesso': True, 'mensagem': 'Linha apagada com sucesso!', 'clientId': client_id})
    except Exception as e:
        sio.emit('resposta_acao_linha', {'sucesso': False, 'erro': str(e), 'clientId': client_id})

@sio.on('comando_editar_linha')
def comando_editar_linha(dados):
    client_id = dados.get('clientId')
    tipo = dados.get('tipo')
    chave = dados.get('chave')
    novos_dados = dados.get('novos_dados', {})

    try:
        from planilha_manager import planilha_manager
        planilha_manager.iniciar()

        with planilha_manager.lock:
            lista = planilha_manager.cotacoes["COTA��O"] if tipo == 'cotacoes' else planilha_manager.pedidos["PEDIDOS"]
            chave_busca = "COTA��O" if tipo == 'cotacoes' else "PEDIDO"

            # Find and update
            for i, item in enumerate(lista):
                if item is None: continue
                if str(item.get(chave_busca, "")).strip() == str(chave).strip():
                    # Update all keys provided
                    for k, v in novos_dados.items():
                        lista[i][k] = v
                    break

            planilha_manager.salvar()

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
            shutil.copy2(planilha_manager.caminho_cotacoes, os.path.join(caminho_backup_dir, f"COTA��ES {hoje_str}.json"))

        # Backup PEDIDOS
        if os.path.exists(planilha_manager.caminho_pedidos):
            shutil.copy2(planilha_manager.caminho_pedidos, os.path.join(caminho_backup_dir, f"PEDIDOS {hoje_str}.json"))

        logger.info(f"Backup realizado com sucesso as {hoje_str}")
    except Exception as e:
        logger.error(f"Erro ao realizar backup: {e}")

# Agendar para toda manha as 07:00
schedule.every().day.at("07:00").do(rotina_backup)

def rotina_impressao_segura():
    def _impressao_thread():
        logger.info("? Hor�rio de impress�o atingido! Verificando se os rob�s est�o ocupados...")
        sio.emit('relatar_progresso_coupa', {'mensagem': '? Hor�rio de impress�o (11h/15h). Verificando disponibilidade...'})
        while True:
            coupa_ocupado = globals().get('bot_coupa') and getattr(globals().get('bot_coupa'), 'thread_atual', None) and globals().get('bot_coupa').thread_atual.is_alive()
            vale_ocupado = globals().get('bot_vale') and getattr(globals().get('bot_vale'), 'thread_atual', None) and globals().get('bot_vale').thread_atual.is_alive()
            if coupa_ocupado or vale_ocupado:
                logger.info("? Rob�s est�o trabalhando. Aguardando 30 segundos para imprimir...")
                time.sleep(30)
            else:
                break
        
        logger.info("??? Rob�s livres! Iniciando impress�o agendada...")
        sio.emit('relatar_progresso_coupa', {'mensagem': '??? Rob�s livres! Enviando cota��es para a impressora HP...'})
        comando_imprimir({'portal': 'coupa', 'impressora': 'HP LaserJet P205X series PCL6 Class Driver', 'avisar_email': True})

    threading.Thread(target=_impressao_thread, daemon=True).start()


def enviar_email_erro(detalhes_erro):
    try:
        user = CONFIG_GLOBAL.get("SMTP_USER")
        pwd = CONFIG_GLOBAL.get("SMTP_PASSWORD")
        to = CONFIG_GLOBAL.get("emails_relatorio", user)
        if not user or not pwd: return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "?? ALERTA CR�TICO - Rob� Maestro ??"
        msg["From"] = user
        msg["To"] = to

        corpo = f"""
        <div style="font-family: Arial; padding: 20px; background: #0f1115; color: #fff; border-radius: 8px;">
            <h2 style="color: #e5534b;">?? Erro na Automa��o</h2>
            <p>O rob� encontrou um problema cr�tico durante a execu��o do ciclo autom�tico (Auto-Pilot).</p>
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
        logger.error(f"Erro ao enviar email de erro cr�tico: {e}")

def loop_automacao():
    logger.info("?? Iniciando Auto-Pilot (Coupa -> Verifica��o -> 1.5h -> Vale -> 1.5h)...")
    time.sleep(10)

    while True:
        # === COUPA ===
        try:
            sio.emit('relatar_progresso_coupa', {'mensagem': '?? Auto-Pilot: Iniciando ciclo de Extra��o do COUPA...'})

            sio.emit('comando_direto', {'modo': 'ligar_robo', 'portal': 'coupa'})

            espera = 0
            while not (globals().get('bot_coupa') and getattr(globals().get('bot_coupa'), 'is_ready', False)):
                time.sleep(2)
                espera += 2
                if espera > 120: raise Exception("Timeout ao ligar o rob� Coupa. Poss�vel Captcha ou bloqueio.")

            sio.emit('comando_direto', {'modo': 'extrair', 'portal': 'coupa'})

            time.sleep(10)
            while globals().get('bot_coupa') and getattr(globals().get('bot_coupa'), 'thread_atual', None) and globals().get('bot_coupa').thread_atual.is_alive():
                time.sleep(5)

            

            sio.emit('relatar_progresso_coupa', {'mensagem': '?? Auto-Pilot: Extra��o do Coupa finalizada. Iniciando Verifica��o (Pente Fino)...'})

            sio.emit('comando_direto', {'modo': 'verificar', 'portal': 'coupa'})

            time.sleep(10)
            while globals().get('bot_coupa') and getattr(globals().get('bot_coupa'), 'thread_atual', None) and globals().get('bot_coupa').thread_atual.is_alive():
                time.sleep(5)

        except Exception as e_coupa:
            import traceback
            msg_erro = f"""Auto-Pilot falhou no COUPA:\n{e_coupa}\n\n{traceback.format_exc()}"""
            enviar_email_erro(msg_erro)
            logger.error(f"Erro no Coupa: {e_coupa}")
            sio.emit('relatar_progresso_coupa', {'mensagem': f'? Erro ou Captcha no Coupa. Cancelando ciclo: {e_coupa}'})
        finally:
            sio.emit('planilha_atualizada')
            sio.emit('relatar_progresso_coupa', {'mensagem': '?? Auto-Pilot: Desligando rob� Coupa e entrando em Pausa...'})

            sio.emit('comando_direto', {'modo': 'desligar_robo', 'portal': 'coupa'})

            sio.emit('relatar_progresso_coupa', {'mensagem': '?? Auto-Pilot: Coupa em pausa de 1 hora e 30 minutos...'})
            logger.info("?? Auto-Pilot: Pausa de 1.5h...")
            time.sleep(90 * 60) # 1.5 horas

        # === VALE ===
        try:
            sio.emit('relatar_progresso_vale', {'mensagem': '?? Auto-Pilot: Iniciando ciclo da VALE...'})

            sio.emit('comando_direto', {'modo': 'ligar_robo', 'portal': 'vale'})

            espera = 0
            while not (globals().get('bot_vale') and getattr(globals().get('bot_vale'), 'is_ready', False)):
                time.sleep(2)
                espera += 2
                if espera > 120: raise Exception("Timeout ao ligar o rob� Vale.")

            sio.emit('comando_direto', {'modo': 'extrair', 'portal': 'vale'})

            time.sleep(10)
            while globals().get('bot_vale') and getattr(globals().get('bot_vale'), 'thread_atual', None) and globals().get('bot_vale').thread_atual.is_alive():
                time.sleep(5)

        except Exception as e_vale:
            import traceback
            msg_erro = f"""Auto-Pilot falhou na VALE:\n{e_vale}\n\n{traceback.format_exc()}"""
            enviar_email_erro(msg_erro)
            logger.error(f"Erro na Vale: {e_vale}")
            sio.emit('relatar_progresso_vale', {'mensagem': f'? Erro ou Captcha na Vale. Cancelando ciclo: {e_vale}'})
        finally:
            sio.emit('planilha_atualizada')
            sio.emit('relatar_progresso_vale', {'mensagem': '?? Auto-Pilot: Desligando rob� Vale e entrando em Pausa...'})

            sio.emit('comando_direto', {'modo': 'desligar_robo', 'portal': 'vale'})

            sio.emit('relatar_progresso_vale', {'mensagem': '?? Auto-Pilot: Vale em pausa de 1 hora e 30 minutos...'})
            logger.info("?? Auto-Pilot: Pausa de 1.5h...")
            time.sleep(90 * 60) # 1.5 horas

threading.Thread(target=loop_automacao, daemon=True).start()

def loop_agendamento():
    while True:
        schedule.run_pending()
        time.sleep(60)

threading.Thread(target=loop_agendamento, daemon=True).start()

# Forcar um backup logo ao ligar, se nao existir backup de hoje
try:
    caminho_backup_dir = CONFIG_GLOBAL.get("caminho_backups", "backups")
    if os.path.exists(caminho_backup_dir):
        hoje = datetime.now().strftime("%Y-%m-%d")
        ja_tem = any(hoje in f for f in os.listdir(caminho_backup_dir))
        if not ja_tem:
            rotina_backup()
except: pass

if __name__ == '__main__':
    try:
        logger.info(f"Conectando ao servidor: {URL_DO_SERVIDOR} ...")
        sio.connect(URL_DO_SERVIDOR)
        sio.wait()
    except Exception as e:
        logger.error(f"Erro ao conectar com o servidor: {e}")


