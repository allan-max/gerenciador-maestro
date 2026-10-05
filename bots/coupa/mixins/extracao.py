# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
Módulo Especialista: Coupa Enterprise (Trabalhador do Maestro)
Autor: Allan Simão
"""
import os
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

logger = logging.getLogger("CoupaApp")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')

if not logger.handlers:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


class ExtracaoMixin:
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

    def extrair_dados_do_evento(self, url: str, num: str, ja_respondido: bool = False, is_negotiation: bool = False, re_extrair: bool = False, venc_tabela: str = "") -> Optional[Dict]:
        if self.evento_ja_processado(num) and not re_extrair:
            logger.info(f"⏭️ Evento {num} já existe e a data não mudou. Pulando.")
            return "SKIPPED"
            
        msg_extra = " (REABERTO POR MUDANÇA DE DATA)" if re_extrair else ""
        logger.info(f"🎯 Extraindo Evento: {num}{msg_extra}")
        try:
            sucesso_carregamento = False
            for tentativa in range(3):
                self.driver.get(url) 
                time.sleep(3)
                try: 
                    self.driver.switch_to.alert.accept()
                    time.sleep(1)
                except NoAlertPresentException: pass
                
                if self._verificar_tela_erro():
                    logger.warning(f"⚠️ Tela 'Oops!' detectada no evento {num}. Iniciando re-login forçado...")
                    
                    # 1. Volta ao link inicial e faz o login de novo
                    self.fazer_login_hibrido() 
                    
                    # 2. Pequena pausa para estabilizar
                    time.sleep(2) 
                    
                    # 3. Busca o novo link do evento na listagem
                    novo_url = self._obter_novo_link_evento(num)
                    if novo_url:
                        logger.info(f"✅ Novo link encontrado para o evento {num}: {novo_url}")
                        url = novo_url
                    else:
                        logger.error(f"❌ Não foi possível encontrar um novo link para o evento {num}. Mantendo o antigo...")
                    
                    # 4. O 'continue' faz o robô tentar o 'self.driver.get(url)' novamente (agora com o link novo se encontrado)
                    continue 
                else:
                    sucesso_carregamento = True
                    break
                    
            if not sucesso_carregamento:
                logger.error(f" Erro persistente (Oops!) no evento {num}. Ignorando.")
                return "SKIPPED"

            try: self.driver.switch_to.frame(self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe"))))
            except: pass

            try:
                logger.info("    Aguardando a interface do Coupa carregar...")
                WebDriverWait(self.driver, 15).until(lambda d: 
                    d.find_elements(By.ID, "participation") or
                    d.find_elements(By.XPATH, "//a[contains(., 'VENTURA COMERCIO VAREJISTA')]") or
                    d.find_elements(By.XPATH, "//a[.//span[text()='Minhas respostas']]") or
                    d.find_elements(By.XPATH, "//a[.//span[text()='Minha resposta']]") or
                    d.find_elements(By.ID, "quote_response_submit") or
                    d.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")
                )
            except Exception: pass

            if self.driver.find_elements(By.XPATH, "//h1[contains(text(), 'já terminou')]"):
                logger.warning("    Evento encerrado."); self.driver.switch_to.default_content(); return "SKIPPED"

            req = "N/A"
            try: 
                titulo_h1 = self.driver.find_element(By.CSS_SELECTOR, "h1.sourcing-wrapper-title").text
                if "NEGOCIAÇãO" in titulo_h1.upper(): 
                    is_negotiation = True
                match_req = re.search(r'(?i)req[^\d]*(\d+)', titulo_h1)
                if match_req:
                    req = match_req.group(1)
            except: pass

            vencimento = venc_tabela if venc_tabela else "N/A"
            frete = "EXW"

            # 1. ACESSO DIRETO (PULANDO O 'PRETENDO' SE NãO FOR NEGOCIAÇãO)
            entrou_nos_itens = False
            
            if not is_negotiation:
                xpath_botoes_acesso = [
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
                            logger.info(f"    Resposta detectada! Pulando o 'Pretendo'.")
                            self.driver.execute_script("arguments[0].click();", b)
                            time.sleep(3)
                            entrou_nos_itens = True
                            break
                    if entrou_nos_itens: break
            else:
                logger.info("    Rodada de Negociação detectada! Forçando novo aceite...")

            # 2. SE É NEGOCIAÇãO OU NãO TEM RASCUNHO, FAZ O ACEITE
            if not entrou_nos_itens:
                try:
                    # ACEITAR TERMOS DE PARTICIPACAO
                    try:
                        # 1. Tenta os radios ocultos com valor 'true'
                        termos = self.driver.find_elements(By.CSS_SELECTOR, "input[type='radio'][value='true'].s-termAccept")
                        if termos:
                            logger.info(f"    Aceitando {len(termos)} termo(s) de condição (radio)...")
                            for termo in termos:
                                self.driver.execute_script("arguments[0].click();", termo)
                        
                        # 2. Busca qualquer label ou botão genérico que esteja escrito "SIM"
                        js_clicar_sim = """
                        var elementos = Array.from(document.querySelectorAll('label, button, span'));
                        var clicados = 0;
                        elementos.forEach(function(el) {
                            if(el.innerText && el.innerText.trim().toUpperCase() === 'SIM') {
                                el.click();
                                clicados++;
                            }
                        });
                        return clicados;
                        """
                        qtd_sim = self.driver.execute_script(js_clicar_sim)
                        if qtd_sim > 0:
                            logger.info(f"    Forçou o clique em {qtd_sim} elemento(s) escrito 'SIM'.")
                    except Exception as e:
                        logger.warning(f"    Erro ao aceitar termos: {e}")

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
                    
                # Pegar a data
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
                        venc_str = f"{int(dia):02d}/{m:02d}/{y} - {hora}"
                        if "Error" not in venc_str: vencimento = venc_str
                except Exception: pass

                # Clicar Inserir resposta (No fluxo normal)
                try:
                    btn_inserir = self.driver.find_elements(By.ID, "quote_response_submit")
                    if btn_inserir and btn_inserir[0].is_displayed():
                        logger.info("   📥 Clicando em Inserir resposta...")
                        self.driver.execute_script("arguments[0].click();", btn_inserir[0])
                        time.sleep(3)
                except Exception: pass

            # 3. VERIFICAÇãO DE SEGURANÇA (0 ITENS) E RECUPERAÇãO BLINDADA
            linhas = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")
            
            if len(linhas) == 0:
                logger.warning("   ⚠️ ZERO itens na tela. Iniciando Loop de Recuperação Rigoroso...")

                # Aumentamos para 3 tentativas e forçamos o robô a ter paciência
                for tentativa_recuperacao in range(3): 
                    logger.info(f"      Tentativa de Recuperação {tentativa_recuperacao + 1}/3...")
                    
                    # O FREIO: Dá 4 segundos para o Coupa processar o JavaScript da tela
                    time.sleep(4) 
                    self.garantir_logado(url) # Verifica login no início da etapa
                    self.driver.switch_to.default_content()
                    try: self.driver.switch_to.frame(self.driver.find_element(By.TAG_NAME, "iframe"))
                    except: pass

                    # 3.1 Tentar botões de acesso direto/alternativo
                    xpath_fallback = [
                        "//a[contains(., 'VENTURA COMERCIO VAREJISTA')]",
                        "//span[contains(text(), 'VENTURA COMERCIO VAREJISTA')]",
                        "//a[.//span[text()='Minhas respostas']]",
                        "//span[text()='Minhas respostas']",
                        "//a[.//span[text()='Minha resposta']]",
                        "//a[.//span[text()='Criar resposta']]",
                        "//a[.//span[text()='Itens']]",
                        "//span[text()='Itens']",
                        "//a[contains(text(), 'Responder')]"
                    ]
                    
                    clicou_algo = False
                    for xpath in xpath_fallback:
                        try:
                            botoes = self.driver.find_elements(By.XPATH, xpath)
                            if botoes:
                                logger.info(f"         🔄 Encontrado botão de acesso escondido. Forçando clique...")
                                self.driver.execute_script("arguments[0].click();", botoes[0])
                                time.sleep(5) # Espera a página reagir ao clique
                                clicou_algo = True
                                break
                        except: pass

                    self.garantir_logado(url) # Verifica login após tentar botões
                    self.driver.switch_to.default_content()
                    try: self.driver.switch_to.frame(self.driver.find_element(By.TAG_NAME, "iframe"))
                    except: pass
                    linhas = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")
                    if len(linhas) > 0:
                        logger.info("         ✅ Itens encontrados via botão de acesso!")
                        break

                    # 3.2 Se não achou itens, FORÇA O FLUXO DO PRETENDO + INSERIR RESPOSTA
                    logger.info("         Ainda 0 itens. Forçando 'Pretendo Participar' -> 'Inserir Resposta'...")
                    try:
                        # ACEITAR TERMOS DE PARTICIPACAO
                    try:
                        # 1. Tenta os radios ocultos com valor 'true'
                        termos = self.driver.find_elements(By.CSS_SELECTOR, "input[type='radio'][value='true'].s-termAccept")
                        if termos:
                            logger.info(f"    Aceitando {len(termos)} termo(s) de condição (radio)...")
                            for termo in termos:
                                self.driver.execute_script("arguments[0].click();", termo)
                        
                        # 2. Busca qualquer label ou botão genérico que esteja escrito "SIM"
                        js_clicar_sim = """
                        var elementos = Array.from(document.querySelectorAll('label, button, span'));
                        var clicados = 0;
                        elementos.forEach(function(el) {
                            if(el.innerText && el.innerText.trim().toUpperCase() === 'SIM') {
                                el.click();
                                clicados++;
                            }
                        });
                        return clicados;
                        """
                        qtd_sim = self.driver.execute_script(js_clicar_sim)
                        if qtd_sim > 0:
                            logger.info(f"    Forçou o clique em {qtd_sim} elemento(s) escrito 'SIM'.")
                    except Exception as e:
                        logger.warning(f"    Erro ao aceitar termos: {e}")

                    dropdowns = self.driver.find_elements(By.ID, "participation")
                        if dropdowns:
                            logger.info("         ✅ Dropdown 'Pretendo' encontrado! Selecionando...")
                            try:
                                Select(dropdowns[0]).select_by_value("true")
                            except Exception:
                                self.driver.execute_script("arguments[0].value='true'; arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", dropdowns[0])
                            time.sleep(2)

                            btn_enviar = self.driver.find_elements(By.CSS_SELECTOR, "button.submitIntend")
                            if btn_enviar:
                                self.driver.execute_script("arguments[0].click();", btn_enviar[0])
                                time.sleep(4)
                                try: self.driver.switch_to.alert.accept()
                                except NoAlertPresentException: pass
                    except Exception as e:
                        logger.warning(f"         ⚠️ Erro ao forçar Pretendo: {e}")

                    # Clicar Inserir resposta ou Submit (Corrigido para aceitar a tag <input>)
                    try:
                        # O asterisco (*) faz o robô procurar em qualquer tag (input, button, a, span)
                        botoes_inserir = self.driver.find_elements(By.XPATH, "//*[@id='quote_response_submit' or @value='Inserir resposta' or contains(@class, 'submitResponse')]")
                        
                        if botoes_inserir:
                            logger.info("         📥 Clicando em 'Inserir resposta' / 'Submit'...")
                            self.driver.execute_script("arguments[0].click();", botoes_inserir[0])
                            time.sleep(6) # Dá um segundo extra para o Coupa renderizar a nova página
                    except Exception: pass

                    self.garantir_logado(url) # Verifica login após forçar pretendo
                    self.driver.switch_to.default_content()
                    try: self.driver.switch_to.frame(self.driver.find_element(By.TAG_NAME, "iframe"))
                    except: pass
                    linhas = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")
                    if len(linhas) > 0:
                        logger.info("         ✅ Itens encontrados após forçar o Pretendo e Inserir Resposta!")
                        break
                    
                    # 3.3 A TÁTICA NUCLEAR: F5 NA PÁGINA
                    # Se chegou até aqui e ainda tem 0 itens, a página do Coupa "crashou" visualmente.
                    if tentativa_recuperacao < 2:
                        logger.warning("         ⏳ Os itens continuam invisíveis. Dando F5 (Refresh) na página...")
                        self.driver.refresh()
                        time.sleep(8) # Dá tempo de sobra para a página recarregar do zero
                        try: self.driver.switch_to.alert.accept()
                        except: pass
                        # Tenta voltar pro frame principal se ele existir
                        try: self.driver.switch_to.frame(self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe"))))
                        except: pass

            # 🚨 TRAVA DE SEGURANÇA FINAL 🚨
            if len(linhas) == 0:
                logger.error(f"   ❌ FALHA: Evento {num} carregou com 0 itens após todas as tentativas de recuperação! Cancelando extração.")
                self.relatorio_email_stats.setdefault('eventos_sem_itens', []).append(num)
                self.driver.switch_to.default_content()
                return "SKIPPED"

            logger.info(f"    {len(linhas)} itens encontrados no evento {num}.")
            try: self.sio.emit('relatar_progresso', {'mensagem': f"📦 {len(linhas)} itens encontrados no evento {num}."})
            except: pass
            
            # 4. EXTRAÇãO DOS ITENS (COM BLINDAGEM DE QTD E LOCAL)
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
                
                logger.info(f"       Item {i+1}: {tit[:20]}... | Qtd: {qtd} | Local: {loc[:30]}...")
                itens.append({"item_num": i+1, "titulo": tit, "descricao": desc, "quantidade": qtd, "local_entrega": loc, "texto_item": texto_item_extra, "texto_compra": texto_compra_extra})

            # REFORÇO OBRIGATÓRIO DA DATA DE VENCIMENTO
            # Clica em "Informações do evento" para capturar a data final com 100% de certeza
            logger.info("       🔍 Confirmando a data de vencimento exata na aba de Informações...")
            try:
                # 1. Tenta clicar no botão "Informações do evento" usando uma busca à prova de falhas de acentuação
                xpath_info = "//a[contains(@class, 'button') and .//span[contains(text(), 'Informa') and contains(text(), 'evento')]]"
                btn_info = self.driver.find_elements(By.XPATH, xpath_info)
                
                if btn_info:
                    self.driver.execute_script("arguments[0].click();", btn_info[0])
                    time.sleep(2) # Dá tempo da aba carregar

                # 2. Localiza a div mestra que tem a data
                div_end_time = self.driver.find_elements(By.CSS_SELECTOR, "div.end_time")
                
                if div_end_time:
                    # Estratégia A: Tentar pegar a data blindada direto do atributo do sistema (ex: "2026-04-29 14:30:00 -0300")
                    data_attr = div_end_time[0].get_attribute("data-end-date")
                    if data_attr:
                        dt_obj = datetime.strptime(data_attr.split(' ')[0], "%Y-%m-%d")
                        hora_attr = data_attr.split(' ')[1][:5] # Pega os 5 primeiros chars da hora HH:MM
                        vencimento = f"{dt_obj.strftime('%d/%m/%Y')} - {hora_attr}"
                    else:
                        # Estratégia B: Pegar pelo texto visível (Month, Date, Time) conforme o seu HTML
                        mes = div_end_time[0].find_element(By.CLASS_NAME, "month").text.strip()
                        dia = div_end_time[0].find_element(By.CLASS_NAME, "date").text.strip()
                        hora = div_end_time[0].find_element(By.CSS_SELECTOR, "span.quotes_h1").text.strip()
                        
                        mapa = {'Jan':1,'Fev':2,'Mar':3,'Abr':4,'Mai':5,'Jun':6,'Jul':7,'Ago':8,'Set':9,'Out':10,'Nov':11,'Dez':12}
                        m = mapa.get(mes.title()[:3], 1)
                        y = datetime.now().year
                        if m < datetime.now().month: y += 1 # Se o mês já passou, joga pro ano que vem
                        
                        venc_str = f"{int(dia):02d}/{m:02d}/{y} - {hora}"
                        if "Error" not in venc_str: vencimento = venc_str
                        
                logger.info(f"       ✅ Data de vencimento confirmada pelo sistema: {vencimento}")
            except Exception as e:
                logger.warning(f"       ⚠️ Não foi possível forçar a aba de Informações: {e}. Mantendo data anterior.")

            try: frete = self.driver.find_element(By.CSS_SELECTOR, "p.s-selectField").text
            except: pass

            self.driver.switch_to.default_content()
            
            return {"numero_evento": num, "req_num": req, "data_vencimento": vencimento, "tipo_frete": frete, "itens": itens, "respondido_origem": ja_respondido, "tem_anexo": tem_anexo_geral}
            
        except Exception as e:
            logger.error(f" Erro extração {num}: {e}")
            try: self.driver.switch_to.default_content()
            except: pass
            return None
    
    def varrer_painel_eventos(self):
        logger.info("\n--- INICIANDO VARREDURA DE EVENTOS ---")
        curr = 1
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
            paginas_vazias_seguidas = 0
            while True:
                if self.solicitacao_parada:
                    logger.warning(" Parada Segura acionada! Encerrando paginação...")
                    break

                self.garantir_logado(self.EVENTS_URL)
                logger.info(f" Analisando Página {curr}...")
                if curr > 1: self.driver.get(f"{self.EVENTS_URL}?page={curr}")
                else: self.driver.get(self.EVENTS_URL)
                
                try: WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "table")))
                except: break
                
                rows = self.driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
                if not rows: break
                
                tarefas = []
                has_next = False
                try: 
                    if self.driver.find_element(By.CSS_SELECTOR, "a.next_page:not(.disabled)"): has_next = True
                except: pass
                
                for r in rows:
                    try:
                        num = r.find_element(By.CSS_SELECTOR, "a span.dt_open_link").text.strip()
                        url = r.find_element(By.XPATH, ".//a").get_attribute('href')
                        
                        is_neg = False
                        try:
                            desc = r.find_element(By.CLASS_NAME, "s-datatable-cell-description").text.upper()
                            if "EQUALIZAÇãO" in desc or "RODADA" in desc: is_neg = True
                        except: pass

                        data_venc_tabela = ""
                        try:
                            data_venc_tabela = r.find_element(By.CLASS_NAME, "s-datatable-cell-end_time").text.strip()
                        except: pass

                        re_extrair = False
                        if self.evento_ja_processado(num):
                            evento_salvo = self.db_eventos.get(num, {})
                            data_salva = evento_salvo.get("vencimento_tabela", "")
                            
                            # Se tem data na tela, tem data no banco, e elas são diferentes: É UMA SEGUNDA CHANCE!
                            if data_venc_tabela and data_salva and data_venc_tabela != data_salva:
                                logger.info(f"🔄 Evento {num} REABERTO! Data mudou de {data_salva} para {data_venc_tabela}.")
                                re_extrair = True
                            else:
                                continue # Pula o evento se já foi processado e a data continua igual
                        
                        tarefas.append({
                            'num': num, 
                            'url': url, 
                            'is_neg': is_neg, 
                            'venc_tabela': data_venc_tabela, 
                            're_extrair': re_extrair
                        })
                    except: continue
                
                logger.info(f"    Página {curr}: Encontrados {len(tarefas)} novos eventos.")
                
                if not tarefas:
                    paginas_vazias_seguidas += 1
                else:
                    paginas_vazias_seguidas = 0
                    
                if paginas_vazias_seguidas >= 3:
                    logger.info("3 páginas seguidas sem novos eventos encontradas. Parando extração de forma segura...")
                    self.sio.emit('relatar_progresso_coupa', {'mensagem': '🛑 3 páginas seguidas sem novos eventos. Encerrando paginação...'})
                    break

                self.sio.emit('relatar_progresso', {'mensagem': f"ðŸ”  Página {curr}: {len(tarefas)} eventos novos encontrados.", 'total': len(tarefas)})

                for idx, t in enumerate(tarefas):
                    if self.solicitacao_parada:
                        self.sio.emit('relatar_progresso_coupa', {'mensagem': '🛑 Paragem acionada. A finalizar o evento atual e a gerar ficheiros Word e Excel...'})
                        break
                    
                    txt_alerta = " (REABERTO)" if t.get('re_extrair') else ""
                    self.sio.emit('relatar_progresso', {'mensagem': f"🎯 A iniciar extração do evento {t['num']}{txt_alerta}...", 'atual': idx + 1})
                    
                    # Passa a flag re_extrair para a função de extração
                    res = self.extrair_dados_do_evento(t['url'], t['num'], is_negotiation=t.get('is_neg', False), re_extrair=t.get('re_extrair', False), venc_tabela=t.get('venc_tabela', ''))
                    
                    if res is None:
                        self.relatorio_email_stats['erros_extracao'].append(t['num'])
                    
                    if res and res != "SKIPPED":
                        # Embutimos a data da tabela para salvar no banco depois
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
                            if not loc or loc in ['n/a']: falt_info = True
                            
                        if res.get('data_vencimento', 'N/A') in ['N/A', '']: falt_info = True
                        if res.get('tipo_frete', 'N/A') in ['N/A', '']: falt_info = True
                        
                        if sem_desc: self.relatorio_email_stats['sem_descricao'].append(t['num'])
                        if falt_info: self.relatorio_email_stats['faltando_info'].append(t['num'])
                        if not res.get('itens'): self.relatorio_email_stats['eventos_sem_itens'].append(t['num'])
                # Gera os ficheiros Word para a página que acabou de ser lida
                if self.dados_processados_lote:
                    self.gerar_word()

                # Sai da paginação se o utilizador mandou parar
                if not has_next or self.solicitacao_parada: 
                    break
                curr += 1
                
            # 👇 O GRAN FINALE: Gera o Word e o Excel do evento que acabou de extrair! 👇
            if self.dados_processados_lote: 
                self.gerar_word()
            
            logger.info("Paragem/Extração concluída! Ficheiros Word e Excel guardados com sucesso.")
            self.enviar_relatorio_email()
            self.sio.emit('tarefa_concluida', {'evento': 'Lote de Extração', 'sucesso': True})

        except Exception as e:
            logger.error(f" Erro Crítico na varredura: {e}")
            if hasattr(self, 'relatorio_email_stats'):
                self.relatorio_email_stats['erro_critico_bot'] = str(e)
                self.enviar_relatorio_email()
            self.sio.emit('tarefa_concluida', {'evento': 'Lote de Extração', 'sucesso': False, 'erro': str(e)})

