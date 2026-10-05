# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
Módulo Especialista: Vale Base Metals (A partir do Sourcing Privado)
Construído do Zero com Paginação e Dropdown Específicos.
"""
import os
from dotenv import load_dotenv
load_dotenv()
import re
import time
import json
import logging
import shutil
import tempfile
import sys
import threading
import traceback
from datetime import datetime
from typing import List, Dict, Optional
from google import genai
from groq import Groq

# === EXCEL ===
import pandas as pd
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Alignment, Border, Side, PatternFill

# === SELENIUM ===
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoAlertPresentException
from selenium.webdriver.common.action_chains import ActionChains

from docx import Document
from docx.shared import Pt, RGBColor, Cm
import base64

logger = logging.getLogger("ValeApp")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')

# 👇 GARANTE A GRAVAÇÃO SEM APAGAR O HISTÓRICO 👇
CAMINHO_LOG_VALE = r"\\SERVIDOR2\Publico\ALLAN\Logs\log-vale.txt"
os.makedirs(os.path.dirname(CAMINHO_LOG_VALE), exist_ok=True) 

# O mode='a' significa APPEND (Adicionar no fim, sem apagar o que já existe)
fh = logging.FileHandler(CAMINHO_LOG_VALE, mode='a', encoding='utf-8')
fh.setFormatter(formatter)

ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(formatter)

if not logger.handlers:
    logger.addHandler(ch)
    logger.addHandler(fh) # Adiciona o gravador de TXT

class ValeScraper:
    def __init__(self, sio_instance, evento_clique, dict_coordenadas):
        # --- FERRAMENTAS DO MAESTRO ---
        self.sio = sio_instance
        self.aguardando_clique = evento_clique
        self.coordenadas_clique = dict_coordenadas
        self.solicitacao_parada = False

        # --- CARREGAR CONFIG ---
        self.config = {}
        try:
            if os.path.exists('CONFIG.json'):
                with open('CONFIG.json', 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
        except Exception:
            pass

        # --- PASTAS ---
        self.BASE_DIR = self.config.get("BASE_DIR", r"\\SERVIDOR2\Publico\ALLAN")
        self.PASTA_RELATORIOS = self.config.get("pasta_coupa_vale", os.path.join(self.BASE_DIR, "eventos do coupa"))
        self.PASTA_BLOQUEADOS = self.config.get("PASTA_BLOQUEADOS", os.path.join(self.BASE_DIR, "eventosBLOCK"))
        self.PASTA_DATABASE = self.config.get("PASTA_DATABASE", os.path.join(self.BASE_DIR, "database"))
        self.PASTA_BACKUPS = os.path.join(self.PASTA_DATABASE, "backups_excel")
        self.PASTA_LOGS = os.path.join(self.PASTA_DATABASE, "logs")
        
        self.ARQUIVO_JSONL = os.path.join(self.PASTA_DATABASE, "eventos_nimbi.jsonl")
        self.ARQUIVO_MARCAS_BLOQUEADAS = os.path.join(self.PASTA_DATABASE, "marcas_bloqueadas.xlsx")
        self.ARQUIVO_PLANILHA_CONTROLE = self.config.get("caminho_excel", r"\\SERVIDOR2\Publico\PLANILHA DE CONTROLE VALE - ESTAGIARIOS (copia 1).xlsx")
        self.ARQUIVO_BASE_MARCAS = os.path.join(self.PASTA_DATABASE, "PLANILHA DE MARCAS.xlsx")
        self.ARQUIVO_MEMORIA_PART_NUMBERS = os.path.join(self.PASTA_DATABASE, "memoria_part_numbers.json")
        
        self.NOME_ABA_ALVO = "COTAÇÃO" 
        
        # --- URLs BASE ---
        self.BASE_SUPPLIER_URL = "https://supplier.coupahost.com"
        self.LOGIN_URL = f"{self.BASE_SUPPLIER_URL}/sessions/new"
        self.EVENTS_URL = f"{self.BASE_SUPPLIER_URL}/quotes/private_events/"

        self.TAMANHO_DO_LOTE = 15
        self.db_eventos: Dict[str, Dict] = {} 
        self.driver = None
        self.wait = None
        self.dados_processados_lote = []
        self.memoria_associativa = {}

        self.usar_groq_hoje = False
        self.dia_falha_gemini = None

    def verificar_acesso_pastas(self):
        pastas = [self.PASTA_RELATORIOS, self.PASTA_BLOQUEADOS, self.PASTA_DATABASE, self.PASTA_BACKUPS, self.PASTA_LOGS]
        for p in pastas: os.makedirs(p, exist_ok=True)
        if not os.path.exists(self.ARQUIVO_MARCAS_BLOQUEADAS):
            pd.DataFrame({"Marcas Bloqueadas": ["SCHNEIDER"]}).to_excel(self.ARQUIVO_MARCAS_BLOQUEADAS, index=False)
        if not os.path.exists(self.ARQUIVO_BASE_MARCAS):
            pd.DataFrame({"Marcas Conhecidas": ["WEG"]}).to_excel(self.ARQUIVO_BASE_MARCAS, index=False)
        if not os.path.exists(self.ARQUIVO_PLANILHA_CONTROLE):
            wb = Workbook(); ws = wb.active; ws.title = self.NOME_ABA_ALVO
            ws.append(["COTAÇÃO", "VENCIMENTO", "ITEM", "QUANTIDADE", "LOCALIDADE", "VENDEDOR", "MODELOS", "MARCAS", "RESPOSTA"])
            wb.save(self.ARQUIVO_PLANILHA_CONTROLE)
        if not os.path.exists(self.ARQUIVO_JSONL):
            with open(self.ARQUIVO_JSONL, 'w', encoding='utf-8') as f: f.write("")

    def carregar_banco_dados(self):
        self.db_eventos = {}
        if os.path.exists(self.ARQUIVO_JSONL):
            with open(self.ARQUIVO_JSONL, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        if d.get('titulo'): self.db_eventos[d['titulo']] = d
                    except: pass
        logger.info(f" Banco interno carregado: {len(self.db_eventos)} registros.")

    def salvar_evento_jsonl(self, numero: str, respondido: bool = False, vencimento_tabela: str = ""):
        if numero:
            try:
                # Guarda o vencimento_tabela no JSONL
                novo = {
                    "titulo": numero, 
                    "data": str(datetime.now().date()), 
                    "respondido": respondido,
                    "vencimento_tabela": vencimento_tabela
                }
                with open(self.ARQUIVO_JSONL, 'a', encoding='utf-8') as f: 
                    f.write(json.dumps(novo) + '\n')
                self.db_eventos[numero] = novo
            except: pass

    def evento_ja_processado(self, num: str) -> bool: 
        return num in self.db_eventos

    def setup_driver(self):
        logger.info("ðŸš— Configurando Navegador...")
        ops = Options()
        ops.add_argument("--no-sandbox")
        ops.add_argument("--disable-dev-shm-usage")
        ops.add_argument("--window-size=1920,1080")
        self.driver = webdriver.Chrome(options=ops)
        self.driver.set_page_load_timeout(60)
        self.wait = WebDriverWait(self.driver, 40)

        #==============================================
        self.memoria_associativa = {}
        
        # === 1. VARIÃVEL E OUVINTE DO OTP ===
        self.otp_atual = None 
        
        @self.sio.on('receber_otp_frontend')
        def on_receber_otp(dados):
            self.otp_atual = dados.get('otp')
            logger.info(f" Código OTP recebido do Painel: {self.otp_atual}")

    # ==========================================

    def iniciar_tarefa(self, modo, dados_extras=None):
        logger.info(f"📥 Ordem recebida da Nuvem! Comando: '{modo}' | Tem dados extras? {'Sim' if dados_extras else 'Não'}")
        
        # 👇 1. A NOVA BLINDAGEM DE PARADA IMEDIATA 👇
        if modo in ["solicitar_parada", "parar_extracao"]:
            self.solicitacao_parada = True
            logger.info("🛑 Comando de Parada Segura ativado! O robô abortará no próximo ciclo.")
            self.sio.emit('relatar_progresso_vale', {'mensagem': "🛑 Parando extração de forma segura..."})
            return # Impede que o código continue
            
        try:
            if modo == "extrair":
                self.varrer_painel_eventos()
            elif modo == "responder" and dados_extras:
                self.responder_evento(dados_extras)
            elif modo == "verificar":             # <--- ADICIONE ESTA LINHA
                self.verificar_eventos()          # <--- E ESTA LINHA AQUI!
        except Exception as e: 
            logger.error(f" Erro Crítico na Tarefa: {e}")

    # ============================ 1. LOGIN HÃBRIDO ============================
    def fazer_login_hibrido(self):
        logger.info(" Iniciando Login Inteligente. Acompanhe pelo Painel Web!")
        self.driver.get(self.LOGIN_URL)
        ultimo_print = 0
        ultimo_clique_botao = 0 # <--- NOVO: Controle para evitar flood de cliques
        email = "VENDAS@VENTURAINFORMATICA.COM.BR"
        senha = os.environ.get("PORTAL_SENHA", os.environ.get("PORTAL_SENHA", "Ventura2025*"))

        while True:
            time.sleep(0.3)
            if len(self.driver.window_handles) > 1:
                self.driver.switch_to.window(self.driver.window_handles[-1])

            if "sessions/new" not in self.driver.current_url:
                # ANTES DE COMEMORAR: Verifica se o portal jogou o botão de confirmação na tela
                try:
                    botoes_finais = self.driver.find_elements(By.ID, "login_button")
                    if botoes_finais and botoes_finais[0].is_displayed():
                        logger.info(" Botão 'Log in' azul de confirmação detectado! Clicando...")
                        self.driver.execute_script("arguments[0].click();", botoes_finais[0])
                        time.sleep(4)
                        continue # Volta para o loop para garantir que avançou de vez
                except: 
                    pass
                
                # Se não tem botão na tela, aí sim o login acabou!
                logger.info("✅ Login concluído com sucesso!")
                return True

            try:
                self.driver.switch_to.default_content()

                # --- A. ESTADO DO CAPTCHA ---
                frames = self.driver.find_elements(By.TAG_NAME, "iframe")
                iframe_caixinha = None
                iframe_desafio = None
                
                for f in frames:
                    src = f.get_attribute("src") or ""
                    name = f.get_attribute("name") or ""
                    if "anchor" in src or name.startswith("a-"): iframe_caixinha = f
                    elif "bframe" in src or name.startswith("c-"): iframe_desafio = f

                captcha_pendente = False
                captcha_ficou_verde = False

                if iframe_caixinha and iframe_caixinha.is_displayed():
                    captcha_pendente = True 
                    self.driver.switch_to.frame(iframe_caixinha)
                    try:
                        if self.driver.find_element(By.ID, "recaptcha-anchor").get_attribute("aria-checked") == "true":
                            captcha_pendente = False 
                            captcha_ficou_verde = True
                    except: pass
                    finally: self.driver.switch_to.default_content()

                if iframe_desafio and iframe_desafio.is_displayed():
                    captcha_pendente = True
                    captcha_ficou_verde = False

                if captcha_pendente:
                    if inicio_captcha is None:
                        inicio_captcha = time.time()
                    elif time.time() - inicio_captcha > 300: # 5 minutos de timeout
                        self.sio.emit('relatar_progresso_vale', {'mensagem': '❌ CAPTCHA EXPIRADO! (5 min sem solução). Fechando o robô.'})
                        raise Exception("CAPTCHA_TIMEOUT")
                        
                    elemento_alvo = iframe_desafio if (iframe_desafio and iframe_desafio.is_displayed()) else iframe_caixinha
                    if elemento_alvo:
                        tempo_atual = time.time()
                        if tempo_atual - ultimo_print > 2.0: # Manda print a cada 2 segundos e comprime
                            try:
                                png_bytes = elemento_alvo.screenshot_as_png
                                from PIL import Image
                                from io import BytesIO
                                import base64
                                img = Image.open(BytesIO(png_bytes))
                                buffer = BytesIO()
                                img.save(buffer, format="PNG")
                                png_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                                self.sio.emit('imagem_captcha_do_robo', {'imagem': png_b64})
                            except Exception as e:
                                pass
                            ultimo_print = tempo_atual

                        if self.aguardando_clique.is_set():
                            px = self.coordenadas_clique['x'] - (elemento_alvo.size['width'] / 2)
                            py = self.coordenadas_clique['y'] - (elemento_alvo.size['height'] / 2)
                            ActionChains(self.driver).move_to_element_with_offset(elemento_alvo, px, py).click().perform()
                            self.aguardando_clique.clear()
                    continue 

                if captcha_ficou_verde:
                    # MUDANÃ‡A: Adicionado o id='login_button' na varredura
                    botoes = self.driver.find_elements(By.XPATH, "//button[@type='submit' or @id='login-submit' or @id='login_button']")
                    for btn in botoes:
                        if btn.is_displayed() or self.driver.execute_script("return arguments[0].offsetWidth > 0;", btn):
                            self.driver.execute_script("arguments[0].click();", btn)
                            ultimo_clique_botao = time.time()
                            break
                    time.sleep(3.0) 
                    continue 

                # --- B. E-MAIL ---
                campos_email = self.driver.find_elements(By.XPATH, "//input[@type='email' or @id='email' or @name='email' or @id='username']")
                email_encontrado_e_vazio = False
                
                for campo in campos_email:
                    visivel = campo.is_displayed()
                    if not visivel: visivel = self.driver.execute_script("return (arguments[0].offsetWidth > 0 || arguments[0].offsetHeight > 0);", campo)
                    valor_atual = self.driver.execute_script("return arguments[0].value;", campo) or ""
                    
                    if visivel and valor_atual.strip() == "":
                        logger.info("ðŸ“§ Injetando E-mail...")
                        try: campo.clear(); campo.send_keys(email)
                        except: self.driver.execute_script("arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", campo, email)
                        email_encontrado_e_vazio = True
                        break
                        
                if email_encontrado_e_vazio:
                    # MUDANÃ‡A: Adicionado o id='login_button' na varredura
                    botoes = self.driver.find_elements(By.XPATH, "//button[@type='submit' or @id='login-submit' or @id='login_button']")
                    for btn in botoes:
                        if btn.is_displayed() or self.driver.execute_script("return arguments[0].offsetWidth > 0;", btn):
                            self.driver.execute_script("arguments[0].click();", btn)
                            ultimo_clique_botao = time.time()
                            break
                    time.sleep(3.0) 
                    continue 

                # --- C. SENHA ---
                self.driver.switch_to.default_content()
                campos_senha = self.driver.find_elements(By.XPATH, "//input[@type='password' or @id='password' or @name='password']")
                dentro_de_iframe = False
                
                if len(campos_senha) == 0:
                    iframes_gerais = self.driver.find_elements(By.TAG_NAME, "iframe")
                    for f in iframes_gerais:
                        try:
                            self.driver.switch_to.frame(f)
                            campos_senha = self.driver.find_elements(By.XPATH, "//input[@type='password' or @id='password' or @name='password']")
                            if len(campos_senha) > 0:
                                dentro_de_iframe = True; break
                            self.driver.switch_to.default_content()
                        except: self.driver.switch_to.default_content()

                senha_encontrada = False
                if len(campos_senha) > 0:
                    for campo in campos_senha:
                        visivel = campo.is_displayed()
                        if not visivel: visivel = self.driver.execute_script("return (arguments[0].offsetWidth > 0 || arguments[0].offsetHeight > 0);", campo)
                        valor_atual = self.driver.execute_script("return arguments[0].value;", campo) or ""

                        if visivel and valor_atual.strip() == "":
                            logger.info(" Injetando Senha...")
                            try: campo.clear(); campo.send_keys(senha)
                            except: self.driver.execute_script("var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set; if(setter) setter.call(arguments[0], arguments[1]); else arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", campo, senha)
                            senha_encontrada = True
                            break

                if senha_encontrada:
                    # MUDANÃ‡A: Adicionado o id='login_button' na varredura
                    botoes = self.driver.find_elements(By.XPATH, "//button[@type='submit' or @id='login-submit' or @id='login_button']")
                    if not botoes and dentro_de_iframe:
                        self.driver.switch_to.default_content()
                        botoes = self.driver.find_elements(By.XPATH, "//button[@type='submit' or @id='login-submit' or @id='login_button']")
                    
                    for btn in botoes:
                        if btn.is_displayed() or self.driver.execute_script("return arguments[0].offsetWidth > 0;", btn):
                            self.driver.execute_script("arguments[0].click();", btn)
                            ultimo_clique_botao = time.time()
                            break
                    self.driver.switch_to.default_content()
                    time.sleep(3.0) 
                    continue 

                if dentro_de_iframe: self.driver.switch_to.default_content()

                # --- D. BOTÃƒO AZUL TEIMOSO (A NOVA TRAVA DE SEGURANÃ‡A) ---
                # Se o bot preencheu tudo, mas a tela não avançou e o botão "Log in" está moscando lá:
                if not captcha_pendente and (time.time() - ultimo_clique_botao > 5):
                    botoes_esquecidos = self.driver.find_elements(By.XPATH, "//button[@type='submit' or @id='login-submit' or @id='login_button']")
                    for btn in botoes_esquecidos:
                        visivel = btn.is_displayed()
                        if not visivel: visivel = self.driver.execute_script("return (arguments[0].offsetWidth > 0);", btn)
                        if visivel:
                            logger.info(" Botão 'Log in' azul solto detectado na tela. Forçando clique...")
                            self.driver.execute_script("arguments[0].click();", btn)
                            ultimo_clique_botao = time.time()
                            time.sleep(3.0)
                            break

            except Exception: pass

    def garantir_logado(self, url_origem=None):
        try:
            email_vazio_na_tela = False
            
            # 1. Procura ativamente pelo campo de e-mail na tela atual
            campos_email = self.driver.find_elements(By.XPATH, "//input[@type='email' or @id='email' or @name='email' or @id='username']")
            for campo in campos_email:
                if campo.is_displayed():
                    valor_atual = self.driver.execute_script("return arguments[0].value;", campo) or ""
                    if valor_atual.strip() == "":
                        email_vazio_na_tela = True
                        break

            # Se não achou, tenta no default_content
            if not email_vazio_na_tela:
                try:
                    self.driver.switch_to.default_content()
                    campos_email = self.driver.find_elements(By.XPATH, "//input[@type='email' or @id='email' or @name='email' or @id='username']")
                    for campo in campos_email:
                        if campo.is_displayed():
                            valor_atual = self.driver.execute_script("return arguments[0].value;", campo) or ""
                            if valor_atual.strip() == "":
                                email_vazio_na_tela = True
                                break
                except: pass

            # 2. Recuperação de Sessão
            if email_vazio_na_tela or self.driver.find_elements(By.CSS_SELECTOR, "div.login_message") or "sessions/new" in self.driver.current_url or self._verificar_tela_erro():
                logger.warning(" Desconexão detectada! A tela de login apareceu de repente. Re-logando...")
                
                self.driver.switch_to.default_content()
                # Refaz o login
                self.fazer_login_hibrido()
                
                # --- NOVIDADE: REFAZ O AQUECIMENTO ANTES DE VOLTAR ---
                logger.info(" Refazendo aquecimento pós-desconexão...")
                self.navegacao_aquecimento()
                
                # Depois do aquecimento, volta para a aba que estava trabalhando antes de cair
                if url_origem: 
                    self.driver.get(url_origem)
                    time.sleep(3)
                    try: self.driver.switch_to.frame(self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe"))))
                    except: pass
        except Exception: 
            pass

    def navegacao_aquecimento(self):
        """Função de segurança para não crashar caso o bot Vale seja forçado a aquecer."""
        logger.info(" Aquecimento inicial de rota concluído.")
        time.sleep(2)

    def iniciar_e_logar(self):
        logger.info(" INICIANDO ROBÃ” (FASE 1: PREPARAÃ‡ÃƒO E LOGIN)...")
        self.verificar_acesso_pastas()
        self.carregar_banco_dados()
        try:
            self.setup_driver()
            if self.fazer_login_hibrido():
                logger.info("✅ Robo pronto para receber tarefas da nuvem.")
                self.is_ready = True
                # Avisa a Nuvem que o login acabou e libera os botÃµes na tela do site!
                self.sio.emit('tarefa_concluida', {'evento': 'Login do RobÃ´', 'sucesso': True})
        except Exception as e: 
            logger.error(f" Erro Crítico no Login: {e}")
            try:
                import traceback
                import sys, os
                sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                from gerenciador import enviar_email_erro
                enviar_email_erro(f"""Vale falhou na etapa de Login:
{e}

{traceback.format_exc()}""")
            except: pass
            self.sio.emit('tarefa_concluida', {'evento': 'Login do RobÃ´', 'sucesso': False, 'erro': str(e)})

    # ============================ 2. DROPDOWN VALE BASE METALS ============================
    def selecionar_cliente_vale_base_metals(self):
        logger.info("ðŸ¢ Selecionando o cliente 'Vale Base Metals' no dropdown...")
        try:
            input_cliente = self.wait.until(EC.presence_of_element_located((By.ID, "customersList_1")))
            self.driver.execute_script("arguments[0].click();", input_cliente)
            time.sleep(1)
            
            # Clica EXATAMENTE na opção da Vale Base Metals mapeada
            opcao_vale = self.wait.until(EC.presence_of_element_located((By.XPATH, "//li[contains(@class, 'ComboBox__resultItem') and text()='Vale Base Metals']")))
            self.driver.execute_script("arguments[0].click();", opcao_vale)
            time.sleep(3) 
            logger.info("   ✅ Cliente selecionado!")
        except Exception as e:
            logger.warning(f"    Cliente não encontrado ou já está selecionado.")

    # ============================ 3. VARREDURA (LISTA E PAGINAÃ‡ÃƒO) ============================
    def varrer_painel_eventos(self):
        logger.info("\n--- INICIANDO VARREDURA DE EVENTOS (VALE BASE METALS) ---")
        self.solicitacao_parada = False 
        
        self.relatorio_email_stats = {
            'total_cotacoes': 0,
            'sem_descricao': [],
            'faltando_info': [],
            'reabertas_ou_data_mudou': [],
            'eventos_sem_itens': [],
            'erros_planilha': [],
            'erros_extracao': [],
            'erro_critico_bot': None,
            'hora_inicio_extracao': time.time()
        }

        try:
            self.garantir_logado()
            
            # 1. Vai para Sourcing Privado
            self.driver.get(self.EVENTS_URL)
            time.sleep(4)
            
            # 2. Selecionar o Cliente Específico
            self.selecionar_cliente_vale_base_metals()
            
            # 3. Mudar para 90 itens por página
            logger.info("ðŸ”Ž Mudando para 90 resultados por página...")
            try:
                btn_90 = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[.//span[text()='90']]")))
                self.driver.execute_script("arguments[0].click();", btn_90)
                time.sleep(4)
            except:
                logger.warning(" Botão de 90 itens não encontrado. Prosseguindo com paginação padrão.")

            # 4. Iniciar Paginação
            pag = 1
            paginas_vazias_seguidas = 0
            while True:
                if self.solicitacao_parada: break
                logger.info(f"ðŸ“„ Analisando Página {pag}...")
                
                try: self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "tr.coupaTable__row")))
                except: logger.info("Nenhuma linha encontrada."); break
                    
                rows = self.driver.find_elements(By.CSS_SELECTOR, "tr.coupaTable__row")
                tarefas = []
                
                for r in rows:
                    try:
                        # Pega todas as colunas daquela linha
                        celulas = r.find_elements(By.CSS_SELECTOR, "td.coupaTable__cell")
                        if not celulas: continue
                        
                        # A primeira coluna tem o link e o número
                        a_tag = celulas[0].find_element(By.TAG_NAME, "a")
                        num = a_tag.text.strip()
                        url = a_tag.get_attribute('href')
                        
                        # A 6ª coluna (índice 5) tem a Data de Vencimento
                        data_venc_tabela = ""
                        try:
                            if len(celulas) >= 6:
                                data_venc_tabela = celulas[5].text.strip()
                        except: pass

                        re_extrair = False
                        if self.evento_ja_processado(num):
                            evento_salvo = self.db_eventos.get(num, {})
                            data_salva = evento_salvo.get("vencimento_tabela", "")
                            
                            if data_venc_tabela and data_salva and data_venc_tabela != data_salva:
                                logger.info(f"🔄 Evento {num} REABERTO! Data mudou de {data_salva} para {data_venc_tabela}.")
                                re_extrair = True
                            else:
                                continue 
                        
                        tarefas.append({
                            'num': num, 
                            'url': url, 
                            'venc_tabela': data_venc_tabela, 
                            're_extrair': re_extrair
                        })
                    except: continue
                
                logger.info(f"   📊 Encontrados {len(tarefas)} eventos a processar nesta página.")
                
                if not tarefas:
                    paginas_vazias_seguidas += 1
                else:
                    paginas_vazias_seguidas = 0
                    
                if paginas_vazias_seguidas >= 3:
                    logger.info("3 páginas seguidas sem novos eventos encontradas. Parando extração de forma segura...")
                    self.sio.emit('relatar_progresso_vale', {'mensagem': '🛑 3 páginas seguidas sem novos eventos. Encerrando paginação...'})
                    break

                self.sio.emit('relatar_progresso', {'mensagem': f"🔍 Página {pag}: {len(tarefas)} eventos a processar encontrados.", 'total': len(tarefas)})

                # Processar eventos da página atual
                for idx, t in enumerate(tarefas):
                    if self.solicitacao_parada: break
                    
                    self.sio.emit('relatar_progresso', {'mensagem': f"🎯 Iniciando extração do evento {t['num']}...", 'atual': idx + 1})

                    self.driver.execute_script(f"window.open('{t['url']}', '_blank');")
                    self.driver.switch_to.window(self.driver.window_handles[-1])
                    
                    res = self.extrair_dados_do_evento(t['url'], t['num'], re_extrair=t.get('re_extrair', False))
                    
                    if res is None:
                        self.relatorio_email_stats['erros_extracao'].append(t['num'])
                    
                    if res and res != "SKIPPED":
                        res['vencimento_tabela'] = t.get('venc_tabela', "")
                        self.dados_processados_lote.append(res)
                        
                        self.relatorio_email_stats['total_cotacoes'] += 1
                        if t.get('re_extrair'):
                            self.relatorio_email_stats['reabertas_ou_data_mudou'].append(t['num'])
                            
                        sem_desc = False
                        falt_info = False
                        for item in res.get('itens', []):
                            desc = str(item.get('descricao', '')).strip().lower()
                            if not desc or desc in ['nenhum', 'n/a']: sem_desc = True
                            loc = str(item.get('local_entrega', '')).strip()
                            if not loc or loc in ['n/a', 'N/A']: falt_info = True
                            
                        if res.get('data_vencimento', 'N/A') in ['N/A', '']: falt_info = True
                        if res.get('tipo_frete', 'N/A') in ['N/A', '']: falt_info = True
                        
                        if sem_desc: self.relatorio_email_stats['sem_descricao'].append(t['num'])
                        if falt_info: self.relatorio_email_stats['faltando_info'].append(t['num'])
                        if not res.get('itens'): self.relatorio_email_stats['eventos_sem_itens'].append(t['num'])

                    self.driver.close()
                    self.driver.switch_to.window(self.driver.window_handles[0])

                # Gera os ficheiros Word para a página que acabou de ser lida
                if self.dados_processados_lote:
                    self.gerar_word()

                if self.solicitacao_parada: break
                
                # 5. Ir para a próxima página
                pag += 1
                try:
                    # Busca o botão com o número da PRÃ“XIMA página
                    btn_next = self.driver.find_element(By.XPATH, f"//button[.//span[text()='{pag}']]")
                    
                    # Se ele estiver desativado ou for a página atual, terminamos.
                    if "disabled" in btn_next.get_attribute("class") or btn_next.get_attribute("currentitem") == "true":
                        logger.info("Fim das páginas alcançado.")
                        break
                        
                    logger.info(f" Avançando para a página {pag}...")
                    self.driver.execute_script("arguments[0].click();", btn_next)
                    time.sleep(4)
                except Exception as e:
                    logger.info("Botão da próxima página não encontrado. Fim da extração.")
                    break
                
            if self.dados_processados_lote: 
                self.gerar_word()
            
            logger.info("✅ Extração concluída!")
            self.enviar_relatorio_email()
            self.sio.emit('tarefa_concluida', {'evento': 'Lote de Extração', 'sucesso': True})

        except Exception as e:
            logger.error(f"❌ Erro Crítico na varredura: {e}")
            if hasattr(self, 'relatorio_email_stats'):
                self.relatorio_email_stats['erro_critico_bot'] = str(e)
                self.enviar_relatorio_email()
            self.sio.emit('tarefa_concluida', {'evento': 'Lote de Extração Vale', 'sucesso': False, 'erro': str(e)})

    def enviar_relatorio_email(self):
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        if getattr(self, 'relatorio_email_stats', None) is None:
            return

        remetente = self.config.get("SMTP_USER", os.environ.get("SMTP_USER", ""))
        senha = self.config.get("SMTP_PASSWORD", os.environ.get("SMTP_PASSWORD", ""))
        
        emails_destino_raw = self.config.get("emails_relatorio", os.environ.get("EMAILS_RELATORIO", ""))
        destinatarios = [e.strip() for e in emails_destino_raw.split(',') if e.strip()]
        
        if not destinatarios:
            logger.warning("Nenhum email de destino configurado em emails_relatorio.")
            return

        assunto = "Relatório de Cotações - Vale Bot"
        
        sem_desc_nums = ", ".join(self.relatorio_email_stats.get('sem_descricao', [])) if self.relatorio_email_stats.get('sem_descricao') else "Nenhuma"
        falt_info_nums = ", ".join(self.relatorio_email_stats.get('faltando_info', [])) if self.relatorio_email_stats.get('faltando_info') else "Nenhuma"
        reabertas_nums = ", ".join(self.relatorio_email_stats.get('reabertas_ou_data_mudou', [])) if self.relatorio_email_stats.get('reabertas_ou_data_mudou') else "Nenhuma"
        sem_itens_nums = ", ".join(self.relatorio_email_stats.get('eventos_sem_itens', [])) if self.relatorio_email_stats.get('eventos_sem_itens') else "Nenhuma"
        erros_plan_nums = ", ".join(self.relatorio_email_stats.get('erros_planilha', [])) if self.relatorio_email_stats.get('erros_planilha') else "Nenhuma"
        
        tempo_total = 0
        if 'hora_inicio_extracao' in self.relatorio_email_stats:
            tempo_total = int(time.time() - self.relatorio_email_stats['hora_inicio_extracao'])
            minutos, segundos = divmod(tempo_total, 60)
            tempo_str = f"{minutos}m {segundos}s"
        else:
            tempo_str = "Desconhecido"

        erros_ext_nums = ", ".join(self.relatorio_email_stats.get('erros_extracao', [])) if self.relatorio_email_stats.get('erros_extracao') else "Nenhuma"
        erro_critico = self.relatorio_email_stats.get('erro_critico_bot')

        vendedores_str = "\n".join([f"- {num}: {vend}" for num, vend in self.relatorio_email_stats.get('vendedores_atribuidos', {}).items()])
        if not vendedores_str: vendedores_str = "Nenhum vendedor atribuído nesta rodada."

        corpo = f"""Relatório de Extração de Cotações Finalizado

Tempo total de execução: {tempo_str}
Total de cotações extraídas: {self.relatorio_email_stats.get('total_cotacoes', 0)}

--- VENDEDORES SUGERIDOS PELO ROBÔ ---
{vendedores_str}

--- ERROS CRÍTICOS DO ROBÔ ---
{"Nenhum erro fatal ocorreu." if not erro_critico else f"⚠️ O robô encontrou uma falha grave e precisou ser interrompido:\n{erro_critico}"}

--- PROBLEMAS DE EXTRAÇÃO ---
Cotações em que o robô não conseguiu acessar ou extrair ({len(self.relatorio_email_stats.get('erros_extracao', []))}):
{erros_ext_nums}

Cotações sem itens detectados ({len(self.relatorio_email_stats.get('eventos_sem_itens', []))}):
{sem_itens_nums}

Cotações que falharam ao salvar na planilha ({len(self.relatorio_email_stats.get('erros_planilha', []))}):
{erros_plan_nums}

--- ALERTAS DE CONTEÚDO ---
Cotações sem descrição ({len(self.relatorio_email_stats.get('sem_descricao', []))}):
{sem_desc_nums}

Cotações com informações faltantes (ex: Vencimento, Local) ({len(self.relatorio_email_stats.get('faltando_info', []))}):
{falt_info_nums}

Cotações reabertas ou com mudança de data ({len(self.relatorio_email_stats.get('reabertas_ou_data_mudou', []))}):
{reabertas_nums}

Atenciosamente,
Vale Bot
"""
        try:
            msg = MIMEMultipart()
            msg['From'] = remetente
            msg['To'] = ", ".join(destinatarios)
            msg['Subject'] = assunto
            msg.attach(MIMEText(corpo, 'plain'))

            try:
                server = smtplib.SMTP_SSL('email-ssl.com.br', 465, timeout=10)
                server.login(remetente, senha)
                server.sendmail(remetente, destinatarios, msg.as_string())
                server.quit()
            except Exception as e1:
                logger.warning(f"Erro no SMTP_SSL: {e1}. Tentando SMTP com STARTTLS em smtp.locaweb.com.br...")
                server = smtplib.SMTP('smtp.locaweb.com.br', 587, timeout=10)
                server.starttls()
                server.login(remetente, senha)
                server.sendmail(remetente, destinatarios, msg.as_string())
                server.quit()
            logger.info(f"Email de relatório enviado com sucesso para {', '.join(destinatarios)}")
        except Exception as e:
            logger.error(f"Erro ao enviar email de relatório: {e}")

    # ============================ 4. EXTRAÃ‡ÃƒO DO EVENTO (Interno) ============================
    def _filter_desc(self, t):
        if "PT ||" in t:
            try:
                s = t.index("PT ||")
                ends = [x for x in [t.find("***", s), t.find("ES ||", s), t.find("EN ||", s)] if x != -1]
                return t[s:min(ends) if ends else len(t)].strip()
            except: pass
        return t
    
    def parse_local(self, t):
        """Filtra a string gigante da Vale e retorna apenas 'Cidade - UF'"""
        t = t.replace("Local de entrega:", "").strip()
        p = [x.strip() for x in t.split('-')]
        ufs = {"AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA","PB","PR","PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO"}
        
        # LÃª de trás para a frente procurando o Estado (UF)
        for i in range(len(p)-1, 0, -1):
            if p[i].upper().replace(".", "") in ufs:
                c = p[i-1]
                if c.upper() not in ["S/N", "S/N."]: 
                    return f"{c} - {p[i].upper().replace('.', '')}"
        return t
    
    def _verificar_tela_erro(self):
        """Verifica se o portal crashou na tela 'Oops! Algo inesperado aconteceu.'"""
        try:
            erros = self.driver.find_elements(By.XPATH, "//h1[@id='error-message' or contains(text(), 'Oops!')]")
            return len(erros) > 0
        except:
            return False

    def _obter_novo_link_evento(self, target_num):
        logger.info(f"🔍 Buscando novo link para o evento {target_num} no painel...")
        try:
            self.garantir_logado(self.EVENTS_URL)
            self.driver.get(self.EVENTS_URL)
            curr = 1
            while True:
                if curr > 1: self.driver.get(f"{self.EVENTS_URL}?page={curr}")
                
                try: WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "table")))
                except: return None
                
                rows = self.driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
                if not rows: return None
                
                for r in rows:
                    try:
                        num = r.find_element(By.CSS_SELECTOR, "a span.dt_open_link").text.strip()
                        if num == target_num:
                            return r.find_element(By.XPATH, ".//a").get_attribute('href')
                    except: continue
                    
                has_next = False
                try: 
                    if self.driver.find_element(By.CSS_SELECTOR, "a.next_page:not(.disabled)"): has_next = True
                except: pass
                
                if not has_next: return None
                curr += 1
        except Exception as e:
            logger.error(f"Erro ao buscar novo link: {e}")
            return None

    def extrair_dados_do_evento(self, url: str, num: str, ja_respondido: bool = False, is_negotiation: bool = False, re_extrair: bool = False):
        if self.evento_ja_processado(num) and not re_extrair: 
            logger.info(f"⏭️ Evento {num} já existe e a data não mudou. Pulando.")
            return "SKIPPED"
            
        msg_extra = " (REABERTO POR MUDANÇA DE DATA)" if re_extrair else ""
        logger.info(f"🎯 Extraindo Evento: {num}{msg_extra}")
        try: self.sio.emit('relatar_progresso', {'mensagem': f"🎯 Iniciando extração do evento {num}{msg_extra}..."})
        except: pass

        try:
            # ==========================================================
            # 1. LOOP ANTI-OOPS E CARREGAMENTO
            # ==========================================================
            sucesso_carregamento = False
            for tentativa in range(3):
                if tentativa > 0: logger.info(f"    Recarregando evento {num} (Tentativa {tentativa+1}/3)...")
                self.driver.get(url) 
                time.sleep(4)
                try: 
                    self.driver.switch_to.alert.accept()
                    time.sleep(1)
                except NoAlertPresentException: pass
                
                if self._verificar_tela_erro():
                    logger.warning(f" Tela 'Oops!' detectada. Tentando de novo...")
                    
                    self.fazer_login_hibrido() 
                    time.sleep(2) 
                    
                    novo_url = self._obter_novo_link_evento(num)
                    if novo_url:
                        logger.info(f"✅ Novo link encontrado para o evento {num}: {novo_url}")
                        url = novo_url
                    else:
                        logger.error(f"❌ Não foi possível encontrar um novo link para o evento {num}. Mantendo o antigo...")
                    
                    continue
                else:
                    sucesso_carregamento = True
                    break
                    
            if not sucesso_carregamento:
                logger.error(f" Erro persistente (Oops!) no evento {num}. Ignorando.")
                return "SKIPPED"

            # ==========================================================
            # 2. IFRAME DA VALE E ESPERA INTELIGENTE
            # ==========================================================
            try: 
                iframe = self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe")))
                self.driver.switch_to.frame(iframe)
                logger.info("    Iframe detectado e acessado.")
            except: pass

            try:
                logger.info("    Aguardando a interface carregar...")
                WebDriverWait(self.driver, 15).until(lambda d: 
                    d.find_elements(By.ID, "participation") or
                    d.find_elements(By.XPATH, "//a[contains(., 'VENTURA COMERCIO VAREJISTA')]") or
                    d.find_elements(By.XPATH, "//a[.//span[text()='Minhas respostas']]") or
                    d.find_elements(By.XPATH, "//a[.//span[text()='Minha resposta']]") or
                    d.find_elements(By.ID, "quote_response_submit") or
                    d.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine") or
                    d.find_elements(By.XPATH, "//a[contains(@class, 'blue rollover button') and contains(@href, 'response_id')]")
                )
            except Exception: pass

            if self.driver.find_elements(By.XPATH, "//h1[contains(text(), 'já terminou')]"):
                logger.warning("   âš ï¸ Evento encerrado."); self.driver.switch_to.default_content(); return "SKIPPED"

            req = "N/A"
            try: 
                titulo_h1 = self.driver.find_element(By.CSS_SELECTOR, "h1.sourcing-wrapper-title").text
                if "NEGOCIAÇÃO" in titulo_h1.upper(): 
                    is_negotiation = True
                match_req = re.search(r'(?i)req[^\d]*(\d+)', titulo_h1)
                if match_req:
                    req = match_req.group(1)
                    self.req = req
            except: pass

            vencimento = "N/A"
            frete = "EXW"

            # ==========================================================
            # 3. ACESSO DIRETO (PULANDO O 'PRETENDO' SE NÃƒO FOR NEGOCIAÃ‡ÃƒO)
            # ==========================================================
            entrou_nos_itens = False
            
            if not is_negotiation:
                xpath_botoes_acesso = [
                    "//a[contains(@class, 'blue rollover button') and contains(@href, 'response_id')]",
                    "//a[contains(., 'VENTURA COMERCIO VAREJISTA')]",
                    "//span[contains(text(), 'VENTURA COMERCIO VAREJISTA')]",
                    "//a[.//span[text()='Minhas respostas']]",
                    "//span[text()='Minhas respostas']",
                    "//a[.//span[text()='Minha resposta']]"
                ]
                
                for xpath in xpath_botoes_acesso:
                    botoes = self.driver.find_elements(By.XPATH, xpath)
                    for b in botoes:
                        if b.is_displayed() or self.driver.execute_script("return arguments[0].offsetWidth > 0;", b):
                            logger.info(f"   ðŸŽ¯ Resposta/Rascunho detectado! Pulando o 'Pretendo'.")
                            self.driver.execute_script("arguments[0].click();", b)
                            time.sleep(4)
                            entrou_nos_itens = True
                            break
                    if entrou_nos_itens: break
            else:
                logger.info("    Rodada de Negociação detectada! Forçando novo aceite...")

            # ==========================================================
            # 4. SE Ã‰ NEGOCIAÃ‡ÃƒO OU NÃƒO TEM RASCUNHO, FAZ O ACEITE NORMAL
            # ==========================================================
            if not entrou_nos_itens:
                try:
                    dropdowns = self.driver.find_elements(By.ID, "participation")
                    if dropdowns:
                        logger.info("    Aceitando evento (Pretendo -> Enviar)...")
                        try:
                            Select(dropdowns[0]).select_by_value("true")
                        except Exception:
                            self.driver.execute_script("arguments[0].value='true'; arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", dropdowns[0])
                        btn_enviar = self.driver.find_elements(By.CSS_SELECTOR, "button.submitIntend")
                        if btn_enviar:
                            self.driver.execute_script("arguments[0].click();", btn_enviar[0])
                            time.sleep(3)
                            try: self.driver.switch_to.alert.accept()
                            except NoAlertPresentException: pass
                except Exception: pass
                    
                # Clicar Inserir resposta
                try:
                    btn_inserir = self.driver.find_elements(By.ID, "quote_response_submit")
                    if btn_inserir and btn_inserir[0].is_displayed():
                        logger.info("    Clicando em Inserir resposta...")
                        self.driver.execute_script("arguments[0].click();", btn_inserir[0])
                        time.sleep(4)
                except Exception: pass

            # ==========================================================
            # 5. VERIFICAÃ‡ÃƒO DE SEGURANÃ‡A (0 ITENS) E RECUPERAÃ‡ÃƒO DE 2 TENTATIVAS
            # ==========================================================
            linhas = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")
            
            if len(linhas) == 0:
                logger.warning("   ZERO itens na tela. Iniciando Loop de Recuperação Rigoroso...")
                
                for tentativa_recuperacao in range(2):
                    logger.info(f"  Tentativa de Recuperação {tentativa_recuperacao + 1}/2...")
                    
                    time.sleep(4)
                    self.garantir_logado(url)
                    self.driver.switch_to.default_content()
                    try: self.driver.switch_to.frame(self.driver.find_element(By.TAG_NAME, "iframe"))
                    except: pass

                    # 5.1 Loop Antigo (Tentar botÃµes de acesso alternativo)
                    xpath_fallback = [
                        "//a[contains(@class, 'blue rollover button') and contains(@href, 'response_id')]",
                        "//a[contains(., 'VENTURA COMERCIO VAREJISTA')]",
                        "//span[contains(text(), 'VENTURA COMERCIO VAREJISTA')]",
                        "//a[.//span[text()='Minhas respostas']]",
                        "//span[text()='Minhas respostas']",
                        "//a[.//span[text()='Minha resposta']]",
                        "//a[.//span[text()='Itens']]"
                    ]
                    for xpath in xpath_fallback:
                        try:
                            botoes = self.driver.find_elements(By.XPATH, xpath)
                            if botoes and (botoes[0].is_displayed() or self.driver.execute_script("return arguments[0].offsetWidth > 0;", botoes[0])): 
                                logger.info("       Clicando em botão de acesso alternativo...")
                                self.driver.execute_script("arguments[0].click();", botoes[0])
                                time.sleep(4)
                                break
                        except: pass
                        
                    self.garantir_logado(url)
                    self.driver.switch_to.default_content()
                    try: self.driver.switch_to.frame(self.driver.find_element(By.TAG_NAME, "iframe"))
                    except: pass
                    linhas = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")
                    if len(linhas) > 0: break # Achou os itens, sucesso!

                    # 5.2 Se ainda não achou, FORÃ‡A O FLUXO COMPLETO DO PRETENDO (O pedido do usuário)
                    logger.info("       Ainda 0 itens. Forçando seleção manual de 'Pretendo' -> 'Enviar' -> 'Data' -> 'Inserir Resposta'...")
                    try:
                        dropdowns = self.driver.find_elements(By.ID, "participation")
                        if dropdowns:
                            logger.info("         ✅ Dropdown 'Pretendo' encontrado! Selecionando...")
                            try:
                                Select(dropdowns[0]).select_by_value("true")
                            except Exception:
                                self.driver.execute_script("arguments[0].value='true'; arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", dropdowns[0])
                            time.sleep(1)
                            
                            btn_enviar = self.driver.find_elements(By.CSS_SELECTOR, "button.submitIntend")
                            if btn_enviar:
                                logger.info("         ✅ Clicando em 'Enviar'...")
                                self.driver.execute_script("arguments[0].click();", btn_enviar[0])
                                time.sleep(3)
                                try: self.driver.switch_to.alert.accept()
                                except NoAlertPresentException: pass
                    except Exception as e: pass

                    # Pegar data RIGOROSAMENTE AGORA (como pedido)
                    try:
                        et = self.driver.find_elements(By.ID, "end_time")
                        if et:
                            mes = et[0].find_element(By.CLASS_NAME, "month").text.strip()
                            dia = et[0].find_element(By.CLASS_NAME, "date").text.strip()
                            mapa = {'Jan':1,'Fev':2,'Mar':3,'Abr':4,'Mai':5,'Jun':6,'Jul':7,'Ago':8,'Set':9,'Out':10,'Nov':11,'Dez':12}
                            m = mapa.get(mes.title(), 1)
                            y = datetime.now().year
                            if m < datetime.now().month: y += 1
                            hora = "14:30"
                            try: 
                                t = et[0].find_element(By.CSS_SELECTOR, "span.quotes_h1").text.strip()
                                if re.match(r'\d{1,2}:\d{2}', t): hora = t
                            except: pass
                            vencimento = f"{int(dia):02d}/{m:02d}/{y} - {hora}"
                            logger.info(f"        Vencimento copiado na recuperação: {vencimento}")
                    except: pass

                    # Clicar Inserir resposta RIGOROSAMENTE AGORA (como pedido)
                    try:
                        btn_inserir = self.driver.find_elements(By.ID, "quote_response_submit")
                        if btn_inserir and btn_inserir[0].is_displayed():
                            logger.info("         ðŸ“¥ Clicando em 'Inserir resposta'...")
                            self.driver.execute_script("arguments[0].click();", btn_inserir[0])
                            time.sleep(4)
                    except Exception: pass
                    
                    self.garantir_logado(url)
                    self.driver.switch_to.default_content()
                    try: self.driver.switch_to.frame(self.driver.find_element(By.TAG_NAME, "iframe"))
                    except: pass
                    linhas = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")
                    if len(linhas) > 0: break # Achou os itens após o Pretendo, sucesso!
            
            # ðŸš¨ TRAVA DE SEGURANÃ‡A FINAL ðŸš¨
            if len(linhas) == 0:
                logger.error(f"    FALHA: Evento {num} carregou com 0 itens! Cancelando extração.")
                self.relatorio_email_stats.setdefault('eventos_sem_itens', []).append(num)
                self.driver.switch_to.default_content()
                return "SKIPPED"

            logger.info(f"  {len(linhas)} itens encontrados no evento {num}.")
            
            # ==========================================================
            # 6. EXTRAÃ‡ÃƒO DOS ITENS (COM BLINDAGEM DE QTD E LOCAL)
            # ==========================================================
            itens = []
            tem_anexo_geral = False
            for i, l in enumerate(linhas):
                try:
                    btn_expandir = l.find_element(By.CSS_SELECTOR, "img.s-expandLines, div.s-expandSidebar")
                    ActionChains(self.driver).click(btn_expandir).perform()
                    time.sleep(0.3)
                except: pass
                
                l = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")[i]
                
                try:
                    if l.find_elements(By.CSS_SELECTOR, "li.attachment.attachmentFile.s-attachmentFile"):
                        tem_anexo_geral = True
                except: pass
                
                tit = "Item"; desc = ""; qtd = "0"; loc = "N/A"
                
                try: tit = l.find_element(By.CSS_SELECTOR, "div.s-description p.s-textField").text.strip()
                except: pass
                try: 
                    d = l.find_elements(By.CSS_SELECTOR, "p.s-textField")
                    if len(d) > 1: desc = self._filter_desc(d[1].text)
                except: pass
                
                if not desc.strip() or desc.strip().lower() == "nenhum":
                    try:
                        dt = l.find_element(By.XPATH, ".//dt[contains(text(), 'Texto de compra')]")
                        dd = dt.find_element(By.XPATH, "following-sibling::dd")
                        desc = self._filter_desc(dd.text)
                    except: pass

                if not desc.strip() or desc.strip().lower() == "nenhum":
                    try:
                        dt = l.find_element(By.XPATH, ".//dt[contains(text(), 'Item Text')]")
                        dd = dt.find_element(By.XPATH, "following-sibling::dd")
                        desc = self._filter_desc(dd.text)
                    except: pass

                if not desc.strip() or desc.strip().lower() == "nenhum":
                    try:
                        dt = l.find_element(By.XPATH, ".//dt[contains(text(), 'Material Purchase Text')]")
                        dd = dt.find_element(By.XPATH, "following-sibling::dd")
                        desc = self._filter_desc(dd.text)
                    except: pass
                
                try: 
                    elementos_unidade = l.find_elements(By.CSS_SELECTOR, "p.valueWithUnit")
                    for q in elementos_unidade:
                        texto_q = q.text.strip()
                        if "BRL" not in texto_q and "R$" not in texto_q and "USD" not in texto_q:
                            qtd = texto_q
                            break
                except: pass
                
                try:
                    elemento_local = l.find_element(By.XPATH, ".//*[contains(text(), 'Local de entrega:')]")
                    loc = self.parse_local(elemento_local.text)
                except: 
                    try: loc = self.parse_local(l.find_element(By.CSS_SELECTOR, "li.attachmentText").text)
                    except: pass
                
                if loc == "N/A" or not loc.strip():
                    try:
                        address_label = l.find_element(By.XPATH, ".//div[contains(@class, 's-fieldLabel') and contains(text(), 'Endereço de entrega')]")
                        address_box = address_label.find_element(By.XPATH, "following-sibling::div[contains(@class, 'addressLines')]")
                        
                        linhas_end = address_box.text.split('\n')
                        for linha in linhas_end:
                            if re.search(r'\d{5}-\d{3}', linha):
                                cidade_estado = re.sub(r'.*?\d{5}-\d{3}\s*', '', linha).strip()
                                if cidade_estado:
                                    loc = re.sub(r'\s+', ' ', cidade_estado)
                                    break
                        
                        if loc == "N/A" or not loc.strip():
                            loc = re.sub(r'\s+', ' ', address_box.text)
                    except: pass
                
                # Novas extrações solicitadas
                texto_item_extra = ""
                try:
                    dt_ti = l.find_element(By.XPATH, ".//dt[contains(text(), 'Texto do item') or contains(text(), 'Item Text')]")
                    dd_ti = dt_ti.find_element(By.XPATH, "following-sibling::dd")
                    txt_ti = dd_ti.text.strip()
                    if txt_ti.lower() not in ['nenhum', 'none', 'n/a', '']:
                        texto_item_extra = txt_ti
                except: pass

                texto_compra_extra = ""
                try:
                    dt_tc = l.find_element(By.XPATH, ".//dt[contains(text(), 'Texto de compra') or contains(text(), 'Material Purchase Text')]")
                    dd_tc = dt_tc.find_element(By.XPATH, "following-sibling::dd")
                    txt_tc = self._filter_desc(dd_tc.text)
                    if txt_tc.lower() not in ['nenhum', 'none', 'n/a', '']:
                        texto_compra_extra = txt_tc
                except: pass
                
                logger.info(f"       Item {i+1}: {tit[:20]}... | Qtd: {qtd} | Local: {loc[:30]}...")
                itens.append({"item_num": i+1, "titulo": tit, "descricao": desc, "quantidade": qtd, "local_entrega": loc, "texto_item": texto_item_extra, "texto_compra": texto_compra_extra})

            # ==========================================================
            # REFORÇO OBRIGATÓRIO DA DATA DE VENCIMENTO (Aba Informações)
            # ==========================================================
            logger.info("       🔍 Confirmando a data de vencimento exata na aba de Informações...")
            try:
                xpath_info = "//a[contains(@class, 'button') and .//span[contains(text(), 'Informa') and contains(text(), 'evento')]]"
                btn_info = self.driver.find_elements(By.XPATH, xpath_info)
                
                if btn_info:
                    self.driver.execute_script("arguments[0].click();", btn_info[0])
                    time.sleep(2) 

                div_end_time = self.driver.find_elements(By.CSS_SELECTOR, "div.end_time")
                
                if div_end_time:
                    data_attr = div_end_time[0].get_attribute("data-end-date")
                    if data_attr:
                        dt_obj = datetime.strptime(data_attr.split(' ')[0], "%Y-%m-%d")
                        hora_attr = data_attr.split(' ')[1][:5]
                        vencimento = f"{dt_obj.strftime('%d/%m/%Y')} - {hora_attr}"
                    else:
                        mes = div_end_time[0].find_element(By.CLASS_NAME, "month").text.strip()
                        dia = div_end_time[0].find_element(By.CLASS_NAME, "date").text.strip()
                        hora = div_end_time[0].find_element(By.CSS_SELECTOR, "span.quotes_h1").text.strip()
                        
                        mapa = {'Jan':1,'Fev':2,'Mar':3,'Abr':4,'Mai':5,'Jun':6,'Jul':7,'Ago':8,'Set':9,'Out':10,'Nov':11,'Dez':12}
                        m = mapa.get(mes.title()[:3], 1)
                        y = datetime.now().year
                        if m < datetime.now().month: y += 1 
                        
                        vencimento = f"{int(dia):02d}/{m:02d}/{y} - {hora}"
                        
                logger.info(f"       ✅ Data de vencimento confirmada pelo sistema: {vencimento}")
            except Exception as e:
                logger.warning(f"       ⚠️ Não foi possível forçar a aba de Informações: {e}. Mantendo data anterior.")

            try: frete = self.driver.find_element(By.CSS_SELECTOR, "p.s-selectField").text
            except: pass

            self.driver.switch_to.default_content()
            
            # Ajuste de Compatibilidade para retorno no Vale/Coupa
            if self.__class__.__name__ == "CoupaScraper":
                return {"numero_evento": num, "req_num": getattr(self, 'req', req), "data_vencimento": vencimento, "tipo_frete": frete, "itens": itens, "respondido_origem": ja_respondido, "tem_anexo": tem_anexo_geral}
            else:
                return {"numero_evento": num, "req_num": req, "data_vencimento": vencimento, "tipo_frete": frete, "itens": itens, "tem_anexo": tem_anexo_geral}
            
        except Exception as e:
            logger.error(f" Erro extração {num}: {e}")
            try: self.driver.switch_to.default_content()
            except: pass
            return None

    # ========================================================
    # 5. RESPONDER EVENTO (ATUALIZADO PARA BASE64 E NUVEM)
    # ========================================================
    def responder_evento(self, dados):
        evento = str(dados.get('evento', '')).strip()
        lista_precos = dados.get('precos', [])
        lista_prazos = dados.get('prazos', [])
        lista_origens = dados.get('origens', [])
        lista_icms = dados.get('icms', [])
        
        # --- DESCOMPACTAR OS ARQUIVOS BASE64 QUE VIERAM DA NUVEM ---
        ds_paths = []
        for f_data in dados.get('datasheets', []):
            # Cria o arquivo fisicamente na pasta de arquivos temporários do Windows
            caminho = os.path.join(tempfile.gettempdir(), f_data['name'])
            with open(caminho, 'wb') as out:
                out.write(base64.b64decode(f_data['data']))
            ds_paths.append(caminho)
            
        dav_paths = []
        for f_data in dados.get('davs', []):
            caminho = os.path.join(tempfile.gettempdir(), f_data['name'])
            with open(caminho, 'wb') as out:
                out.write(base64.b64decode(f_data['data']))
            dav_paths.append(caminho)
            
        logger.info(f"🚀 Iniciando Resposta do Evento {evento}...")
        
        try:
            # ========================================================
            # 1. ROTA DIRETA PARA SOURCING (À PROVA DE FALHAS)
            # ========================================================
            self.sio.emit('relatar_progresso', {'mensagem': f" Acessando diretamente a página de Sourcing..."})
            
            # Navega diretamente pela URL em vez de tentar clicar no menu!
            url_sourcing = f"{self.BASE_SUPPLIER_URL}/quotes/private_events"
            self.driver.get(url_sourcing)
            time.sleep(3)
            self.garantir_logado(url_sourcing)

            # ========================================================
            # 1.5 DIGITAR O CLIENTE (VALE BASE METAIS)
            # ========================================================
            logger.info(" Filtrando pelo cliente 'VALE BASE METAIS'...")
            self.sio.emit('relatar_progresso', {'mensagem': f" Filtrando eventos do cliente 'VALE BASE METAIS'..."})
            try:
                # Usa o campo de busca de clientes (ID customersList_1)
                input_cliente = self.wait.until(EC.presence_of_element_located((By.ID, "customersList_1")))
                input_cliente.clear()
                time.sleep(0.5)
                
                # 👇 A ÚNICA DIFERENÇA: A EMPRESA 👇
                input_cliente.send_keys("VALE BASE METAIS")
                
                time.sleep(1)
                input_cliente.send_keys(Keys.ENTER) # Pressiona Enter após digitar
                time.sleep(3) # Aguarda 3 segundos pro site recarregar a tabela
                logger.info("   ✅ Filtro 'VALE BASE METAIS' aplicado com sucesso!")
            except Exception as e:
                logger.warning(f"    Não foi possível digitar 'VALE BASE METAIS' no campo de cliente: {e}")

            # 2. Pesquisa o evento
            logger.info(" Buscando evento...")
            search = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input.s-toolbarSearch")))
            search.clear()
            search.send_keys(evento)
            search.send_keys(Keys.RETURN)
            time.sleep(4)

            # 3. Entra no evento
            logger.info(f"   🖱️ Procurando link do evento {evento} na tabela...")
            try:
                # 1. XPath ultra-preciso usando a classe e a estrutura HTML
                xpath_preciso = f"//td[contains(@class, 'coupaTable__cell')]//a[contains(text(), '{evento}') or contains(@href, '{evento}')]"
                link_evento = self.wait.until(EC.presence_of_element_located((By.XPATH, xpath_preciso)))
                
                # 2. Rola a página para garantir que o elemento está visível na tela
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link_evento)
                time.sleep(1)
                
                # 3. Clica no link
                self.driver.execute_script("arguments[0].click();", link_evento)
                logger.info("   ✅ Clique efetuado com sucesso!")
                time.sleep(3)
                
                # 4. TRATAMENTO CRÍTICO: Como o link tem target="_blank", ele abre uma NOVA ABA.
                if len(self.driver.window_handles) > 1:
                    self.driver.switch_to.window(self.driver.window_handles[-1])
                    logger.info("   🔄 Foco alterado para a aba do evento.")
                    
            except Exception as e:
                logger.warning(f"   ⚠️ Link não encontrado na tabela. Forçando acesso via URL (Nova Aba)... ({e})")
                self.driver.execute_script(f"window.open('{self.BASE_SUPPLIER_URL}/quotes/external_responses/{evento}/multi', '_blank');")
                time.sleep(3)
                self.driver.switch_to.window(self.driver.window_handles[-1])

            time.sleep(3)

            # --- PULAR PARA A NOVA ABA E MERGULHAR NO IFRAME ---
            self.driver.switch_to.window(self.driver.window_handles[-1])
            time.sleep(3) 
            
            try: 
                self.driver.switch_to.frame(self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe"))))
            except: 
                pass

            # 4. Clica em Minha Resposta (COM PRIORIDADE ESTRITA VENTURA -> MINHAS RESPOSTAS)
            logger.info(" Procurando botão de acesso ao evento...")
            try:
                time.sleep(2) # Pequena pausa para garantir que a página carregou
                
                # A ORDEM DEFINE A PRIORIDADE: Ele tenta o nÂ° 1 primeiro. Se falhar, vai para o 2.
                xpath_botoes = [
                    # =========================================================
                    # 1Âª PRIORIDADE: O seu Span/Link com o nome da empresa
                    # =========================================================
                    "//a[contains(., 'VENTURA COMERCIO VAREJISTA')]",
                    "//span[contains(text(), 'VENTURA COMERCIO VAREJISTA')]",
                    
                    # =========================================================
                    # 2Âª PRIORIDADE: O botão plural "Minhas respostas" (com /multi)
                    # =========================================================
                    "//a[.//span[text()='Minhas respostas']]",
                    "//span[text()='Minhas respostas']",
                    f"//a[contains(@href, '/quotes/external_responses/{evento}/multi')]",
                    
                    # =========================================================
                    # 3Âª PRIORIDADE (Rede de Segurança): Se for um evento 100% virgem
                    # =========================================================
                    "//a[.//span[text()='Minha resposta']]",
                    "//a[.//span[text()='Criar resposta']]",
                    "//span[text()='Itens']"
                ]
                
                botao_clicado = False
                for xpath in xpath_botoes:
                    botoes = self.driver.find_elements(By.XPATH, xpath)
                    for b in botoes:
                        if b.is_displayed() or self.driver.execute_script("return arguments[0].offsetWidth > 0;", b):
                            logger.info(f"    Alvo encontrado! Clicando no padrão: {xpath}")
                            # Usamos o clique via Javascript para furar qualquer bloqueio visual do site
                            self.driver.execute_script("arguments[0].click();", b)
                            botao_clicado = True
                            break
                    if botao_clicado: 
                        break # Paramos o loop instantaneamente se o botão for clicado
                
                # Se a tela bloqueou tudo porque ainda não aceitou os Termos de Participação
                if not botao_clicado:
                    logger.warning("    Botão direto não encontrado. Tentando Aceitar Termos de Participação primeiro...")
                    try:
                        Select(self.wait.until(EC.presence_of_element_located((By.ID, "participation")))).select_by_value("true")
                        self.driver.find_element(By.CSS_SELECTOR, "button.submitIntend").click()
                        time.sleep(3)
                        
                        self.driver.find_element(By.XPATH, "//a[.//span[text()='Itens']]").click()
                        logger.info("   ✅ Termos aceitos! Avançando para a resposta...")
                    except Exception as e:
                        raise Exception("A tela não tem botão de responder e não pediu aceite de termos. O evento pode estar encerrado ou indisponível.")

            except Exception as erro:
                raise Exception(f"Falha ao abrir a aba de preços: {str(erro)}")
                
            time.sleep(4)

            # 5. Anexar MÃšLTIPLOS arquivos
            logger.info(f" Injetando PDFs ({len(ds_paths)} Datasheets, {len(dav_paths)} DAVs)...")
            
            if ds_paths:
                for path in ds_paths:
                    try:
                        inputs_arquivo = self.driver.find_elements(By.CSS_SELECTOR, "input[type='file'][name='attachment[file]']")
                        if len(inputs_arquivo) > 0:
                            inputs_arquivo[0].send_keys(path)
                            time.sleep(2) 
                    except Exception as e:
                        logger.warning(f"âš ï¸ Erro ao anexar Datasheet ({path}): {e}")
            
            if dav_paths:
                for path in dav_paths:
                    try:
                        inputs_arquivo = self.driver.find_elements(By.CSS_SELECTOR, "input[type='file'][name='attachment[file]']")
                        if len(inputs_arquivo) > 1:
                            inputs_arquivo[1].send_keys(path)
                            time.sleep(2)
                    except Exception as e:
                        logger.warning(f"âš ï¸ Erro ao anexar DAV ({path}): {e}")
            
            time.sleep(5) 

            # 6. Salvar Rascunho
            logger.info(" Salvando Rascunho Inicial...")
            try:
                btn_rascunho = self.driver.find_element(By.CSS_SELECTOR, "button.s-menuSaveResponse")
                self.driver.execute_script("arguments[0].click();", btn_rascunho)
                time.sleep(5)
            except: pass

            # 7. Expandir e Preencher APENAS os itens informados
            logger.info(" Avaliando linhas e preenchendo itens específicos...")
            time.sleep(2)
            
            try:
                linhas_de_itens = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")
                logger.info(f"ðŸ“¦ O evento possui {len(linhas_de_itens)} linhas/itens no total.")
                
                for idx, linha in enumerate(linhas_de_itens):
                    se_tem_preco = idx < len(lista_precos) and str(lista_precos[idx]).strip() != ""
                    se_tem_prazo = idx < len(lista_prazos) and str(lista_prazos[idx]).strip() != ""
                    
                    if se_tem_preco or se_tem_prazo:
                        logger.info(f"    Processando Item {idx+1}...")
                        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", linha)
                        time.sleep(0.5)
                        
                        # --- 1. PRIMEIRO: EXPANDIR A LINHA PARA OS CAMPOS APARECEREM ---
                        try:
                            btn_expandir = linha.find_element(By.CSS_SELECTOR, "div.s-expandSidebar, img.s-expandLines")
                            self.driver.execute_script("arguments[0].click();", btn_expandir)
                            time.sleep(1.5) 
                        except: 
                            pass 
                        
                        # --- 2. SEGUNDO: APLICAR TODAS AS REGRAS DA VALE BASE METALS ---
                        try:
                            # Função interna para injetar com segurança no React
                            def preencher_cf(seletor, valor_fixo, nome_log):
                                cfs = linha.find_elements(By.CSS_SELECTOR, seletor)
                                if cfs:
                                    try:
                                        cfs[0].clear()
                                        cfs[0].send_keys(valor_fixo)
                                    except:
                                        self.driver.execute_script("""
                                            arguments[0].value = arguments[1];
                                            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                                            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                                        """, cfs[0], valor_fixo)
                                    logger.info(f"      ✅ {nome_log} preenchido com '{valor_fixo}'")

                            # Copiar NCM (Se existir)
                            ncm_element = linha.find_elements(By.CSS_SELECTOR, "div.s-classification_of_goods p.s-textField")
                            if ncm_element:
                                ncm_val = ncm_element[0].text.strip()
                                ncm_input = linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_8")
                                if ncm_input:
                                    ncm_input[0].clear()
                                    ncm_input[0].send_keys(ncm_val)
                                    time.sleep(1)
                                    ncm_input[0].send_keys(Keys.ARROW_DOWN)
                                    time.sleep(0.2)
                                    ncm_input[0].send_keys(Keys.ENTER)
                                    logger.info(f"      ✅ NCM copiado e injetado: {ncm_val}")

                            # Preenchimentos Fixos
                            preencher_cf("input.s-custom_field_7", "3", "Campo 7")
                            preencher_cf("input.s-custom_field_2", "0", "Campo 2")
                            preencher_cf("input.s-custom_field_6", "0,65", "Campo 6")
                            preencher_cf("input.s-custom_field_5", "0", "Campo 5")

                            # Preencher ICMS (Campo 3) se o utilizador informou
                            icms_atual = str(lista_icms[idx]).strip() if idx < len(lista_icms) else ""
                            if icms_atual:
                                preencher_cf("input.s-custom_field_3", icms_atual, "ICMS (%)")

                            # Preencher Origem (Campo 9 ComboBox) se o utilizador informou
                            origem_atual = str(lista_origens[idx]).strip() if idx < len(lista_origens) else ""
                            if origem_atual:
                                cfs_origem = linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_9")
                                if cfs_origem:
                                    cfs_origem[0].clear()
                                    cfs_origem[0].send_keys(origem_atual)
                                    time.sleep(1)
                                    cfs_origem[0].send_keys(Keys.ARROW_DOWN)
                                    time.sleep(0.2)
                                    cfs_origem[0].send_keys(Keys.ARROW_UP)
                                    time.sleep(0.2)
                                    cfs_origem[0].send_keys(Keys.ENTER)
                                    logger.info(f"      ✅ Origem preenchida: {origem_atual}")

                        except Exception as e:
                            logger.warning(f"       Erro ao processar formulário extra da Vale: {e}")

                        # --- 3. TERCEIRO: PREENCHER PREÃ‡O E PRAZO NORMALMENTE ---
                        if se_tem_preco:
                            preco_atual = str(lista_precos[idx]).strip()
                            try:
                                input_preco = linha.find_element(By.CSS_SELECTOR, "input.s-price_amount_input")
                                try:
                                    input_preco.clear()
                                    input_preco.send_keys(preco_atual)
                                except:
                                    self.driver.execute_script("""
                                        arguments[0].value = arguments[1];
                                        arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                                        arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                                    """, input_preco, preco_atual)
                                logger.info(f"      ✅ Preço {preco_atual} inserido.")
                            except Exception as e:
                                logger.warning(f"       Falha ao inserir preço: {e}")

                        if se_tem_prazo:
                            prazo_atual = str(lista_prazos[idx]).strip()
                            try:
                                input_prazo = linha.find_element(By.CSS_SELECTOR, "input.s-lead_time_input")
                                try:
                                    ActionChains(self.driver) \
                                        .move_to_element(input_prazo) \
                                        .click() \
                                        .pause(0.2) \
                                        .key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL) \
                                        .send_keys(Keys.BACKSPACE) \
                                        .send_keys(prazo_atual) \
                                        .send_keys(Keys.TAB) \
                                        .perform()
                                    
                                    time.sleep(0.2)
                                    if not input_prazo.get_attribute('value') or prazo_atual not in input_prazo.get_attribute('value'): raise Exception("Reset")
                                except:
                                    self.driver.execute_script("""
                                        let el = arguments[0];
                                        let val = arguments[1];
                                        let lastValue = el.value;
                                        el.value = val;
                                        let event = new Event('input', { bubbles: true });
                                        event.simulated = true;
                                        if (el._valueTracker) { el._valueTracker.setValue(lastValue); }
                                        el.dispatchEvent(event);
                                        el.dispatchEvent(new Event('change', { bubbles: true }));
                                        el.dispatchEvent(new Event('blur', { bubbles: true }));
                                    """, input_prazo, prazo_atual)
                                logger.info(f"      ✅ Prazo {prazo_atual} inserido.")
                            except Exception as e:
                                logger.warning(f"       Falha ao inserir prazo: {e}")
                    else:
                        logger.info(f"    Item {idx+1} ignorado (Você deixou os campos vazios).")
                        
            except Exception as e:
                logger.error(f" Erro crítico no preenchimento das linhas: {e}")

            # 8. Salvar Final
            logger.info("✅ Finalizando salvamento do evento...")
            try:
                btn_salvar = self.driver.find_element(By.CSS_SELECTOR, "button.s-save")
                self.driver.execute_script("arguments[0].click();", btn_salvar)
                time.sleep(4)
            except: pass

            logger.info(f"🎉 Evento {evento} respondido com sucesso!")
            self.sio.emit('tarefa_concluida', {'evento': f'Evento {evento}', 'sucesso': True})
            
            # --- FECHAR A ABA E VOLTAR PARA A PRINCIPAL ---
            self.driver.close()
            self.driver.switch_to.window(self.driver.window_handles[0])
            
        except Exception as e:
            logger.error(f" Erro ao responder evento: {e}")
            self.sio.emit('tarefa_concluida', {'evento': f'Evento {evento}', 'sucesso': False, 'erro': str(e)})
            
            # Se der erro, garante que fecha a aba extra e volta pra principal pra não travar tudo
            if len(self.driver.window_handles) > 1:
                self.driver.close()
                self.driver.switch_to.window(self.driver.window_handles[0])

    # ============================ 6. GERAÃ‡ÃƒO DE WORD / EXCEL ============================
    def gerar_word(self):
        if not self.dados_processados_lote: return
        logger.info("\n GERANDO DOCUMENTOS WORD (Agrupados por Data e Complexidade)...")
        
        # PRE-PROCESSAMENTO: Extrair IA e buscar histórico
        for ev in self.dados_processados_lote:
            vendedores_deste_evento = set()
            for it in ev.get('itens', []):
                descricoes_unicas = []
                for k in ['descricao', 'texto_item', 'texto_compra']:
                    v = str(it.get(k, '')).strip()
                    if v and v.lower() not in [x.lower() for x in descricoes_unicas]:
                        descricoes_unicas.append(v)
                it['descricoes_unicas'] = descricoes_unicas
                txt_completo = f"{it.get('titulo','')} {' '.join(descricoes_unicas)} {it.get('part_number','')}"
                dados_ia = self.extrair_dados_gemini(txt_completo)
                it['dados_ia'] = dados_ia
                match_hist = self.encontrar_vendedor_similar(it, dados_ia)
                if match_hist:
                    it['historico_match'] = match_hist
                    import re
                    m = re.search(r'Ultimo Vendedor:\s*([^\(]+)', match_hist)
                    if m: vendedores_deste_evento.add(m.group(1).strip())
            
            if not hasattr(self, 'relatorio_email_stats'): self.relatorio_email_stats = {}
            if 'vendedores_atribuidos' not in self.relatorio_email_stats: self.relatorio_email_stats['vendedores_atribuidos'] = {}
            
            num = ev.get('numero_evento', 'Desconhecido')
            if vendedores_deste_evento:
                self.relatorio_email_stats['vendedores_atribuidos'][num] = ", ".join(vendedores_deste_evento)
            else:
                self.relatorio_email_stats['vendedores_atribuidos'][num] = "Nenhum"

        
        marcas_bloq = []
        try: marcas_bloq = [str(x).strip().lower() for x in pd.read_excel(self.ARQUIVO_MARCAS_BLOQUEADAS).iloc[:,0] if pd.notna(x)]
        except: pass
        
        bloqs = []
        complexos = []
        agrupados_por_data = {}
        
        for d in self.dados_processados_lote:
            # 1. Verifica marcas bloqueadas
            todos_itens_bloqueados = True
            marca_ultima = ""
            
            for it in d['itens']:
                txt = f"{it.get('titulo', '')} {it.get('descricao', '')}".lower()
                item_tem_bloqueio = False
                for m in marcas_bloq:
                    if m and len(m) > 1 and re.search(r'\b'+re.escape(m)+r'\b', txt): 
                        item_tem_bloqueio = True
                        marca_ultima = m.upper()
                        break
                
                if not item_tem_bloqueio:
                    todos_itens_bloqueados = False
                    break
            
            if todos_itens_bloqueados and len(d['itens']) > 0 and marca_ultima:
                d['marca_bloqueada'] = marca_ultima
                bloqs.append(d)
                continue
                
            # 2. Verifica complexidade (Mais de 4 itens OU mais de 120 palavras)
            muitos_itens = len(d['itens']) > 4
            desc_longa = False
            for it in d['itens']:
                # Conta as palavras separadas por espaço
                if len(str(it['descricao']).split()) > 250:
                    desc_longa = True
                    break
            
            if muitos_itens or desc_longa:
                complexos.append(d) # Vai ser salvo sozinho
            else:
                # 3. Agrupa por Data de Vencimento
                data_venc = str(d.get('data_vencimento', 'N/A')).split(' - ')[0].strip()
                if data_venc not in agrupados_por_data:
                    agrupados_por_data[data_venc] = []
                agrupados_por_data[data_venc].append(d)
        
        # --- SALVAR ARQUIVOS WORD ---
        validos_para_excel = []
        
        # Complexos: Salva 1 por 1 em arquivos isolados
        if complexos:
            for c in complexos: 
                self.salvar_doc([c], self.PASTA_RELATORIOS, "COMPLEXO")
                
        # Bloqueados: Agrupa e salva na pasta de bloqueio
        if bloqs: 
            self.salvar_doc(bloqs, self.PASTA_BLOQUEADOS, "BLOQUEADO")

        # PadrÃµes: Salva agrupado por data, no máximo 5 por arquivo
        for data, eventos in agrupados_por_data.items():
            data_limpa = data.replace('/', '-') # Troca a / por - para o Windows não dar erro no nome do arquivo
            
            # Organizar também por descrição, para que eventos com a mesma descrição fiquem juntos
            eventos.sort(key=lambda ev: ev.get('itens', [{}])[0].get('descricao', '').strip().lower() if ev.get('itens') else '')
            
            # Fatiar a lista de eventos de 5 em 5
            for i in range(0, len(eventos), 5):
                lote_5 = eventos[i:i+5]
                self.salvar_doc(lote_5, self.PASTA_RELATORIOS, f"Vencimento_{data_limpa}_Eventos")
                validos_para_excel.extend(lote_5)

        # --- ATUALIZAR EXCEL E BANCO ---
        todos_para_excel = validos_para_excel + complexos
        sucesso_excel = True
        if todos_para_excel: 
            sucesso_excel = self.atualizar_excel(todos_para_excel)
        
        if sucesso_excel:
            for d in self.dados_processados_lote:
                try:
                    venc_tab = d.get('vencimento_tabela', "")
                    novo = {
                        "titulo": d['numero_evento'], 
                        "data": str(datetime.now().date()), 
                        "respondido": True, 
                        "vencimento_tabela": venc_tab
                    }
                    with open(self.ARQUIVO_JSONL, 'a', encoding='utf-8') as f: 
                        f.write(json.dumps(novo) + '\n')
                    self.db_eventos[d['numero_evento']] = novo
                except: pass
            
        self.dados_processados_lote.clear()
        logger.info("✅ Lote de Word finalizado.")

    def salvar_doc(self, lista, pasta, prefixo):
        if not lista: return
        try:
            nome = f"{prefixo}_{'_'.join([x['numero_evento'] for x in lista])}.docx"
            doc = Document()

            for section in doc.sections:
                section.top_margin = Cm(1.0)
                section.bottom_margin = Cm(1.0)
                section.left_margin = Cm(1.0)
                section.right_margin = Cm(1.0)

            style = doc.styles['Normal']
            style.font.name = 'Arial'
            style.font.size = Pt(9)
            style.paragraph_format.space_after = Pt(0) 
            style.paragraph_format.line_spacing = 1.0  

            for i, ev in enumerate(lista):
                if i > 0: 
                    p_linha = doc.add_paragraph("_" * 100)
                    p_linha.paragraph_format.space_before = Pt(10)
                
                if ev.get('marca_bloqueada'): 
                    r = doc.add_paragraph().add_run(f"âš ï¸ BLOQUEADO: {ev['marca_bloqueada']}")
                    r.bold = True; r.font.color.rgb = RGBColor(255,0,0)
                
                p_cabecalho = doc.add_paragraph()
                texto_evento = f"BASE METAIS | REQ: {ev['req_num']} | EVENTO: {ev['numero_evento']}"
                if ev.get('tem_anexo'):
                    texto_evento += " *contem anexo*"
                texto_evento += "\t"
                p_cabecalho.add_run(texto_evento).bold = True
                p_cabecalho.add_run("VENCIMENTO: ").bold = True
                p_cabecalho.add_run(f"{ev['data_vencimento']}").bold = True
                
                doc.add_paragraph("RESPONDIDO POR: ______________ CONFERIDO POR: ______________ PRECIFICADO POR:_____________")
                
                p_frete = doc.add_paragraph()
                p_frete.add_run("FRETE: ").bold = True
                p_frete.add_run(f" {ev['tipo_frete']}")

                for item in ev['itens']:
                    p_titulo = doc.add_paragraph()
                    if item['titulo'] != 'Não':
                        p_titulo.add_run(f"ITEM {item['item_num']} (Título): ").bold = True
                        p_titulo.add_run(f" {item['titulo']}")
                        
                        p_desc = doc.add_paragraph()
                        p_desc.add_run("DESCRIÃ‡ÃƒO:\n").bold = True
                        p_desc.add_run(f"{item['descricao']}")
                    else:
                        p_titulo.add_run(f"ITEM {item['item_num']}: ").bold = True
                        p_titulo.add_run(f" {item['descricao']}")
                    
                    import re
                    def _normalize(t):
                        return str(t).strip().lower() if t else ""

                    desc_norm = _normalize(item.get('descricao', ''))
                    ti_norm = _normalize(item.get('texto_item', ''))
                    tc_norm = _normalize(item.get('texto_compra', ''))

                    if item.get('texto_item') and ti_norm != desc_norm:
                        p_texto_item = doc.add_paragraph()
                        p_texto_item.add_run("TEXTO DO ITEM:\n").bold = True
                        p_texto_item.add_run(f"{item['texto_item']}")
                        
                    if item.get('texto_compra') and tc_norm != desc_norm and tc_norm != ti_norm:
                        p_texto_compra = doc.add_paragraph()
                        p_texto_compra.add_run("TEXTO DE COMPRA DO MATERIAL:\n").bold = True
                        p_texto_compra.add_run(f"{item['texto_compra']}")
                    p_qtd = doc.add_paragraph()
                    p_qtd.add_run("QTD: ").bold = True
                    p_qtd.add_run(f" {item['quantidade']} - LOCAL: {item['local_entrega']}")
                    
                    doc.add_paragraph("VALOR DE VENDA: R$ __________________ CUSTO UNIT: R$__________________")
                    
                    if item.get('historico_match'):
                        p_hist = doc.add_paragraph()
                        p_hist.add_run(item['historico_match']).bold = True
            
            doc.save(os.path.join(pasta, nome))
            logger.info(f"   Doc Salvo Compactado: {nome}")
        except Exception as e: 
            logger.error(f"Erro no Word: {e}")
    
    def extrair_dados_groq(self, texto_item):
        """Motor Reserva: Usa a Groq (Llama 3) quando o Gemini atinge o limite."""
        GROQ_API_KEY = os.environ.get("GROQ_API_KEY_VALE") 
        try:
            client = Groq(api_key=GROQ_API_KEY)
            prompt = f"""
            Você é um assistente especialista em compras corporativas e automação de suprimentos.
            Sua tarefa é analisar a descrição do item abaixo e extrair 3 informações exatas:
            1. Marca (Fabricante)
            2. Modelo
            3. Part Number (PN / Referência)

            Regras:
            - Retorne ESTRITAMENTE no formato JSON.
            - Se não encontrar a informação, deixe o valor como uma string vazia "".
            - Não invente dados. Use apenas o que está no texto.
            
            Formato de Saída:
            {{"marca": "NOME_DA_MARCA", "modelo": "NOME_DO_MODELO", "part_number": "CODIGO_PN"}}
            
            Texto para analisar:
            {texto_item}
            """
            response = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "Você é uma API que retorna apenas JSON válido."},
                    {"role": "user", "content": prompt}
                ],
                model="openai/gpt-oss-120b",
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            dados = json.loads(response.choices[0].message.content)
            return {
                "marca": str(dados.get("marca", "")).strip().upper(),
                "modelo": str(dados.get("modelo", "")).strip().upper(),
                "part_number": str(dados.get("part_number", "")).strip().upper()
            }
        except Exception as e:
            logger.warning(f"   ⚠️ Falha na extração reserva com GROQ: {e}")
            return {"marca": "", "modelo": "", "part_number": ""}


    def carregar_historico(self):
        if hasattr(self, 'historico_vendedores_cache') and self.historico_vendedores_cache is not None:
            return self.historico_vendedores_cache
        
        self.historico_vendedores_cache = []
        json_path = getattr(self, 'PASTA_DATABASE', getattr(self, 'config', {}).get('pasta_database', r'\\SERVIDOR2\Publico\ALLAN\database\Banco-de-dados'))
        
        if isinstance(json_path, dict) or not isinstance(json_path, str):
            json_path = r'\\SERVIDOR2\Publico\ALLAN\database\Banco-de-dados'
            
        json_file = os.path.join(json_path, 'COTAÇÕES.json')
        
        if not os.path.exists(json_file):
            logger.warning(f"   ⚠️ Arquivo COTAÇÕES.json não encontrado em {json_file}")
            return []
            
        try:
            import json
            with open(json_file, 'r', encoding='utf-8') as f:
                dados_completos = json.load(f)
                
            if 'COTAÇÃO' in dados_completos:
                registros = dados_completos['COTAÇÃO']
            else:
                # Caso a estrutura mude ou seja um array direto
                registros = dados_completos if isinstance(dados_completos, list) else []
                
            for row in registros:
                # O JSON possui as chaves: COTAÇÃO, VENCIMENTO, ITEM, QUANTIDADE, LOCALIDADE, VENDEDOR, MODELOS, MARCAS, RESPOSTA
                # A chave 'MARCAS ' às vezes tem espaço no final
                
                data = str(row.get('COTAÇÃO', '')) + " " + str(row.get('VENCIMENTO', ''))
                desc = str(row.get('ITEM', '')).strip()
                vend = str(row.get('VENDEDOR', '')).strip()
                mod = str(row.get('MODELOS', '')).strip().upper()
                marc = str(row.get('MARCAS ', row.get('MARCAS', ''))).strip().upper()
                resposta = str(row.get('RESPOSTA', '')).strip().upper()
                
                if vend and desc and vend.lower() not in ["none", "", "nan"] and resposta == "RESPONDIDO":
                    self.historico_vendedores_cache.append({
                        'data': data.strip(),
                        'descricao': desc,
                        'vendedor': vend,
                        'modelo': mod,
                        'marca': marc
                    })
            logger.info(f"   ✅ Carregados {len(self.historico_vendedores_cache)} registros históricos do COTAÇÕES.json com vendedor atribuído.")
        except Exception as e:
            logger.error(f"   ❌ Erro ao carregar histórico do COTAÇÕES.json: {e}")
        
        return self.historico_vendedores_cache


    def encontrar_vendedor_similar(self, item_dict, dados_ia):
        historico = self.carregar_historico()
        if not historico: return None
        
        import difflib
        import re
        modelo_atual = dados_ia.get("modelo", "").strip().upper()
        marca_atual = dados_ia.get("marca", "").strip().upper()
        part_number = dados_ia.get("part_number", "").strip().upper()
        
        if not modelo_atual and part_number:
            modelo_atual = part_number
            
        texto_item_list = []
        descricao_list = []
        texto_compra_list = []
        
        if isinstance(item_dict, dict):
            if item_dict.get('texto_item') and str(item_dict['texto_item']).strip():
                texto_item_list.append(str(item_dict['texto_item']).lower())
            if item_dict.get('descricao') and str(item_dict['descricao']).strip():
                descricao_list.append(str(item_dict['descricao']).lower())
            if item_dict.get('texto_compra') and str(item_dict['texto_compra']).strip():
                texto_compra_list.append(str(item_dict['texto_compra']).lower())
        else:
            texto_item_list.append(str(item_dict).lower())
        
        logger.info(f"   ?? Buscando vendedor para Modelo: '{modelo_atual}' | Marca: '{marca_atual}'")
        
        # ETAPA 1: Modelo e Marca
        for row in reversed(historico):
            row_mod = row['modelo'].strip().upper()
            row_marc = row['marca'].strip().upper()
            
            if modelo_atual and len(modelo_atual) > 2 and row_mod and modelo_atual in row_mod:
                logger.info(f"   ? Vendedor encontrado! [Modelo exato] -> {row['vendedor']}")
                return f"Ultimo Vendedor: {row['vendedor']} ({row['data']}) - Motivo: Modelo exato"
            
            elif marca_atual and len(marca_atual) > 2 and row_marc and marca_atual in row_marc:
                logger.info(f"   ? Vendedor encontrado! [Marca exata] -> {row['vendedor']}")
                return f"Ultimo Vendedor: {row['vendedor']} ({row['data']}) - Motivo: Marca exata"

        def buscar_por_similaridade(lista_alvos, nome_etapa):
            if not lista_alvos: return None
            for row in reversed(historico):
                desc_lines = row['descricao'].split('\n')
                for line in desc_lines:
                    line_clean = re.sub(r'^\(Item \d+\)\s*', '', line).strip().lower()
                    if not line_clean or len(line_clean) < 5: continue
                    
                    for desc_alvo in lista_alvos:
                        t_maior, t_menor = (desc_alvo, line_clean) if len(desc_alvo) > len(line_clean) else (line_clean, desc_alvo)
                        
                        if len(t_maior) == 0 or (len(t_menor) / len(t_maior)) < 0.8:
                            continue
                            
                        matcher = difflib.SequenceMatcher(None, t_maior, t_menor)
                        if matcher.real_quick_ratio() < 0.85: continue
                        if matcher.quick_ratio() < 0.85: continue
                        
                        ratio = matcher.ratio()
                        if ratio >= 0.85:
                            logger.info(f"   ? Vendedor encontrado! [Similaridade alta em {nome_etapa} ({ratio*100:.1f}%)] -> {row['vendedor']}")
                            return f"Ultimo Vendedor: {row['vendedor']} ({row['data']}) - Motivo: Similaridade em {nome_etapa} ({ratio*100:.1f}%)"
            return None

        # ETAPA 2: Texto do Item
        res = buscar_por_similaridade(texto_item_list, "Texto do Item")
        if res: return res
        
        # ETAPA 3: Descricao
        res = buscar_por_similaridade(descricao_list, "Descricao")
        if res: return res
        
        # ETAPA 4: Texto de Compra
        res = buscar_por_similaridade(texto_compra_list, "Texto de Compra")
        if res: return res
        
        logger.info(f"   ? Nenhum vendedor similar encontrado no historico para este item.")
        return None

    def extrair_dados_gemini(self, texto_item):
        """Motor Principal: Usa o Gemini. Se der limite (429), joga pra Groq no resto do dia."""
        hoje = datetime.now().date()
        
        # 1. Verifica se já falhou hoje. Se sim, desvia direto para a Groq
        if self.usar_groq_hoje and self.dia_falha_gemini == hoje:
            return self.extrair_dados_groq(texto_item)
        elif self.dia_falha_gemini and self.dia_falha_gemini != hoje:
            # Virou o dia! Dá uma nova chance ao Gemini
            self.usar_groq_hoje = False
            self.dia_falha_gemini = None

        GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY") 
        client = genai.Client(
            api_key=GOOGLE_API_KEY,
            http_options={'retry_options': {'attempts': 1}}
        )
        
        try:
            prompt = f"""
            Você é um assistente especialista em compras corporativas e automação de suprimentos.
            Sua tarefa é analisar a descrição do item abaixo e extrair 3 informações exatas:
            1. Marca (Fabricante)
            2. Modelo
            3. Part Number (PN / Referência)

            Regras:
            - Retorne ESTRITAMENTE no formato JSON.
            - Se não encontrar a informação, deixe o valor como uma string vazia "".
            
            Formato de Saída:
            {{"marca": "NOME_DA_MARCA", "modelo": "NOME_DO_MODELO", "part_number": "CODIGO_PN"}}
            
            Texto para analisar:
            {texto_item}
            """
            response = client.models.generate_content(
                model="gemini-2.5-flash", 
                contents=prompt,
                config={
                    "temperature": 0.0,
                    "response_mime_type": "application/json",
                    "automatic_function_calling": {"disable": True}
                }
            )
            dados = json.loads(response.text)
            return {
                "marca": str(dados.get("marca", "")).strip().upper(),
                "modelo": str(dados.get("modelo", "")).strip().upper(),
                "part_number": str(dados.get("part_number", "")).strip().upper()
            }
            
        except Exception as e:
            erro_str = str(e).upper()
            # 2. O DESVIO: Se der o erro 429 ou RESOURCE_EXHAUSTED, ativa a Groq
            if "429" in erro_str or "RESOURCE_EXHAUSTED" in erro_str:
                logger.warning("   🔄 Limite diário do Gemini atingido (429). Alternando para GROQ pelo resto do dia!")
                self.usar_groq_hoje = True
                self.dia_falha_gemini = hoje
                return self.extrair_dados_groq(texto_item)
            else:
                logger.warning(f"   ⚠️ Falha na extração com Gemini: {e}")
                return {"marca": "", "modelo": "", "part_number": ""}

    def atualizar_excel(self, dados):
        if not dados: return
        
        try:
            self.sio.emit('relatar_progresso_vale', {'mensagem': "Gravando dados no Banco de Dados JSON..."})
            logger.info("Gravando dados JSON da Vale...")
            
            from planilha_manager import planilha_manager
            
            linhas_inserir = []
            cnt = 0
            for ev in dados:
                d_agg, q_agg, mo_agg, ma_agg = [], [], [], []
                pref = len(ev['itens']) > 1
                for it in ev['itens']:
                    p = f"(Item {it['item_num']}) " if pref else ""
                    q_agg.append(f"{p}{it['quantidade']}")
                
                    # CHAMA A IA PARA LER O TEXTO DA VALE
                    descricoes_unicas = []
                    for k in ['descricao', 'texto_item', 'texto_compra']:
                        v = str(it.get(k, '')).strip()
                        if v and v.lower() not in [x.lower() for x in descricoes_unicas]:
                            descricoes_unicas.append(v)
                    txt_completo = f"{it.get('titulo','')} {' '.join(descricoes_unicas)} {it.get('part_number','')}"
                    dados_ia = it.get('dados_ia')
                    if not dados_ia:
                        logger.info(f"   - Analisando item {it['item_num']} com IA...")
                        dados_ia = getattr(self, 'extrair_dados_gemini')(txt_completo)
                        it['dados_ia'] = dados_ia
                
                    ma = dados_ia.get("marca")
                    mo_extraido = dados_ia.get("modelo")
                    pn_extraido = dados_ia.get("part_number")
                    mo = pn_extraido if pn_extraido else mo_extraido
                
                    if mo: mo_agg.append(f"{p}{mo}")
                    if ma: ma_agg.append(f"{p}{ma}")
                
                    # Escolhe a descricao
                    melhor_desc = str(it.get('descricao', '')).strip()
                    if mo or ma:
                        for d in descricoes_unicas:
                            d_low = d.lower()
                            if ((mo and str(mo).lower() in d_low) or (ma and str(ma).lower() in d_low)):
                                melhor_desc = d
                                break
                            
                    d_agg.append(f"{p}{melhor_desc[:150000]}")
                
                vendedores_set = set()
                for it in ev['itens']:
                    match2 = it.get('historico_match')
                    if not match2 and 'dados_ia' in it:
                        match2 = getattr(self, 'encontrar_vendedor_similar', lambda x,y: None)(it, it['dados_ia'])
                        it['historico_match'] = match2
                    if match2:
                        m = __import__('re').search(r'Ultimo Vendedor:\s*([^\(]+)', match2)
                        if m: vendedores_set.add(m.group(1).strip())
                    
                vendedor_str = ", ".join(vendedores_set) if vendedores_set else ""

                vals = [ev['numero_evento'], ev['data_vencimento'].split(' - ')[0], "\n".join(d_agg), "\n".join(q_agg), ev['itens'][0]['local_entrega'], vendedor_str, "\n".join(mo_agg), "\n".join(ma_agg), ""]
                linhas_inserir.append(vals)
            
                cnt += 1
        
            planilha_manager.adicionar_linhas(self.NOME_ABA_ALVO, linhas_inserir)
        
            logger.info(f"JSON salvo com sucesso (+{cnt} linhas).")
            self.sio.emit('relatar_progresso_vale', {'mensagem': f"Banco de Dados atualizado com sucesso (+{cnt} novas cotações)."})
            return True
            
        except Exception as e:
            logger.error(f"ERRO AO SALVAR JSON: {e}")
            if hasattr(self, 'relatorio_email_stats') and 'erros_planilha' in self.relatorio_email_stats:
                for ev in dados:
                    if ev['numero_evento'] not in self.relatorio_email_stats['erros_planilha']:
                        self.relatorio_email_stats['erros_planilha'].append(ev['numero_evento'])
            return False
    # ========================================================
    # 8. VERIFICAÃ‡ÃƒO DE EVENTOS (COM GESTÃƒO DE NOVA ABA)
    # ========================================================
    def verificar_eventos(self):
        logger.info("\n--- INICIANDO VERIFICAÃ‡ÃƒO DE EVENTOS VALE (NOVA ABA) ---")
        try:
            # === 1. ABRIR NOVA ABA E MUDAR PARA ELA ===
            self.driver.execute_script("window.open('');")
            self.driver.switch_to.window(self.driver.window_handles[-1])
            
            link_verificacao = "https://valebasemetals.coupahost.com/quotes/external_responses/824e8a0982de566c7bebeb7315a6bd89b533033a175c5acddf970e59ccc1e3b7954b1ce5107982e6/terms?quote_request_id=6213&source=event_invitation"
            self.driver.get(link_verificacao)
            time.sleep(3)

            # === 2. PEDIR O OTP Ã€ NUVEM ===
            self.otp_atual = None
            self.sio.emit('pedir_otp_usuario')
            
            timeout = 180 # 3 minutos de limite
            start_time = time.time()
            logger.info("Robo pausado. Aguardando utilizador inserir o OTP no painel...")
            
            while self.otp_atual is None:
                # Se clicar em "Parar RobÃ´" no site, aborta na hora!
                if getattr(self, 'solicitacao_parada', False):
                    raise Exception("Processo cancelado pelo utilizador durante a espera do OTP.")
                if time.time() - start_time > timeout:
                    raise Exception("Tempo esgotado! O código OTP não foi inserido.")
                time.sleep(1)
            
            # === 3. INJETAR OTP ===
            logger.info(" OTP recebido. Injetando no portal...")
            campo_otp = self.wait.until(EC.presence_of_element_located((By.ID, "supplier_otp")))
            campo_otp.clear()
            campo_otp.send_keys(self.otp_atual)
            campo_otp.send_keys(Keys.RETURN)
            time.sleep(4)

            # === 4. NAVEGAR PARA PÃGINA INICIAL ===
            self.wait.until(EC.element_to_be_clickable((By.ID, "home"))).click()
            time.sleep(4)

            # === 5. MAPEAR A TABELA ===
            logger.info(" Lendo status dos eventos na tabela...")
            status_eventos = {}
            
            while True:
                if getattr(self, 'solicitacao_parada', False):
                    raise Exception("Processo cancelado pelo utilizador.")

                time.sleep(2)
                linhas = self.driver.find_elements(By.XPATH, "//tr[contains(@class, 'coupa_datatable_row')]")
                
                for linha in linhas:
                    try:
                        id_evento = linha.find_element(By.XPATH, ".//span[@class='dt_open_link']").text.strip()
                        num_respostas = linha.find_element(By.XPATH, ".//td[contains(@class, 's-datatable-cell-num_responses')]").text.strip()
                        status_eventos[id_evento] = num_respostas
                    except:
                        continue 

                # Paginação
                try:
                    botao_avancar = self.driver.find_element(By.XPATH, "//a[contains(@class, 'next_page') and contains(text(), 'Avançar')]")
                    self.driver.execute_script("arguments[0].click();", botao_avancar)
                    logger.info(" Avançando para a próxima página...")
                    time.sleep(3) 
                except:
                    logger.info("✅ Fim das páginas atingido. Leitura concluída.")
                    break

            # === 6. PINTAR EXCEL ===
            self._colorir_excel_verificacao(status_eventos)
            self.sio.emit('tarefa_concluida', {'evento': 'Verificação Vale', 'sucesso': True})

        except Exception as e:
            logger.error(f" Erro na verificação: {e}")
            self.sio.emit('tarefa_concluida', {'evento': 'Verificação Vale', 'sucesso': False, 'erro': str(e)})

        finally:
            # === 7. GESTÃƒO DE ABAS (Executado SEMPRE no final, dÃª erro ou não) ===
            try:
                if len(self.driver.window_handles) > 1:
                    logger.info(" Fechando aba extra e retornando ao painel principal...")
                    self.driver.close() # Fecha a aba que foi aberta para a verificação
                    self.driver.switch_to.window(self.driver.window_handles[0]) # Volta para a primeira aba (Original)
            except Exception as e:
                logger.error(f"Erro ao tentar restaurar abas: {e}")

    # ========================================================
    # 7. PINTAR EXCEL APÃ“S VERIFICAÃ‡ÃƒO OTP
    # ========================================================
    def _colorir_excel_verificacao(self, status_eventos):
        logger.info(" Atualizando cores na Planilha de Controle...")
        
        if os.path.exists(self.ARQUIVO_PLANILHA_CONTROLE):
            try:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                shutil.copy2(self.ARQUIVO_PLANILHA_CONTROLE, os.path.join(self.PASTA_BACKUPS, f"backup_cores_{ts}.xlsx"))
            except: pass
            
        while True:
            try:
                if getattr(self, 'solicitacao_parada', False): return
                from planilha_manager import planilha_manager
                planilha_manager.iniciar(self.ARQUIVO_PLANILHA_CONTROLE)
                
                # Transforma status_eventos (str -> str de num_respostas) em str -> bool (respondido ou não)
                mapa_status = { k: (v != "0") for k, v in status_eventos.items() }
                
                planilha_manager.atualizar_status_respostas(self.NOME_ABA_ALVO, mapa_status, col_chave=1, col_status=9)
                logger.info("✅ Planilha atualizada com o status dos eventos!")
                break # Sucesso
            except PermissionError:
                if 'wb' in locals() and wb:
                    try: wb.close()
                    except: pass
                logger.warning("⏳ Planilha em uso. Aguardando liberação para pintar as cores...")
                if hasattr(self, 'sio'):
                    self.sio.emit('relatar_progresso_vale', {'mensagem': "⚠️ PLANILHA EM USO! O robô ficará tentando salvar até a planilha ser fechada..."})
                time.sleep(15)
                continue
            except Exception as e:
                if 'wb' in locals() and wb:
                    try: wb.close()
                    except: pass
                logger.error(f"Erro ao pintar Excel: {e}")
                break
