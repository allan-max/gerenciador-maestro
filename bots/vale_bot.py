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
import ambiente
import log_robos
import csv
from datetime import datetime, timedelta
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
from bots.termos_participacao import aceitar_termos
from selenium.webdriver.common.action_chains import ActionChains

from docx import Document
from docx.shared import Pt, RGBColor, Cm
import base64
from bots.transmissor_captcha import manter_transmissao, parar_transmissao

logger = logging.getLogger("ValeApp")

try:
    from bots.recomendacao_vendedor import selecionar_alvos_comparacao_item
except ImportError:
    from recomendacao_vendedor import selecionar_alvos_comparacao_item


try:
    from bots.validacao_cotacao import validar_itens_evento, contem_expected_quantity
except ImportError:
    from validacao_cotacao import validar_itens_evento, contem_expected_quantity

logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')

# 👇 GARANTE A GRAVAÇÃO SEM APAGAR O HISTÓRICO 👇
log_robos.configurar(logger, 'log-vale.txt')   # data, máquina e rotação; sem o servidor, só console

# Login: limite total e limite do captcha (antes o laço podia rodar para sempre)
LOGIN_TIMEOUT_S = 600
CAPTCHA_TIMEOUT_S = 300


class LoginTimeout(Exception):
    pass


# Verificação (docs/melhorias-bots, fase 3)
VALE_URL_VERIFICACAO_PADRAO = "https://valebasemetals.coupahost.com/quotes/external_responses/824e8a0982de566c7bebeb7315a6bd89b533033a175c5acddf970e59ccc1e3b7954b1ce5107982e6/terms?quote_request_id=6213&source=event_invitation"   # CONFIG.json "vale_url_verificacao" muda
OTP_TIMEOUT_S = 180
OTP_EMAIL_INTERVALO_S = 10   # de quanto em quanto tempo procura o código na caixa (otp_email.py)
JANELA_NAO_RESPONDIDO_DIAS = 7   # NÃO RESPONDIDO recente continua sendo conferido


# Portal instável (docs/melhorias-bots, fase 4): 3 'Oops' seguidos = pausa; depois de 3 pausas, abandona o ciclo
OOPS_POR_PAUSA = 3
PAUSA_OOPS_S = 600
MAX_PAUSAS_OOPS = 3

from bots.coupa.mixins.resposta import RespostaMixin

class ValeScraper(RespostaMixin):
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
                with open('CONFIG.json', 'r', encoding='utf-8-sig') as f:   # aceita BOM (Bloco de Notas)
                    self.config = json.load(f)
        except Exception:
            pass

        import ambiente
        if ambiente.modo_teste():   # testes não tocam a base de produção
            self.config["BASE_DIR"] = ambiente.BASE_TESTE
            self.config["pasta_coupa_vale"] = os.path.join(ambiente.BASE_TESTE, "eventos do coupa")
            cand = os.path.join(ambiente.RAIZ, "teste", "teste.xlsx")
            self.config["caminho_excel"] = cand if os.path.exists(cand) else os.path.join(ambiente.RAIZ, "teste.xlsx")
            for chave in ("PASTA_DATABASE", "PASTA_BLOQUEADOS"):
                self.config.pop(chave, None)

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
                ambiente.exigir_trava(self.ARQUIVO_JSONL)
                # Guarda o vencimento_tabela no JSONL
                novo = {
                    "titulo": numero, 
                    "data": str(datetime.now().date()), 
                    "respondido": respondido,
                    "vencimento_tabela": vencimento_tabela
                }
                ambiente.acrescentar_jsonl(self.ARQUIVO_JSONL, novo)
                self.db_eventos[numero] = novo
            except Exception: logger.exception(f'Falha ao gravar o evento {numero} no JSONL')

    def evento_ja_processado(self, num: str) -> bool: 
        return num in self.db_eventos

    def setup_driver(self):
        logger.info("ðŸš— Configurando Navegador...")
        ops = Options()
        ops.add_argument("--no-sandbox")
        ops.add_argument("--disable-dev-shm-usage")
        ops.add_argument("--window-size=1920,1080")
        ops.add_argument("--remote-allow-origins=*")
        # Servidor sem ninguém olhando (RDP desconectado/janela coberta): sem isto o Chrome congela a
        # renderização e a transmissão do captcha fica travada
        ops.add_argument("--disable-backgrounding-occluded-windows")
        ops.add_argument("--disable-renderer-backgrounding")
        ops.add_argument("--disable-background-timer-throttling")
        anterior = getattr(self, 'driver', None)
        if anterior is not None:   # religar o mesmo robô: fecha o navegador dele antes de abrir outro
            try:
                anterior.quit()
            except Exception:
                pass
        import navegador
        ops.add_argument(f"--user-data-dir={navegador.preparar('vale')}")   # fecha Chrome que sobrou deste robô
        self.driver = webdriver.Chrome(options=ops)
        self.driver.set_page_load_timeout(60)
        self.wait = WebDriverWait(self.driver, 40)

        #==============================================
        self.memoria_associativa = {}
        
        # === 1. VARIÃVEL E OUVINTE DO OTP ===
        self.otp_atual = None 
        
        @self.sio.on('receber_otp_frontend')
        def on_receber_otp(dados):
            self.otp_atual = (str(dados.get('otp') or '')).strip() or None   # vazio não conta
            logger.info(" Código OTP recebido do painel.")

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
            self.solicitacao_parada = False   # cada tarefa começa sem a parada da anterior
            self.motivo_abandono = None
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
        try:
            self.driver.get(self.LOGIN_URL)
        except Exception as e_nav:
            logger.warning(f"Aviso ao carregar URL de login inicial: {e_nav}")
        ultimo_print = 0
        ultimo_clique_botao = 0 # <--- NOVO: Controle para evitar flood de cliques
        inicio_captcha = None
        limite_login = time.time() + LOGIN_TIMEOUT_S
        ultimo_erro = None
        email = "VENDAS@VENTURAINFORMATICA.COM.BR"
        senha = os.environ.get("PORTAL_SENHA") or ""   # só do .env: senha no código fica pública no GitHub
        if not senha:
            raise RuntimeError("PORTAL_SENHA não configurada no .env do gerenciador.")
        captcha_pendente = False

        while True:
            if not captcha_pendente:
                time.sleep(0.3)
            else:
                time.sleep(0.005)
            if time.time() > limite_login:
                self.solicitacao_parada = True   # quem engolir o erro ainda para o ciclo no próximo evento
                self.motivo_abandono = 'Login não concluído (captcha ou portal fora do ar).'
                raise LoginTimeout(f"LOGIN_TIMEOUT: login não concluiu em {LOGIN_TIMEOUT_S // 60} min")
            try:
                if len(self.driver.window_handles) > 1:
                    self.driver.switch_to.window(self.driver.window_handles[-1])

                url_atual = self.driver.current_url or ""
                if url_atual.startswith("data:") or url_atual == "about:blank":
                    self.driver.get(self.LOGIN_URL)
                    time.sleep(1)
                    continue

                if "sessions/new" not in url_atual:
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
                    if inicio_captcha is not None:
                        parar_transmissao(self)
                        self.sio.emit('captcha_resolvido_do_robo', {'resolvido': True})
                    logger.info("✅ Login concluído com sucesso!")
                    return True

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
                        self.sio.emit('relatar_progresso', {'mensagem': '⚠️ Captcha detectado no login! Resolva na tela principal de Minhas Cotações...'})
                    elif time.time() - inicio_captcha > CAPTCHA_TIMEOUT_S:
                        self.sio.emit('relatar_progresso_vale', {'mensagem': '❌ CAPTCHA EXPIRADO! (5 min sem solução). Fechando o robô.'})
                        parar_transmissao(self)
                        raise LoginTimeout("CAPTCHA_TIMEOUT")
                        
                    elemento_alvo = iframe_desafio if (iframe_desafio and iframe_desafio.is_displayed()) else iframe_caixinha
                    if elemento_alvo:
                        ultimo_check_dom = time.time()
                        rect = None
                        try:
                            rect = elemento_alvo.rect
                        except Exception:
                            rect = None

                        while True:
                            # 1. Clique ultrarrápido (CDP direto em 5ms, sem pausar o stream)
                            if self.aguardando_clique.is_set():
                                try:
                                    cx = int(self.coordenadas_clique.get('x', 0))
                                    cy = int(self.coordenadas_clique.get('y', 0))
                                    clique_ok = False
                                    if rect and hasattr(self.driver, 'execute_cdp_cmd'):
                                        try:
                                            abs_x = int(rect['x'] + cx)
                                            abs_y = int(rect['y'] + cy)
                                            self.driver.execute_cdp_cmd('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': abs_x, 'y': abs_y})
                                            self.driver.execute_cdp_cmd('Input.dispatchMouseEvent', {'type': 'mousePressed', 'x': abs_x, 'y': abs_y, 'button': 'left', 'clickCount': 1})
                                            self.driver.execute_cdp_cmd('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'x': abs_x, 'y': abs_y, 'button': 'left', 'clickCount': 1})
                                            clique_ok = True
                                        except Exception:
                                            clique_ok = False
                                    if not clique_ok:
                                        larg = (rect or {}).get('width') or elemento_alvo.size['width']
                                        alt = (rect or {}).get('height') or elemento_alvo.size['height']
                                        px = cx - (larg / 2)
                                        py = cy - (alt / 2)
                                        ActionChains(self.driver).move_to_element_with_offset(elemento_alvo, px, py).click().perform()
                                except Exception:
                                    pass
                                finally:
                                    self.aguardando_clique.clear()

                            # 2. Transmissão contínua (~60 FPS) numa thread própria, CDP direto: bots/transmissor_captcha.py
                            tempo_atual = time.time()
                            if manter_transmissao(self, self.driver, self.sio, rect) is None and tempo_atual - ultimo_print >= 0.5:
                                # iframe sem posição: caminho lento antigo (print do elemento)
                                try:
                                    png_bytes = getattr(elemento_alvo, 'screenshot_as_png', None)
                                    if png_bytes:
                                        png_b64 = base64.b64encode(png_bytes).decode('utf-8')
                                        self.sio.emit('imagem_captcha_do_robo', {'imagem': png_b64})
                                except Exception:
                                    pass
                                ultimo_print = tempo_atual

                            # 3. Timeout check
                            if time.time() - inicio_captcha > CAPTCHA_TIMEOUT_S:
                                self.sio.emit('relatar_progresso_vale', {'mensagem': '❌ CAPTCHA EXPIRADO! (5 min sem solução). Fechando o robô.'})
                                parar_transmissao(self)
                                raise LoginTimeout("CAPTCHA_TIMEOUT")

                            # 4. Verificação periódica do DOM a cada 400ms (não a cada frame de 16ms!)
                            if time.time() - ultimo_check_dom >= 0.4:
                                ultimo_check_dom = time.time()
                                try:
                                    if not elemento_alvo.is_displayed():
                                        break
                                    if elemento_alvo is iframe_caixinha:
                                        break
                                    rect = elemento_alvo.rect
                                except Exception:
                                    break

                            time.sleep(0.005)
                    continue 

                if captcha_ficou_verde:
                    if inicio_captcha is not None:
                        parar_transmissao(self)
                        self.sio.emit('captcha_resolvido_do_robo', {'resolvido': True})
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

            except LoginTimeout:
                self.solicitacao_parada = True
                self.motivo_abandono = 'Login não concluído (captcha ou portal fora do ar).'
                raise
            except Exception as e:
                # Elementos somem e recarregam o tempo todo no login: registra só quando o erro muda
                if str(e) != ultimo_erro:
                    ultimo_erro = str(e)
                    logger.warning(f"Login: {type(e).__name__}: {ultimo_erro[:200]}")

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
        import ambiente, instancia_unica
        if not ambiente.modo_teste() and not instancia_unica.trava_ativa():
            # testar_coupa.py e afins rodavam o robô sozinho e gravavam na base de produção (13/10)
            msg = "Robô recusado: fora do gerenciador ele só roda em modo teste (MAESTRO_TESTE=1)."
            logger.error(msg)
            self.sio.emit('tarefa_concluida', {'evento': 'Login do Robo', 'sucesso': False, 'erro': msg})
            return
        log_robos.configurar(logger, 'log-vale.txt')   # o servidor podia estar fora quando o módulo foi importado
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
    def _pausa_por_oops(self):
        """Conta telas 'Oops' seguidas. A cada OOPS_POR_PAUSA pausa PAUSA_OOPS_S; passou de MAX_PAUSAS_OOPS,
        abandona o ciclo (liga a parada e guarda o motivo). Devolve True quando é para parar de tentar."""
        self.oops_seguidos = getattr(self, 'oops_seguidos', 0) + 1
        if self.oops_seguidos % OOPS_POR_PAUSA:
            return False
        pausa = self.oops_seguidos // OOPS_POR_PAUSA
        if pausa > MAX_PAUSAS_OOPS:
            self.motivo_abandono = f"Portal instável: {self.oops_seguidos} telas 'Oops' seguidas; ciclo abandonado."
            logger.error(self.motivo_abandono)
            self.solicitacao_parada = True
            return True
        logger.warning(f"{self.oops_seguidos} telas 'Oops' seguidas: pausa de {PAUSA_OOPS_S // 60} min ({pausa}/{MAX_PAUSAS_OOPS}).")
        fim = time.time() + PAUSA_OOPS_S
        while time.time() < fim and not self.solicitacao_parada:
            time.sleep(5)
        return self.solicitacao_parada

    def varrer_painel_eventos(self):
        logger.info("\n--- INICIANDO VARREDURA DE EVENTOS (VALE BASE METALS) ---")
        self.oops_seguidos = 0   # a parada é zerada ao iniciar a tarefa; aqui ela se perdia no aquecimento
        
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
                    
                    res = self.extrair_dados_do_evento(t['url'], t['num'], re_extrair=t.get('re_extrair', False), venc_tabela=t.get('venc_tabela', ''))
                    
                    if res == "ENCERRADO":
                        # "Já terminou" é definitivo: grava (novo ou reaberto) com a data da tabela, senão volta em todo
                        # ciclo. Se reabrir de novo, a data muda e ele volta como REABERTO. Oops/0 itens tentam de novo.
                        anterior = self.db_eventos.get(t['num'], {})
                        self.salvar_evento_jsonl(t['num'], anterior.get('respondido', bool(t.get('re_extrair'))), t.get('venc_tabela', ''))

                    if res is None:
                        self.relatorio_email_stats['erros_extracao'].append(t['num'])
                    
                    if res and res not in ("SKIPPED", "ENCERRADO"):
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
            motivo = getattr(self, 'motivo_abandono', None)   # abandonado (Oops/login) não é sucesso
            self.sio.emit('tarefa_concluida', {'evento': 'Lote de Extração', 'sucesso': not motivo, **({'erro': motivo} if motivo else {})})

        except Exception as e:
            logger.error(f"❌ Erro Crítico na varredura: {e}")
            if hasattr(self, 'relatorio_email_stats'):
                self.relatorio_email_stats['erro_critico_bot'] = str(e)
                self.enviar_relatorio_email()
            self.sio.emit('tarefa_concluida', {'evento': 'Lote de Extração Vale', 'sucesso': False, 'erro': str(e)})

    def enviar_relatorio_email(self):
        if getattr(self, 'relatorio_email_stats', None) is None:
            return

        try:
            from bots.relatorio_email import enviar_email_relatorio
        except ImportError:
            from relatorio_email import enviar_email_relatorio

        enviar_email_relatorio(
            nome_bot="Vale Bot",
            stats=self.relatorio_email_stats,
            config=getattr(self, 'config', {}),
            logger_inst=logger
        )

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

    @staticmethod
    def parsear_data_vencimento(data_str: str) -> Optional[str]:
        if not data_str or str(data_str).strip() in ['N/A', '', 'None']:
            return None
        texto = str(data_str).strip()
        if re.match(r'^\d{2}/\d{2}/\d{4}(\s*-\s*\d{2}:\d{2})?$', texto):
            return texto
        m_iso = re.search(r'(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{1,2}):(\d{2}))?', texto)
        if m_iso:
            ano, mes, dia = m_iso.group(1), m_iso.group(2), m_iso.group(3)
            hora = f"{int(m_iso.group(4)):02d}:{m_iso.group(5)}" if m_iso.group(4) else ""
            return f"{dia}/{mes}/{ano} - {hora}" if hora else f"{dia}/{mes}/{ano}"
        m_coupa = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2,4})(?:\s+(\d{1,2}):(\d{2})\s*(am|pm)?)?', texto, re.IGNORECASE)
        if m_coupa:
            p1, p2, p3 = int(m_coupa.group(1)), int(m_coupa.group(2)), int(m_coupa.group(3))
            ano = p3 if p3 >= 1000 else (2000 + p3 if p3 < 100 else p3)
            if p1 > 12: dia, mes = p1, p2
            elif p2 > 12: dia, mes = p2, p1
            else: mes, dia = p1, p2
            hora_str = ""
            if m_coupa.group(4) and m_coupa.group(5):
                h = int(m_coupa.group(4))
                minu = m_coupa.group(5)
                ampm = (m_coupa.group(6) or "").lower()
                if ampm == 'pm' and h < 12: h += 12
                elif ampm == 'am' and h == 12: h = 0
                hora_str = f" - {h:02d}:{minu}"
            return f"{dia:02d}/{mes:02d}/{ano}{hora_str}"
        return None

    def capturar_end_time_elemento(self) -> Optional[str]:
        """
        Captura a data de vencimento real do elemento HTML do Coupa:
        <div class="img" id="end_time">
          <div class="month">Out</div>
          <div class="date">23</div>
        </div>
        """
        try:
            elementos = self.driver.find_elements(By.CSS_SELECTOR, "#end_time, div.end_time, .end_time")
            for el in elementos:
                try:
                    data_attr = el.get_attribute("data-end-date")
                    parsed_attr = self.parsear_data_vencimento(data_attr)
                    if parsed_attr:
                        return parsed_attr
                except: pass

                try:
                    mes_el = el.find_elements(By.CLASS_NAME, "month")
                    dia_el = el.find_elements(By.CLASS_NAME, "date")
                    if mes_el and dia_el:
                        mes_raw = mes_el[0].text.strip().lower()
                        dia_raw = dia_el[0].text.strip()
                        mapa_meses = {
                            'jan': 1, 'fev': 2, 'feb': 2, 'mar': 3, 'abr': 4, 'apr': 4,
                            'mai': 5, 'may': 5, 'jun': 6, 'jul': 7, 'ago': 8, 'aug': 8,
                            'set': 9, 'sep': 9, 'out': 10, 'oct': 10, 'nov': 11, 'dez': 12, 'dec': 12
                        }
                        mes_prefix = mes_raw[:3]
                        if mes_prefix in mapa_meses and dia_raw.isdigit():
                            m = mapa_meses[mes_prefix]
                            y = datetime.now().year
                            hora_str = ""
                            try:
                                h_els = el.find_elements(By.CSS_SELECTOR, "span.quotes_h1, .time, .hour")
                                for h_el in h_els:
                                    t_h = h_el.text.strip()
                                    m_h = re.search(r'(\d{1,2}:\d{2})', t_h)
                                    if m_h:
                                        hora_str = f" - {m_h.group(1)}"
                                        break
                            except: pass
                            return f"{int(dia_raw):02d}/{m:02d}/{y}{hora_str}"
                except: pass
        except: pass
        return None

    def _texto_valido(self, t):
        return bool(t) and t.strip().lower() not in ('', 'nenhum', 'none', 'n/a')

    def _ler_frete_linha(self, l):
        """Incoterms (EXW, CIF, FOB...) da linha do item; só existe com o item expandido. '' se não achar."""
        try: frete = l.find_element(By.CSS_SELECTOR, "div.s-shipping_term p.s-selectField").text
        except:
            try: frete = l.find_element(By.XPATH, ".//div[contains(@class, 's-fieldLabel') and contains(text(), 'Incoterms')]/following-sibling::p[contains(@class, 's-selectField')]").text
            except: return ""
        frete = (frete or "").strip().upper()
        return frete if frete.lower() not in ('nenhum', 'none', 'n/a') else ""

    def _frete_do_evento(self, itens, frete_pagina, padrao="EXW"):
        """Frete do evento: o primeiro Incoterms informado pelos itens; sem nenhum, o da página; por último, EXW."""
        for it in itens or []:
            frete = str(it.get("frete") or "").strip().upper()
            if frete and frete.lower() not in ('nenhum', 'none', 'n/a'):
                return frete
        frete = str(frete_pagina or "").strip().upper()
        return frete if frete and frete.lower() not in ('nenhum', 'none', 'n/a') else padrao

    def _ler_campos_linha(self, l):
        """Uma leitura (sem cliques) de título, descrição, quantidade, local de entrega e textos extras da linha."""
        tit = "Item"; desc = ""; qtd = "0"; loc = "N/A"
            
        # 1. Título e Descrição
        try: tit = l.find_element(By.CSS_SELECTOR, "div.s-description p.s-textField").text.strip()
        except: pass
        try: 
            d = l.find_elements(By.CSS_SELECTOR, "p.s-textField")
            if len(d) > 1: desc = self._filter_desc(d[1].text)
        except: pass
            
        # Nova verificação: Texto de compra do material
        if not desc.strip() or desc.strip().lower() == "nenhum":
            try:
                dt = l.find_element(By.XPATH, ".//dt[contains(text(), 'Texto de compra')]")
                dd = dt.find_element(By.XPATH, "following-sibling::dd")
                desc = self._filter_desc(dd.text)
            except: pass
            
        # Nova verificação: Item Text
        if not desc.strip() or desc.strip().lower() == "nenhum":
            try:
                dt = l.find_element(By.XPATH, ".//dt[contains(text(), 'Item Text')]")
                dd = dt.find_element(By.XPATH, "following-sibling::dd")
                desc = self._filter_desc(dd.text)
            except: pass

        # Nova verificação: Material Purchase Text
        if not desc.strip() or desc.strip().lower() == "nenhum":
            try:
                dt = l.find_element(By.XPATH, ".//dt[contains(text(), 'Material Purchase Text')]")
                dd = dt.find_element(By.XPATH, "following-sibling::dd")
                desc = self._filter_desc(dd.text)
            except: pass
            
        # 2. Quantidade (BLINDADO CONTRA PREÇOS "BRL")
        try: 
            elementos_unidade = l.find_elements(By.CSS_SELECTOR, "p.valueWithUnit")
            for q in elementos_unidade:
                texto_q = q.text.strip()
                if "BRL" not in texto_q and "R$" not in texto_q and "USD" not in texto_q:
                    qtd = texto_q
                    break
        except: pass
            
        # 3. Local de Entrega (BUSCA ROBUSTA POR PALAVRA-CHAVE)
        try:
            elemento_local = l.find_element(By.XPATH, ".//*[contains(text(), 'Local de entrega:')]")
            loc = self.parse_local(elemento_local.text)
        except: 
            try: loc = self.parse_local(l.find_element(By.CSS_SELECTOR, "li.attachmentText").text)
            except: pass
            
        # Nova verificação: Endereço de entrega detalhado
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
        return {"tit": tit, "desc": desc, "qtd": qtd, "loc": loc, "texto_item": texto_item_extra, "texto_compra": texto_compra_extra, "frete": self._ler_frete_linha(l)}

    def _extrair_itens_linhas(self, linhas):
        itens = []
        tem_anexo_geral = False
        MAX_TENTATIVAS = 3
        for i, l in enumerate(linhas):
            # Insistência inteligente: o Local de Entrega só aparece com o item expandido (a quantidade aparece
            # mesmo fechado). Sem local = não expandiu, clica de novo; com local e sem textos = já está aberto,
            # só relê (um novo clique fecharia o item).
            campos = {"tit": "Item", "desc": "", "qtd": "0", "loc": "N/A", "texto_item": "", "texto_compra": "", "frete": ""}
            item_expandido = False
            tem_texto_descritivo = False
            for tentativa in range(MAX_TENTATIVAS):
                if tentativa == 0:
                    try:
                        btn_expandir = l.find_element(By.CSS_SELECTOR, "img.s-expandLines, div.s-expandSidebar")
                        ActionChains(self.driver).click(btn_expandir).perform()
                    except: pass
                    time.sleep(0.3)
                elif not item_expandido:
                    logger.warning(f"       ⚠️ Item {i+1} não expandiu (sem Local de Entrega). Re-expandindo (tentativa {tentativa+1}/{MAX_TENTATIVAS})...")
                    try: self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", l)
                    except: pass
                    try:
                        btn_expandir = l.find_element(By.CSS_SELECTOR, "img.s-expandLines, div.s-expandSidebar")
                        ActionChains(self.driver).click(btn_expandir).perform()
                    except: pass
                    time.sleep(0.6 + 0.2 * (tentativa - 1))
                else:
                    logger.info(f"       🔁 Item {i+1} já aberto, mas sem textos descritivos. Relendo sem clicar (tentativa {tentativa+1}/{MAX_TENTATIVAS})...")
                    time.sleep(0.6)

                try:
                    l = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")[i]
                except: pass

                try:
                    if l.find_elements(By.CSS_SELECTOR, "li.attachment.attachmentFile.s-attachmentFile"):
                        tem_anexo_geral = True
                except: pass

                try: leitura = self._ler_campos_linha(l)
                except Exception: leitura = {}

                # Uma releitura nunca apaga o que uma tentativa anterior já tinha achado
                if leitura.get("tit", "Item") != "Item": campos["tit"] = leitura["tit"]
                if leitura.get("qtd", "0") != "0": campos["qtd"] = leitura["qtd"]
                for chave in ("desc", "texto_item", "texto_compra"):
                    if self._texto_valido(leitura.get(chave, "")) or not campos[chave].strip():
                        campos[chave] = leitura.get(chave, "") or campos[chave]

                loc_lido = leitura.get("loc", "N/A") or ""
                item_expandido = bool(loc_lido.strip()) and loc_lido != "N/A"
                if item_expandido: campos["loc"] = loc_lido
                if leitura.get("frete"): campos["frete"] = leitura["frete"]

                tem_texto_descritivo = any(self._texto_valido(campos[c]) for c in ("desc", "texto_item", "texto_compra"))
                if item_expandido and tem_texto_descritivo:
                    break
            else:
                logger.warning(f"       ⚠️ Item {i+1}: extração incompleta após {MAX_TENTATIVAS} tentativas (expandido: {item_expandido} | textos: {tem_texto_descritivo}).")

            tit = campos["tit"]; desc = campos["desc"]; qtd = campos["qtd"]; loc = campos["loc"]
            texto_item_extra = campos["texto_item"]; texto_compra_extra = campos["texto_compra"]

            # DESCRIÇÃO nunca fica em branco quando o item tem algum texto
            if not self._texto_valido(desc) and (texto_compra_extra or texto_item_extra):
                desc = texto_compra_extra or texto_item_extra

            logger.info(f"       Item {i+1}: {tit[:20]}... | Qtd: {qtd} | Local: {loc[:30]}...")
            itens.append({"item_num": i+1, "titulo": tit, "descricao": desc, "quantidade": qtd, "local_entrega": loc, "texto_item": texto_item_extra, "texto_compra": texto_compra_extra, "frete": campos["frete"]})
        return itens, tem_anexo_geral

    def extrair_dados_do_evento(self, url: str, num: str, ja_respondido: bool = False, is_negotiation: bool = False, re_extrair: bool = False, venc_tabela: str = ""):
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
                    if self._pausa_por_oops(): return "SKIPPED"
                    
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
                    self.oops_seguidos = 0
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
                logger.warning("   âš ï¸ Evento encerrado."); self.driver.switch_to.default_content(); return "ENCERRADO"

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

            vencimento = self.parsear_data_vencimento(venc_tabela) or "N/A"
            venc_inicial = self.capturar_end_time_elemento()
            if venc_inicial:
                vencimento = venc_inicial
                logger.info(f"   📅 Vencimento detectado na tela do evento: {vencimento}")
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
                # "Aceita os Termos e condições?" (div.s-term): marca o Sim de cada termo antes do Pretendo
                aceitar_termos(self.driver)
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
# ==========================================================
            # 5. EXTRAÇÃO E VALIDAÇÃO DOS ITENS (COM RETENTATIVAS OBRIGATÓRIAS)
            # REGRA: Sempre que exibir 'Expected Quantity x Unit Price' ou itens não identificados,
            # DEVE retentar extrair os itens antes de prosseguir para o próximo evento.
            # Cada campo é validado individualmente a cada tentativa.
            # Se persistir após múltiplas tentativas, envia e-mail de alerta e a cotação NÃO é processada com sucesso.
            # ==========================================================
            MAX_TENTATIVAS_ITENS = 3
            itens_validados = False
            itens = []
            tem_anexo_geral = False
            ultimo_motivo_invalido = ""
            teve_expected_quantity = False

            for tentativa in range(1, MAX_TENTATIVAS_ITENS + 1):
                logger.info(f"    🔍 Validação de Itens do evento {num} (Tentativa {tentativa}/{MAX_TENTATIVAS_ITENS})...")

                if tentativa > 1:
                    logger.warning(f"    ⏳ Recarregando página para nova tentativa de extração do evento {num}...")
                    try:
                        self.driver.refresh()
                        time.sleep(6)
                        try: self.driver.switch_to.alert.accept()
                        except: pass
                        self.garantir_logado(url)
                        self.driver.switch_to.default_content()
                        try: self.driver.switch_to.frame(self.driver.find_element(By.TAG_NAME, "iframe"))
                        except: pass
                    except Exception: pass

                linhas = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")

                if len(linhas) == 0:
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
                            if botoes:
                                self.driver.execute_script("arguments[0].click();", botoes[0])
                                time.sleep(4)
                                break
                        except: pass

                    self.garantir_logado(url)
                    self.driver.switch_to.default_content()
                    try: self.driver.switch_to.frame(self.driver.find_element(By.TAG_NAME, "iframe"))
                    except: pass
                    linhas = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")

                if len(linhas) == 0:
                    try:
                        dropdowns = self.driver.find_elements(By.ID, "participation")
                        if dropdowns:
                            aceitar_termos(self.driver)   # "Aceita os Termos e condições?" -> Sim, antes do Pretendo
                            try: Select(dropdowns[0]).select_by_value("true")
                            except: self.driver.execute_script("arguments[0].value='true'; arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", dropdowns[0])
                            time.sleep(1)
                            btn_enviar = self.driver.find_elements(By.CSS_SELECTOR, "button.submitIntend")
                            if btn_enviar:
                                self.driver.execute_script("arguments[0].click();", btn_enviar[0])
                                time.sleep(3)
                                try: self.driver.switch_to.alert.accept()
                                except: pass
                    except: pass

                    try:
                        botoes_inserir = self.driver.find_elements(By.ID, "quote_response_submit")
                        if botoes_inserir and botoes_inserir[0].is_displayed():
                            self.driver.execute_script("arguments[0].click();", botoes_inserir[0])
                            time.sleep(4)
                    except: pass

                    self.garantir_logado(url)
                    self.driver.switch_to.default_content()
                    try: self.driver.switch_to.frame(self.driver.find_element(By.TAG_NAME, "iframe"))
                    except: pass
                    linhas = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")

                if len(linhas) > 0:
                    itens, tem_anexo = self._extrair_itens_linhas(linhas)
                    if tem_anexo: tem_anexo_geral = True
                else:
                    itens = []

                texto_tela = ""
                try: texto_tela = self.driver.find_element(By.TAG_NAME, "body").text
                except: pass

                valido, motivo, tem_eq = validar_itens_evento(itens, texto_tela)
                if tem_eq:
                    teve_expected_quantity = True

                if valido:
                    logger.info(f"    ✅ Sucesso: {len(itens)} item(ns) identificados e validados no evento {num}!")
                    itens_validados = True
                    break
                else:
                    ultimo_motivo_invalido = motivo
                    logger.warning(f"    ⚠️ Falha na validação do evento {num} (Tentativa {tentativa}/{MAX_TENTATIVAS_ITENS}): {motivo}")

            if not itens_validados:
                logger.error(f"   ❌ FALHA DEFINITIVA: Evento {num} persistiu sem itens válidos após {MAX_TENTATIVAS_ITENS} tentativas! ({ultimo_motivo_invalido})")
                self.relatorio_email_stats.setdefault('eventos_sem_itens', []).append(num)
                if teve_expected_quantity:
                    self.relatorio_email_stats.setdefault('eventos_expected_quantity', []).append(num)

                try:
                    self.enviar_relatorio_email()
                except Exception as e_mail:
                    logger.warning(f"Erro ao disparar e-mail de alerta para evento {num}: {e_mail}")

                self.driver.switch_to.default_content()
                return "SKIPPED"

                        # REFORÇO OBRIGATÓRIO DA DATA DE VENCIMENTO (Aba Informações)
            # ==========================================================
            logger.info("       🔍 Confirmando a data de vencimento exata na aba de Informações...")
            try:
                xpath_info = "//a[contains(@class, 'button') and .//span[contains(text(), 'Informa') and contains(text(), 'evento')]]"
                btn_info = self.driver.find_elements(By.XPATH, xpath_info)
                
                if btn_info:
                    self.driver.execute_script("arguments[0].click();", btn_info[0])
                    time.sleep(2) 

                venc_info = self.capturar_end_time_elemento()
                if venc_info:
                    vencimento = venc_info
                        
                logger.info(f"       ✅ Data de vencimento confirmada pelo sistema: {vencimento}")
            except Exception as e:
                logger.warning(f"       ⚠️ Não foi possível forçar a aba de Informações: {e}. Mantendo data anterior: {vencimento}")

            # Se ainda nao tem data valida confirmada, usa a data da tabela do painel (se disponivel)
            if vencimento in ["N/A", ""] and venc_tabela:
                parsed_tabela = self.parsear_data_vencimento(venc_tabela)
                if parsed_tabela:
                    vencimento = parsed_tabela
                    logger.info(f"       ✅ Data de vencimento obtida da tabela do painel: {vencimento}")

            # O frete vem do Incoterms lido nos itens expandidos; a página (já na aba de informações) é só reserva
            frete_pagina = ""
            try: frete_pagina = self.driver.find_element(By.CSS_SELECTOR, "p.s-selectField").text
            except: pass
            frete = self._frete_do_evento(itens, frete_pagina)

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

    # 5. RESPONDER EVENTO: herdado de RespostaMixin (bots/coupa/mixins/resposta.py)
    # Código único que responde tanto Vale quanto Vale Base Metals.

    @staticmethod
    def _gravar_anexo(pasta, f_data):
        # basename: o nome vem do portal e não pode apontar para fora da pasta temporária
        nome = os.path.basename(str(f_data.get('name') or '').replace('\\', '/'))
        nome = re.sub(r'[<>:"|?*\x00-\x1f]', '_', nome).strip(' .')   # caracteres que o Windows recusa
        caminho = os.path.join(pasta, nome or 'anexo.pdf')
        n = 1
        while os.path.exists(caminho):   # datasheet e DAV com o mesmo nome não se sobrescrevem
            caminho = os.path.join(pasta, f"{n}_{nome or 'anexo.pdf'}")
            n += 1
        with open(caminho, 'wb') as out:
            out.write(base64.b64decode(f_data['data']))
        return caminho

    def _marcar_rascunho(self, evento):
        try:
            from planilha_manager import planilha_manager
            if planilha_manager.marcar_rascunho(evento):
                logger.info(f"   📝 Cotação {evento} marcada como RASCUNHO no banco.")
        except Exception:
            logger.exception(f"Rascunho salvo no portal, mas não deu para marcar {evento} como RASCUNHO no banco")


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
                    m = re.search(r'Ultimo Vendedor:\s*([^\(]+)', match_hist)
                    if m: vendedores_deste_evento.add(m.group(1).strip())
                try:
                    from bots.classificador_tecnologia import analisar_item_tecnologia
                except ImportError:
                    from classificador_tecnologia import analisar_item_tecnologia
                analisar_item_tecnologia(it, txt_completo, dados_ia)
            try:
                from bots.classificador_tecnologia import agrupar_itens_equivalentes
            except ImportError:
                from classificador_tecnologia import agrupar_itens_equivalentes
            agrupar_itens_equivalentes(ev.get('itens', []))  # ONDE ENCONTRAR? sem repetir produto igual no evento
            
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
        
        try:
            from bots.itens_dell import evento_tem_dell, adicionar_mensagem_dell, EVENTOS_POR_WORD_DELL
            from bots.itens_hardware import evento_tem_hardware, adicionar_mensagem_hardware, lotes_hardware, chave_semelhanca
        except ImportError:
            from itens_dell import evento_tem_dell, adicionar_mensagem_dell, EVENTOS_POR_WORD_DELL
            from itens_hardware import evento_tem_hardware, adicionar_mensagem_hardware, lotes_hardware, chave_semelhanca

        bloqs = []
        complexos = []
        dells = []
        hardwares = []
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

            # Itens da marca DELL: Words próprios, 3 eventos por arquivo, com a orientação Dell no final
            if evento_tem_dell(d):
                dells.append(d)
                continue

            # Informática/hardware/TV/tecnologia: Words próprios, no máximo 3 eventos, marcas/modelos parecidos juntos
            if evento_tem_hardware(d):
                hardwares.append(d)
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

        # Dell: 3 eventos por arquivo, orientação de onde cotar Dell no final de cada arquivo
        dells.sort(key=chave_semelhanca)   # modelos Dell parecidos no mesmo arquivo
        for i in range(0, len(dells), EVENTOS_POR_WORD_DELL):
            lote_dell = dells[i:i + EVENTOS_POR_WORD_DELL]
            self.salvar_doc(lote_dell, self.PASTA_RELATORIOS, "DELL_Eventos", mensagem_final=lambda doc: adicionar_mensagem_dell(doc, getattr(self, 'config', None)))
            validos_para_excel.extend(lote_dell)

        # Hardware: no máximo 3 eventos por arquivo, lista de fornecedores de hardware no final de cada arquivo
        for lote_hw in lotes_hardware(hardwares):
            self.salvar_doc(lote_hw, self.PASTA_RELATORIOS, "HARDWARE_Eventos", mensagem_final=adicionar_mensagem_hardware)
            validos_para_excel.extend(lote_hw)

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
                        "respondido": False, "extraido": True, 
                        "vencimento_tabela": venc_tab
                    }
                    ambiente.acrescentar_jsonl(self.ARQUIVO_JSONL, novo)
                    self.db_eventos[d['numero_evento']] = novo
                except: pass
            
        self.dados_processados_lote.clear()
        logger.info("✅ Lote de Word finalizado.")

    def salvar_doc(self, lista, pasta, prefixo, mensagem_final=None):
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
                    # Frete do item só aparece na linha quando difere do cabeçalho (FRETE: do evento)
                    frete_item = str(item.get('frete') or '').strip().upper()
                    sufixo_frete = f" - FRETE: {frete_item}" if frete_item and frete_item != str(ev.get('tipo_frete', '')).strip().upper() else ""
                    p_qtd.add_run(f" {item['quantidade']} - LOCAL: {item['local_entrega']}{sufixo_frete}")
                    
                    doc.add_paragraph("VALOR DE VENDA: R$ __________________ CUSTO UNIT: R$__________________")
                    
                    if item.get('historico_match'):
                        p_hist = doc.add_paragraph()
                        p_hist.add_run(item['historico_match']).bold = True
                    if item.get('onde_encontrar'):
                        try:
                            from bots.classificador_tecnologia import adicionar_paragrafo_onde_encontrar
                        except ImportError:
                            from classificador_tecnologia import adicionar_paragrafo_onde_encontrar
                        adicionar_paragrafo_onde_encontrar(doc, item)

            if mensagem_final:
                mensagem_final(doc)   # ex.: orientação "Itens Dell" no final do arquivo

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
            
        if not json_path.endswith('Banco-de-dados'):
            json_path = os.path.join(json_path, 'Banco-de-dados')
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

        # ETAPA 2: Comparação de Itens com as regras de prioridade:
        # 1. Prioridade principal: ITEM TEXT (texto do item).
        # 2. Exceção DESCRIÇÃO: apenas se NÃO houver ITEM TEXT no evento.
        # 3. Exceção Marca/Modelo: se marca e modelo foram encontrados na DESCRIÇÃO na busca prévia, a DESCRIÇÃO tem prioridade.
        # Regra Crítica: Em todos os outros casos, comparação baseada EXCLUSIVAMENTE no ITEM TEXT.
        alvos_rotulados = selecionar_alvos_comparacao_item(item_dict, dados_ia)
        for desc_alvo, rotulo in alvos_rotulados:
            res = buscar_por_similaridade([desc_alvo], rotulo)
            if res:
                return res
        
        logger.info(f"   ? Nenhum vendedor similar encontrado no historico para este item.")
        return None

    def extrair_dados_gemini(self, texto_item):
        """Pipeline em 3 níveis: 1º Banco de Dados -> 2º Regex -> 3º Claude Structured Outputs (com fallback Gemini/Groq)."""
        try:
            from bots.extrator_item import extrair_dados_item_hibrido
        except ImportError:
            from extrator_item import extrair_dados_item_hibrido
        pasta_db = getattr(self, 'PASTA_DATABASE', getattr(self, 'config', {}).get('pasta_database', None))
        if not isinstance(pasta_db, str):
            pasta_db = None
        return extrair_dados_item_hibrido(
            texto_item,
            caminho_json=pasta_db,
            fallback_ia_fn=self._extrair_dados_gemini_legado
        )

    def _extrair_dados_gemini_legado(self, texto_item):
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

    def _localidade_com_frete(self, loc, frete):
        """Coluna LOCALIDADE da planilha: 'Cidade - UF (CIF)'. O frete entra na mesma coluna para manter as 9 colunas."""
        loc = str(loc or "").strip()
        frete = str(frete or "").strip().upper()
        if not frete or frete.lower() in ('nenhum', 'none', 'n/a'):
            return loc
        return f"{loc} ({frete})" if loc else frete

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
                
                # O vendedor recomendado e inserido apenas no Word e no email; nao deve ser salvo no banco
                vendedor_str = ""
                raw_venc = str(ev.get('data_vencimento', '')).split(' - ')[0].strip()
                data_venc_salvar = "" if raw_venc in ['N/A', 'None', ''] else raw_venc

                vals = [ev['numero_evento'], data_venc_salvar, "\n".join(d_agg), "\n".join(q_agg), self._localidade_com_frete(ev['itens'][0]['local_entrega'], ev.get('tipo_frete', '')), vendedor_str, "\n".join(mo_agg), "\n".join(ma_agg), ""]
                linhas_inserir.append(vals)
            
                cnt += 1
        
            if not planilha_manager.adicionar_linhas(self.NOME_ABA_ALVO, linhas_inserir):
                raise RuntimeError("Falha ao salvar cotacoes; consulte logs/gerenciador.log.")
            if linhas_inserir:
                from eventos import registrar
                numeros = [str(l[0]) for l in linhas_inserir]
                registrar('vale', 'cotacao_nova', 'ok', f"{len(numeros)} cotação(ões) nova(s) da Vale",
                          ref=numeros[0], detalhe={'cotacoes': numeros[:20]})
        
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
        """Lê a coluna de respostas enviadas (precisa do OTP) e aplica planilha_manager.novo_status nas cotações da Vale.
        Sem OTP em OTP_TIMEOUT_S, pula e registra o motivo (não trava o ciclo)."""
        logger.info("--- VERIFICAÇÃO DE EVENTOS VALE ---")
        self.solicitacao_parada = False   # parada/login esgotado da extração anterior não cancela a verificação
        try:
            self.driver.execute_script("window.open('');")
            self.driver.switch_to.window(self.driver.window_handles[-1])
            self.driver.get(self.config.get('vale_url_verificacao', VALE_URL_VERIFICACAO_PADRAO))
            time.sleep(3)
            if not self._esperar_otp():
                return

            logger.info(" OTP recebido. Injetando no portal...")
            campo_otp = self.wait.until(EC.presence_of_element_located((By.ID, "supplier_otp")))
            campo_otp.clear()
            campo_otp.send_keys(self.otp_atual)
            campo_otp.send_keys(Keys.RETURN)
            time.sleep(4)
            self.wait.until(EC.element_to_be_clickable((By.ID, "home"))).click()
            time.sleep(4)

            pendentes = self._pendentes_vale()
            logger.info(f" Verificando {len(pendentes)} cotações pendentes da Vale...")
            lidos, ilegiveis, paginas, erro = self._ler_lista_vale(pendentes)

            # Só números deste robô: um número do Coupa igual ao de um evento da Vale não é tocado
            vistos = {n: q for n, q in lidos.items() if n in self.db_eventos}
            from planilha_manager import planilha_manager
            mudancas = planilha_manager.atualizar_status_respostas(self.NOME_ABA_ALVO, vistos, 1, 9)
            if erro:
                raise erro   # o que foi lido já está gravado

            achadas = sum(1 for n in pendentes if n in vistos)
            relatorio = self._relatorio_nao_encontradas_vale(pendentes, vistos)
            resumo = (f"Verificação Vale: {len(vistos)} eventos lidos em {paginas} página(s), {achadas}/{len(pendentes)} "
                      f"pendentes encontradas, {len(mudancas)} status alterados, {ilegiveis} linhas ilegíveis")
            logger.info(f"✅ {resumo}" + (f". Não encontradas: {relatorio}" if relatorio else ""))
            import eventos
            eventos.registrar('vale', 'verificacao_concluida', 'ok', resumo, detalhe={'encontradas': achadas, 'total': len(pendentes), 'mudancas': len(mudancas), 'paginas': paginas})
            self.sio.emit('relatar_progresso_vale', {'mensagem': f"✅ {resumo}"})
            self.sio.emit('tarefa_concluida', {'evento': 'Verificação Vale', 'sucesso': True})

        except Exception as e:
            logger.exception(f" Erro na verificação: {e}")
            self.sio.emit('tarefa_concluida', {'evento': 'Verificação Vale', 'sucesso': False, 'erro': str(e)})

        finally:
            try:
                if len(self.driver.window_handles) > 1:
                    logger.info(" Fechando aba extra e retornando ao painel principal...")
                    self.driver.close()
                    self.driver.switch_to.window(self.driver.window_handles[0])
            except Exception as e:
                logger.error(f"Erro ao tentar restaurar abas: {e}")

    def _otp_do_email(self, pedido, ja_avisou):
        """(código, ja_avisou). O e-mail é só um atalho: se falhar, avisa uma vez e o painel continua valendo."""
        try:
            import otp_email
            return otp_email.buscar_codigo(pedido), ja_avisou
        except Exception as e:
            if not ja_avisou:
                logger.warning(f"Não deu para ler o OTP no e-mail ({type(e).__name__}: {e}). Digite o código no painel.")
            return None, True

    def _esperar_otp(self):
        self.otp_atual = None
        pedido = datetime.now().astimezone()   # só vale e-mail de código que chegou depois deste pedido
        self.sio.emit('pedir_otp_usuario')
        limite = time.time() + OTP_TIMEOUT_S
        proxima_leitura, email_falhou = time.time() + OTP_EMAIL_INTERVALO_S, False
        logger.info("Robô pausado. Aguardando o OTP (no painel ou no e-mail)...")
        while self.otp_atual is None:
            if time.time() >= proxima_leitura:
                proxima_leitura = time.time() + OTP_EMAIL_INTERVALO_S
                codigo, email_falhou = self._otp_do_email(pedido, email_falhou)
                if codigo and self.otp_atual is None:
                    self.otp_atual = codigo
                    logger.info(" OTP lido do e-mail automaticamente.")
                    self.sio.emit('relatar_progresso_vale', {'mensagem': "🔐 Código lido do e-mail automaticamente (pode fechar a janela do OTP)."})
                    break
            if getattr(self, 'solicitacao_parada', False):
                raise Exception("Processo cancelado pelo utilizador durante a espera do OTP.")
            if time.time() > limite:
                msg = f"Verificação da Vale pulada: OTP não informado em {OTP_TIMEOUT_S // 60} min."
                logger.warning(msg)
                try:
                    import eventos
                    eventos.registrar('vale', 'verificacao', 'aviso', msg)
                except Exception:
                    logger.exception('Falha ao registrar o evento da verificação pulada')
                self.sio.emit('tarefa_concluida', {'evento': 'Verificação Vale', 'sucesso': False, 'erro': msg})
                return False
            time.sleep(1)
        return True

    def _pendentes_vale(self):
        """Cotações deste robô (as do eventos_nimbi.jsonl) sem resposta confirmada: vazias, RASCUNHO e
        NÃO RESPONDIDO que venceram há poucos dias."""
        from planilha_manager import planilha_manager, status_atual, data_vencimento, RASCUNHO, NAO_RESPONDIDO
        planilha_manager.iniciar()
        limite = datetime.now().date() - timedelta(days=JANELA_NAO_RESPONDIDO_DIAS)

        def alvo(r):
            s = status_atual(r.get('RESPOSTA'))
            d = data_vencimento(r.get('VENCIMENTO'))
            return s in ('', RASCUNHO) or (s == NAO_RESPONDIDO and d is not None and d >= limite)
        return {str(r.get('COTAÇÃO')).strip(): r for r in planilha_manager.cotacoes.get('COTAÇÃO', [])
                if isinstance(r, dict) and str(r.get('COTAÇÃO') or '').strip() in self.db_eventos and alvo(r)}

    def _ler_lista_vale(self, pendentes):
        """{número: respostas} (None = célula ilegível). Se o portal falhar no meio, devolve o que já leu e o erro."""
        vistos, ilegiveis, paginas = {}, 0, 0
        ordem = lambda q: -1 if q is None else q   # número repetido: vale a maior quantidade lida
        from ambiente import paginas_verificacao
        max_paginas = paginas_verificacao('vale')   # CONFIG.json, editável no painel (padrão 60; 15 linhas por página)
        try:
            while paginas < max_paginas:
                if getattr(self, 'solicitacao_parada', False):
                    raise Exception("Processo cancelado pelo utilizador.")
                time.sleep(2)
                # coupa_datatable_row é a tela antiga; a extração já usa coupaTable__row
                linhas = self.driver.find_elements(By.XPATH, "//tr[contains(@class, 'coupa_datatable_row') or contains(@class, 'coupaTable__row')]")
                for _ in range(3 if paginas == 0 else 1):   # página lenta não é "fim da lista": espera de novo antes de desistir
                    if linhas:
                        break
                    time.sleep(5)
                    linhas = self.driver.find_elements(By.XPATH, "//tr[contains(@class, 'coupa_datatable_row') or contains(@class, 'coupaTable__row')]")
                if not linhas:
                    if paginas == 0:
                        raise Exception("A lista de eventos da Vale não carregou.")
                    break
                paginas += 1
                for linha in linhas:
                    try:
                        num = linha.find_element(By.XPATH, ".//span[contains(@class, 'dt_open_link')]").text.strip()
                    except Exception:
                        ilegiveis += 1
                        continue
                    try:
                        texto = linha.find_element(By.XPATH, ".//td[contains(@class, 's-datatable-cell-num_responses')]").text.strip()
                        qtd = int(texto) if texto.isdigit() else None
                    except Exception:
                        qtd = None
                    if num and (num not in vistos or ordem(qtd) > ordem(vistos[num])):
                        vistos[num] = qtd
                faltam = sum(1 for n in pendentes if n not in vistos)
                logger.info(f"    Página {paginas}: {len(linhas)} linhas, faltam {faltam} pendentes.")
                if not faltam:
                    break
                try:
                    botao = self.driver.find_element(By.XPATH, "//a[contains(@class, 'next_page') and contains(text(), 'Avançar')]")
                except Exception:
                    logger.info("✅ Fim das páginas atingido.")
                    break
                self.driver.execute_script("arguments[0].click();", botao)
                time.sleep(3)
        except Exception as e:
            logger.exception(f"Leitura interrompida na página {paginas}; gravando o que já foi lido.")
            return vistos, ilegiveis, paginas, e
        return vistos, ilegiveis, paginas, None

    def _relatorio_nao_encontradas_vale(self, pendentes, vistos):
        faltando = [r for n, r in pendentes.items() if n not in vistos]
        pasta = os.path.join(ambiente.RAIZ, 'relatorios')
        caminho = os.path.join(pasta, f"verificacao_vale_{datetime.now():%Y-%m-%d}.csv")
        if not faltando:
            if os.path.exists(caminho):
                os.remove(caminho)   # relatório de uma rodada anterior do dia não vale mais
            return None
        os.makedirs(pasta, exist_ok=True)
        with open(caminho, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(['COTAÇÃO', 'VENCIMENTO', 'RESPOSTA', 'VENDEDOR'])
            w.writerows([r.get('COTAÇÃO'), r.get('VENCIMENTO'), r.get('RESPOSTA') or '', r.get('VENDEDOR')] for r in faltando)
        return caminho

