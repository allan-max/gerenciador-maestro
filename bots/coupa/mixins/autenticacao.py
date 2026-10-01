# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
Módulo Especialista: Coupa Enterprise (Trabalhador do Maestro)
Autor: Allan Simão
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
import random
import traceback
from datetime import datetime
from typing import List, Dict, Set, Optional, Any
from google import genai
from groq import Groq

import pandas as pd
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Alignment, Border, Side, PatternFill

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoAlertPresentException, NoSuchElementException
from selenium.webdriver.common.action_chains import ActionChains

from docx import Document
from docx.shared import Pt, RGBColor, Cm

import base64
from io import BytesIO
from PIL import Image
import requests

logger = logging.getLogger("CoupaApp")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')

if not logger.handlers:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


class AutenticacaoMixin:
    def setup_driver(self):
        logger.info("🚗 Configurando Navegador...")
        ops = Options()
        ops.add_argument("--no-sandbox")
        ops.add_argument("--disable-dev-shm-usage")
        ops.add_argument("--window-size=1920,1080")
        ops.add_argument("--start-maximized")
        self.driver = webdriver.Chrome(options=ops)
        self.driver.set_page_load_timeout(60)
        self.wait = WebDriverWait(self.driver, 40)

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
                        self.sio.emit('relatar_progresso_coupa', {'mensagem': '❌ CAPTCHA EXPIRADO! (5 min sem solução). Fechando o robô.'})
                        raise Exception("CAPTCHA_TIMEOUT")
                        
                    elemento_alvo = iframe_desafio if (iframe_desafio and iframe_desafio.is_displayed()) else iframe_caixinha
                    if elemento_alvo:
                        tempo_atual = time.time()
                        if tempo_atual - ultimo_print > 2.0: # Manda print a cada 2 segundos em vez de metralhar
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
                    # MUDANÇA: Adicionado o id='login_button' na varredura
                    botoes = self.driver.find_elements(By.XPATH, "//button[@type='submit' or @id='login-submit' or @id='login_button']")
                    for btn in botoes:
                        if btn.is_displayed() or self.driver.execute_script("return arguments[0].offsetWidth > 0;", btn):
                            self.driver.execute_script("arguments[0].click();", btn)
                            ultimo_clique_botao = time.time()
                            break
                    time.sleep(3.0) 
                    continue 

                campos_email = self.driver.find_elements(By.XPATH, "//input[@type='email' or @id='email' or @name='email' or @id='username']")
                email_encontrado_e_vazio = False
                
                for campo in campos_email:
                    visivel = campo.is_displayed()
                    if not visivel: visivel = self.driver.execute_script("return (arguments[0].offsetWidth > 0 || arguments[0].offsetHeight > 0);", campo)
                    valor_atual = self.driver.execute_script("return arguments[0].value;", campo) or ""
                    
                    if visivel and valor_atual.strip() == "":
                        logger.info(" Injetando E-mail...")
                        try: campo.clear(); campo.send_keys(email)
                        except: self.driver.execute_script("arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", campo, email)
                        email_encontrado_e_vazio = True
                        break
                        
                if email_encontrado_e_vazio:
                    # MUDANÇA: Adicionado o id='login_button' na varredura
                    botoes = self.driver.find_elements(By.XPATH, "//button[@type='submit' or @id='login-submit' or @id='login_button']")
                    for btn in botoes:
                        if btn.is_displayed() or self.driver.execute_script("return arguments[0].offsetWidth > 0;", btn):
                            self.driver.execute_script("arguments[0].click();", btn)
                            ultimo_clique_botao = time.time()
                            break
                    time.sleep(3.0) 
                    continue 

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
                    # MUDANÇA: Adicionado o id='login_button' na varredura
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
            
            # Verifica campos de login no frame atual
            campos_email = self.driver.find_elements(By.XPATH, "//input[@type='email' or @id='email' or @name='email' or @id='username']")
            for campo in campos_email:
                if campo.is_displayed():
                    valor_atual = self.driver.execute_script("return arguments[0].value;", campo) or ""
                    if valor_atual.strip() == "":
                        email_vazio_na_tela = True
                        break

            # Se não achou, verifica no contexto principal (default_content)
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

            # 👇 AGORA ELE TAMBÉM RECUPERA SE DER O ERRO 'OOPS' 👇
            sessao_expirada = email_vazio_na_tela or \
                              self.driver.find_elements(By.CSS_SELECTOR, "div.login_message") or \
                              "sessions/new" in self.driver.current_url or \
                              self._verificar_tela_erro() # <--- VERIFICA O OOPS AQUI TAMBÉM

            if sessao_expirada:
                logger.warning("🔄 Sessão inválida ou erro 'Oops' detectado. Re-logando...")
                self.driver.switch_to.default_content()
                self.fazer_login_hibrido()
                
                logger.info("🔥 Refazendo aquecimento pós-recuperação...")
                self.navegacao_aquecimento()
                
                if url_origem: 
                    self.driver.get(url_origem)
                    time.sleep(3)
                    try:
                        self.driver.switch_to.frame(self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe"))))
                    except: pass
        except Exception: 
            pass
    

    def navegacao_aquecimento(self):
        try:
            logger.info(" Iniciando Aquecimento Humano...")
            urls = [
                f"{self.BASE_SUPPLIER_URL}/profile/business_profile",
                f"{self.BASE_SUPPLIER_URL}/orders/",
                f"{self.BASE_SUPPLIER_URL}/advanced_ship_notices/"
            ]
            random.shuffle(urls) 
            for url in urls:
                logger.info(f"   ... Visitando: {url.split('/')[-1]}")
                self.driver.get(url)
                time.sleep(random.randint(5, 8))
        except: pass

    def _verificar_tela_erro(self):
        """Verifica se o portal crashou na tela 'Oops! Algo inesperado aconteceu.'"""
        try:
            erros = self.driver.find_elements(By.XPATH, "//h1[@id='error-message' or contains(text(), 'Oops!')]")
            return len(erros) > 0
        except:
            return False

    def iniciar_e_logar(self):
        logger.info(" INICIANDO ROBO” (FASE 1: PREPARAÇãO E LOGIN)...")
        self.verificar_acesso_pastas()
        
        # 1. Carrega todas as memórias e bases de dados PRIMEIRO
        self.criar_backup_inicial()
        self.carregar_banco_dados()
        self.carregar_memoria_part_numbers()
        self.aprender_padroes_historico()
        self.sincronizar_base_de_marcas()
        
        # 2. Abre o navegador APENAS UMA VEZ e faz o login
        try:
            self.setup_driver()
            if self.fazer_login_hibrido():
                logger.info("✅ Login concluído! Robô pronto para receber tarefas da nuvem.")
                
                # ðŸ‘‡ ESTA É A LINHA MãGICA QUE DESTRANCA O BOTãO NO SITE NA HORA ðŸ‘‡
                self.sio.emit('tarefa_concluida', {'evento': 'Login do Robo', 'sucesso': True})
                self.is_ready = True
            else:
                self.sio.emit('tarefa_concluida', {'evento': 'Login do Robo', 'sucesso': False, 'erro': 'Falha na validação do login.'})
        except Exception as e:
            if 'wb' in locals() and wb:
                try: wb.close()
                except: pass
            logger.error(f" Erro Crítico no Login: {e}")
            traceback.print_exc()
            self.sio.emit('tarefa_concluida', {'evento': 'Login do Robô', 'sucesso': False, 'erro': str(e)})

