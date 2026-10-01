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


class RespostaMixin:
    def responder_evento(self, dados):
        evento = str(dados.get('evento', '')).strip()
        lista_precos = dados.get('precos', [])
        lista_prazos = dados.get('prazos', [])
        lista_origens = dados.get('origens', [])
        lista_icms = dados.get('icms', [])
        
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
            # 1. ROTA DIRETA PARA SOURCING (À PROVA DE FALHAS)
            self.sio.emit('relatar_progresso', {'mensagem': f" Acedendo diretamente ã  página de Sourcing..."})
            
            # Navega diretamente pela URL em vez de tentar clicar no menu!
            url_sourcing = f"{self.BASE_SUPPLIER_URL}/quotes/private_events"
            self.driver.get(url_sourcing)
            time.sleep(3)
            self.garantir_logado(url_sourcing)

            # 1.5 DIGITAR O CLIENTE (VALE)
            logger.info(" Filtrando pelo cliente 'vale'...")
            self.sio.emit('relatar_progresso', {'mensagem': f" Filtrando eventos do cliente 'vale'..."})
            try:
                # Usa o campo de busca de clientes (ID customersList_1)
                input_cliente = self.wait.until(EC.presence_of_element_located((By.ID, "customersList_1")))
                input_cliente.clear()
                time.sleep(0.5)
                input_cliente.send_keys("vale")
                time.sleep(1)
                input_cliente.send_keys(Keys.ENTER) # Pressiona Enter após digitar
                time.sleep(3) # Aguarda 3 segundos pro site recarregar a tabela
                logger.info("   ✅ Filtro 'vale' aplicado com sucesso!")
            except Exception as e:
                logger.warning(f"    Não foi possível digitar 'vale' no campo de cliente: {e}")

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
                # 1. XPath ultra-preciso usando a classe e a estrutura HTML que você capturou
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
                # O Selenium precisa de ser instruído a "pular" para essa aba recém-aberta.
                if len(self.driver.window_handles) > 1:
                    self.driver.switch_to.window(self.driver.window_handles[-1])
                    logger.info("   🔄 Foco alterado para a aba do evento.")
                    
            except Exception as e:
                logger.warning(f"   ⚠️ Link não encontrado na tabela. Forçando acesso via URL (Nova Aba)... ({e})")
                # Se falhar o clique, abre o evento forçadamente numa ABA NOVA 
                # (Abrimos em nova aba via JS para não quebrar a lógica de fechar aba no final da função)
                self.driver.execute_script(f"window.open('{self.BASE_SUPPLIER_URL}/quotes/external_responses/{evento}/multi', '_blank');")
                time.sleep(3)
                self.driver.switch_to.window(self.driver.window_handles[-1])

            time.sleep(3)

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
                
                # A ORDEM DEFINE A PRIORIDADE: Ele tenta o n° 1 primeiro. Se falhar, vai para o 2.
                xpath_botoes = [
                    # 1ª PRIORIDADE: O seu Span/Link com o nome da empresa
                    "//a[contains(., 'VENTURA COMERCIO VAREJISTA')]",
                    "//span[contains(text(), 'VENTURA COMERCIO VAREJISTA')]",
                    
                    # 2ª PRIORIDADE: O botão plural "Minhas respostas" (com /multi)
                    "//a[.//span[text()='Minhas respostas']]",
                    "//span[text()='Minhas respostas']",
                    f"//a[contains(@href, '/quotes/external_responses/{evento}/multi')]",
                    
                    # 3ª PRIORIDADE (Rede de Segurança): Se for um evento 100% virgem
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
                        el = self.wait.until(EC.presence_of_element_located((By.ID, "participation")))
                        try:
                            Select(el).select_by_value("true")
                        except Exception:
                            self.driver.execute_script("arguments[0].value='true'; arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", el)
                        self.driver.find_element(By.CSS_SELECTOR, "button.submitIntend").click()
                        time.sleep(3)
                        try: 
                            self.driver.switch_to.alert.accept()
                        except Exception: 
                            pass
                        try:
                            self.wait.until(EC.element_to_be_clickable((By.ID, "quote_response_submit"))).click()
                        except:
                            pass
                        try:
                            self.driver.find_element(By.XPATH, "//a[.//span[text()='Itens']]").click()
                        except:
                            pass
                        logger.info("   ✅ Termos aceitos! Avançando para a resposta...")
                    except Exception as e:
                        raise Exception("A tela não tem botão de responder e não pediu aceite de termos. O evento pode estar encerrado ou indisponível.")

            except Exception as erro:
                raise Exception(f"Falha ao abrir a aba de preços: {str(erro)}")
                
            time.sleep(4)

            # 5. Anexar MÚLTIPLOS arquivos
            logger.info(f" Injetando PDFs ({len(ds_paths)} Datasheets, {len(dav_paths)} DAVs)...")
            
            if ds_paths:
                for path in ds_paths:
                    try:
                        inputs_arquivo = self.driver.find_elements(By.CSS_SELECTOR, "input[type='file'][name='attachment[file]']")
                        if len(inputs_arquivo) > 0:
                            inputs_arquivo[0].send_keys(path)
                            time.sleep(4) # AUMENTADO: Dá tempo da barra de upload do Coupa processar o arquivo
                    except Exception as e:
                        logger.warning(f" Erro ao anexar Datasheet ({path}): {e}")
            
            if dav_paths:
                for path in dav_paths:
                    try:
                        inputs_arquivo = self.driver.find_elements(By.CSS_SELECTOR, "input[type='file'][name='attachment[file]']")
                        if len(inputs_arquivo) > 1:
                            inputs_arquivo[1].send_keys(path)
                            time.sleep(4) # AUMENTADO: Evita que a requisição de salvamento atropele o upload
                    except Exception as e:
                        logger.warning(f" Erro ao anexar DAV ({path}): {e}")
            
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
                logger.info(f" O evento possui {len(linhas_de_itens)} linhas/itens no total.")
                
                for idx, linha in enumerate(linhas_de_itens):
                    se_tem_preco = idx < len(lista_precos) and str(lista_precos[idx]).strip() != ""
                    se_tem_prazo = idx < len(lista_prazos) and str(lista_prazos[idx]).strip() != ""
                    
                    if se_tem_preco or se_tem_prazo:
                        logger.info(f"    Processando Item {idx+1}...")
                        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", linha)
                        time.sleep(1) # PAUSA: Espera a rolagem terminar
                        
                        try:
                            btn_expandir = linha.find_element(By.CSS_SELECTOR, "div.s-expandSidebar, img.s-expandLines")
                            self.driver.execute_script("arguments[0].click();", btn_expandir)
                            time.sleep(2) # PAUSA: Espera a animação do Coupa abrir os campos
                        except: 
                            pass 
                        
                        try:
                            # Função interna reescrita para evitar bloqueio por Rate Limit (Bombardeio AJAX)
                            def preencher_cf(seletor, valor_fixo, nome_log):
                                cfs = linha.find_elements(By.CSS_SELECTOR, seletor)
                                if cfs:
                                    try:
                                        cfs[0].clear()
                                        time.sleep(0.2)
                                        cfs[0].send_keys(valor_fixo)
                                        cfs[0].send_keys(Keys.TAB) # Simula o usuário saindo do campo
                                    except:
                                        # Adicionado o evento 'blur' para forçar o auto-save de forma mais limpa
                                        self.driver.execute_script("""
                                            arguments[0].value = arguments[1];
                                            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                                            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                                            arguments[0].dispatchEvent(new Event('blur', { bubbles: true }));
                                        """, cfs[0], valor_fixo)
                                    logger.info(f"      ✅ {nome_log} preenchido com '{valor_fixo}'")
                                    time.sleep(1.5) # O FREIO MãGICO: Dá 1.5s para o servidor processar antes de ir pro próximo imposto

                            # Copiar NCM (Se existir no item, joga pro custom_field_9 ComboBox)
                            ncm_element = linha.find_elements(By.CSS_SELECTOR, "div.s-classification_of_goods p.s-textField")
                            if ncm_element:
                                ncm_val = ncm_element[0].text.strip()
                                ncm_input = linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_9")
                                if ncm_input:
                                    ncm_input[0].clear()
                                    ncm_input[0].send_keys(ncm_val)
                                    time.sleep(1.5) # Mais tempo para o AJAX do dropdown do NCM carregar
                                    ncm_input[0].send_keys(Keys.ARROW_DOWN)
                                    time.sleep(0.5)
                                    ncm_input[0].send_keys(Keys.ENTER)
                                    logger.info(f"      ✅ NCM copiado e injetado: {ncm_val}")
                                    time.sleep(1.5) # Freio após injetar NCM

                            # Impostos Fixos
                            preencher_cf("input.s-custom_field_8", "3", "COFINS (%)")    # Campo 8
                            preencher_cf("input.s-custom_field_7", "0,65", "PIS (%)")    # Campo 7
                            preencher_cf("input.s-custom_field_6", "0", "IPI (%)")       # Campo 6
                            preencher_cf("input.s-custom_field_3", "0", "ISS (%)")       # Campo 3 (0 para material)

                            # Preencher ICMS se o utilizador informou (Campo 4)
                            icms_atual = str(lista_icms[idx]).strip() if idx < len(lista_icms) else ""
                            if icms_atual:
                                preencher_cf("input.s-custom_field_4", icms_atual, "ICMS (%)")

                            # Preencher Origem (ComboBox - Campo 1) se o utilizador informou
                            origem_atual = str(lista_origens[idx]).strip() if idx < len(lista_origens) else ""
                            if origem_atual:
                                cfs_origem = linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_1")
                                if cfs_origem:
                                    cfs_origem[0].clear()
                                    time.sleep(0.5)
                                    cfs_origem[0].send_keys(origem_atual)
                                    time.sleep(1.5)
                                    cfs_origem[0].send_keys(Keys.ARROW_DOWN)
                                    time.sleep(0.5)
                                    cfs_origem[0].send_keys(Keys.ARROW_UP)
                                    time.sleep(0.5)
                                    cfs_origem[0].send_keys(Keys.ENTER)
                                    logger.info(f"      ✅ Origem preenchida: {origem_atual}")
                                    time.sleep(1.5) # Freio após injetar Origem

                        except Exception as e:
                            logger.warning(f"       Erro ao processar formulário extra do Coupa: {e}")

                        if se_tem_preco:
                            preco_atual = str(lista_precos[idx]).strip()
                            try:
                                input_preco = linha.find_element(By.CSS_SELECTOR, "input.s-price_amount_input")
                                try:
                                    input_preco.clear()
                                    time.sleep(0.3)
                                    input_preco.send_keys(preco_atual)
                                    input_preco.send_keys(Keys.TAB)
                                except:
                                    self.driver.execute_script("""
                                        arguments[0].value = arguments[1];
                                        arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                                        arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                                        arguments[0].dispatchEvent(new Event('blur', { bubbles: true }));
                                    """, input_preco, preco_atual)
                                logger.info(f"      ✅ Preço {preco_atual} inserido.")
                                time.sleep(1.5) # Freio após injetar o Preço
                            except Exception as e:
                                logger.warning(f"      âš ï¸ Falha ao inserir preço: {e}")

                        if se_tem_prazo:
                            prazo_atual = str(lista_prazos[idx]).strip()
                            try:
                                input_prazo = linha.find_element(By.CSS_SELECTOR, "input.s-lead_time_input")
                                try:
                                    ActionChains(self.driver) \
                                        .move_to_element(input_prazo) \
                                        .click() \
                                        .pause(0.3) \
                                        .key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL) \
                                        .send_keys(Keys.BACKSPACE) \
                                        .send_keys(prazo_atual) \
                                        .send_keys(Keys.TAB) \
                                        .perform()
                                    
                                    time.sleep(0.5)
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
                                time.sleep(1.5) # Freio após injetar o Prazo
                            except Exception as e:
                                logger.warning(f"      âš ï¸ Falha ao inserir prazo: {e}")
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
            
            self.driver.close()
            self.driver.switch_to.window(self.driver.window_handles[0])
            
        except Exception as e:
            logger.error(f"âŒ Erro ao responder evento: {e}")
            self.sio.emit('tarefa_concluida', {'evento': f'Evento {evento}', 'sucesso': False, 'erro': str(e)})
            
            # Se der erro, garante que fecha a aba extra e volta pra principal pra não travar tudo
            if len(self.driver.window_handles) > 1:
                self.driver.close()
                self.driver.switch_to.window(self.driver.window_handles[0])
