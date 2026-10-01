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


class PedidosMixin:
    def extrair_pedidos(self):
        logger.info("\n--- INICIANDO EXTRAÇÃO DE PEDIDOS (ORDERS) ---")
        self.solicitacao_parada = False

        pedidos_detalhes = []
        
        # Carrega pedidos ja existentes para checagem rapida (se disponivel)
        pedidos_existentes = set()
        arq_db = getattr(self, 'ARQUIVO_PEDIDOS_JSON', None)
        if arq_db and __import__('os').path.exists(arq_db):
            try:
                import json
                with open(arq_db, 'r', encoding='utf-8') as f:
                    dados = json.load(f)
                    for d in dados:
                        if 'pedido' in d: pedidos_existentes.add(d['pedido'])
            except: pass

        clientes_alvo = ["Vale", "Vale Base Metals"]

        try:
            url_orders = f"{self.BASE_SUPPLIER_URL}/orders/"
            self.garantir_logado(url_orders)
            
            try:
                nav_link = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//a[@data-testid='main_nav_order']")))
                self.driver.execute_script("arguments[0].click();", nav_link)
                logger.info(" Clicou no link da navbar 'Pedidos'.")
                time.sleep(4)
            except Exception:
                logger.info(" Link da navbar não encontrado ou falhou, navegando direto via URL...")
                self.driver.get(url_orders)
                time.sleep(4)
            
            self.garantir_logado(url_orders)
            
            for cliente in clientes_alvo:
                if self.solicitacao_parada: break
                
                logger.info(f" Selecionando o cliente '{cliente}' no filtro...")
                try:
                    combo_input = self.driver.find_elements(By.CSS_SELECTOR, "input.s-inputCustomerList")
                    if combo_input:
                        combo_el = combo_input[0]
                        valor_atual = combo_el.get_attribute("value")
                        if not valor_atual or cliente.lower() not in str(valor_atual).lower():
                            logger.info(f" Digitando '{cliente}' no combobox...")
                            combo_el.clear()
                            time.sleep(1)
                            combo_el.send_keys(cliente)
                            time.sleep(2)
                            combo_el.send_keys(Keys.ENTER)
                            time.sleep(4)
                            logger.info(f" Filtro de cliente '{cliente}' aplicado com sucesso.")
                        else:
                            logger.info(f" Cliente '{cliente}' já estava selecionado.")
                    else:
                        logger.warning(" Combobox de clientes nao encontrado na pagina.")
                except Exception as e:
                    logger.warning(f" Erro ao tentar selecionar o cliente: {e}")
                
                pagina_atual = 1
                paginas_sem_novo = 0
                
                while not self.solicitacao_parada:
                    logger.info(f" Aguardando tabela de pedidos carregar (Pagina {pagina_atual} - Cliente {cliente})...")
                    try:
                        self.wait.until(EC.presence_of_element_located((By.ID, "connect_order_header_tbody")))
                    except Exception:
                        try:
                            iframe = self.driver.find_element(By.TAG_NAME, "iframe")
                            self.driver.switch_to.frame(iframe)
                            self.wait.until(EC.presence_of_element_located((By.ID, "connect_order_header_tbody")))
                            logger.info(" Tabela de pedidos encontrada dentro do iframe.")
                        except Exception:
                            logger.warning(" Tabela de pedidos (connect_order_header_tbody) nao carregou a tempo ou esta vazia.")
                    
                    pedidos_list = []
                
                    try:
                        try:
                            self.wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(@href, 'supplier_order_headers')]")))
                        except Exception:
                            pass
                        links = self.driver.find_elements(By.XPATH, "//td[contains(@class, 's-datatable-cell-id')]//a[contains(@href, 'supplier_order_headers')]")
                        if not links:
                            links = self.driver.find_elements(By.XPATH, "//a[contains(@href, 'supplier_order_headers')]")
                        for a_tag in links:
                            if self.solicitacao_parada:
                                break
                            num_pedido = a_tag.text.strip()
                            href = a_tag.get_attribute("href")
                            if num_pedido and href:
                                existe = False
                                for p in pedidos_list:
                                    if p['num'] == num_pedido: existe = True
                                if not existe:
                                    pedidos_list.append({'num': num_pedido, 'url': href})
                    except Exception as e:
                        logger.error(f" Erro ao ler tabela de pedidos: {e}")
                    
                    logger.info(f" Total de pedidos encontrados nesta página: {len(pedidos_list)}")
                
                    # Filtra pedidos novos
                    pedidos_novos_na_pagina = []
                    for p in pedidos_list:
                        if p['num'] not in pedidos_existentes:
                            pedidos_novos_na_pagina.append(p)
                            
                    if not pedidos_novos_na_pagina:
                        paginas_sem_novo += 1
                        logger.info(f" Nenhum pedido novo nesta página. ({paginas_sem_novo}/3)")
                    else:
                        paginas_sem_novo = 0
                
                    for p in pedidos_novos_na_pagina:
                        if self.solicitacao_parada:
                            break
                            
                        num = p['num']
                        url = p['url']
                        logger.info(f"\n--- EXTRAINDO DETALHES DO PEDIDO {num} ---")
                        
                        try:
                            self.driver.execute_script(f"window.open('{url}', '_blank');")
                            self.driver.switch_to.window(self.driver.window_handles[-1])
                            time.sleep(4)
                        
                            try:
                                iframe = self.driver.find_element(By.TAG_NAME, "iframe")
                                self.driver.switch_to.frame(iframe)
                            except: pass
                        
                            # 1. Cidade e Estado
                            cidade_estado = "N/A"
                            try:
                                address_el = self.driver.find_elements(By.CSS_SELECTOR, "span.address")
                                if address_el:
                                    addr_text = address_el[0].text
                                    m = re.search(r'\d{5}-\d{3}\s+(.+?)\s+([A-Z]{2})', addr_text)
                                    if m:
                                        cidade_estado = f"{m.group(1).strip()} {m.group(2).strip()}"
                                    else:
                                        cidade_estado = addr_text.replace('\n', ' | ')[:100]
                            except: pass
                            
                            # 2. Frete
                            frete = "N/A"
                            try:
                                frete_el = self.driver.find_elements(By.ID, "order_header_shipping_term")
                                if frete_el: frete = frete_el[0].text.strip()
                            except: pass
                        
                            # 3. Valor Total
                            valor_total = "N/A"
                            try:
                                total_el = self.driver.find_elements(By.XPATH, "//div[contains(@class, 'shadow total')]//span[@title='BRL']")
                                if total_el: valor_total = total_el[0].text.strip()
                            except: pass
                        
                            # 4. Numero do Evento
                            evento_num = "N/A"
                            vendedor_encontrado = "N/A"
                            try:
                                evento_el = self.driver.find_elements(By.XPATH, "//div[contains(@class, 'form_element') and label[contains(text(), 'Nota Fornecedor')]]")
                                if evento_el:
                                    texto_nota = evento_el[0].text
                                    numeros_possiveis = re.findall(r'\b\d{5,}\b', texto_nota)
                                    if numeros_possiveis:
                                        evento_num = " / ".join(numeros_possiveis)
                                        vendedor_encontrado = self.buscar_vendedor_por_evento(numeros_possiveis)
                            except: pass
                        
                            logger.info(f" [HEADER] Cidade: {cidade_estado} | Frete: {frete} | Total: {valor_total} | Evento: {evento_num}")
                        
                            # 5. ITENS
                            itens = []
                            linhas_itens = self.driver.find_elements(By.XPATH, "//tr[contains(@id, 'supplier_order_line_row')]")
                            logger.info(f" Encontrados {len(linhas_itens)} itens no pedido.")
                        
                            for idx, linha in enumerate(linhas_itens, 1):
                                prazo = "N/A"
                                try:
                                    prazo_el = linha.find_elements(By.CSS_SELECTOR, "span.s-local_need_by_date")
                                    if prazo_el: prazo = prazo_el[0].text.strip()
                                except: pass
                            
                                texto_item = "N/A"
                                try:
                                    texto_el = linha.find_elements(By.XPATH, ".//div[label[contains(text(), 'Texto do item') or contains(text(), 'Item')]]")
                                    if texto_el:
                                        t = texto_el[0].text.replace("Texto do Item", "").replace("Texto do item", "").strip()
                                    
                                        # Se estiver vazio ou for "Nenhum", tenta buscar no "Texto Pedido Material"
                                        if not t or t.lower() == "nenhum" or "Nenhum" in t:
                                            texto_mat_el = linha.find_elements(By.XPATH, ".//div[label[contains(text(), 'Texto Pedido Material') or contains(text(), 'Descrição da Compra do Material')]]")
                                            if texto_mat_el:
                                                t_mat = texto_mat_el[0].text.replace("Texto Pedido Material", "").replace("Descrição da Compra do Material", "").strip()
                                                # Pega apenas a parte do PT || até os asteriscos ou próxima língua
                                                m = re.search(r'PT\s*\|\|(.*?)(?:\*{3,}|[A-Z]{2}\s*\|\||$)', t_mat, re.IGNORECASE | re.DOTALL)
                                                if m:
                                                    t = m.group(1).strip()
                                                else:
                                                    t = t_mat # fallback se não tiver a marcação PT ||
                                                
                                        texto_item = t
                                except: pass
                            
                                impostos = "N/A"
                                try:
                                    impostos_el = linha.find_elements(By.XPATH, ".//div[label[contains(text(), 'Informações Tributárias') or contains(text(), 'Informação de impostos')]]")
                                    if impostos_el:
                                        impostos = impostos_el[0].text.replace("Informações Tributárias", "").replace("Informação de impostos", "").strip().replace('\n', ' ')
                                except: pass
                            
                                email_req = "N/A"
                                try:
                                    email_el = linha.find_elements(By.XPATH, ".//div[label[contains(text(), 'Informação Adicional') or contains(text(), 'Requisitante')]]")
                                    if email_el:
                                        t = email_el[0].text
                                        m = re.search(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', t)
                                        if m: email_req = m.group(1)
                                except: pass
                                
                                origem = "N/A"
                                try:
                                    origem_el = linha.find_elements(By.XPATH, ".//div[label[contains(text(), 'Origem do Material')]]")
                                    if origem_el: origem = origem_el[0].text.replace("Origem do Material", "").strip()
                                except: pass
                            
                                # Regras de ALERTA (Cores)
                                alertas_chamado = []
                                if "3 - IMPORTACAO" in origem.upper():
                                    alertas_chamado.append(f"abrir chamado no {num} por ser importado.")
                                if "IPI" in impostos.upper() and ("11%" in impostos or "15%" in impostos or "18%" in impostos or "20%" in impostos):
                                    alertas_chamado.append(f"abrir chamado no {num} por conter IPI nos impostos")
                            
                                logger.info(f"   [ITEM {idx}] Prazo: {prazo} | Requisitante: {email_req}")
                                logger.info(f"          Texto: {texto_item[:100]}...")
                                logger.info(f"          Impostos: {impostos[:100]}...")
                            
                                for alerta in alertas_chamado:
                                    logger.warning(f"   🚨 ALERTA: - {alerta}")
                            
                                itens.append({
                                    'prazo': prazo,
                                    'texto': texto_item,
                                    'impostos': impostos,
                                    'email': email_req,
                                    'origem': origem,
                                    'alertas': alertas_chamado
                                })
                        
                            try:
                                self.driver.switch_to.default_content() # Garante que está fora do iframe
                                time.sleep(1)
                            
                                def buscar_botao_impressao():
                                    btn = self.driver.find_elements(By.XPATH, "//a[contains(., 'Exibição da impressão')]")
                                    if not btn:
                                        btn = self.driver.find_elements(By.XPATH, "//img[contains(@class, 'sprite-printer')]/ancestor::a")
                                    return btn

                                btn_impressao = buscar_botao_impressao()
                            
                                # Se não achou, tenta dentro do iframe
                                if not btn_impressao:
                                    try:
                                        iframe = self.driver.find_element(By.TAG_NAME, "iframe")
                                        self.driver.switch_to.frame(iframe)
                                        btn_impressao = buscar_botao_impressao()
                                    except:
                                        pass
                                
                                if btn_impressao:
                                    href_impressao = btn_impressao[0].get_attribute("href")
                                    if href_impressao:
                                        logger.info(f" Baixando impressão do pedido {num}. URL: {href_impressao}")
                                    
                                        # Abre a aba de impressão
                                        self.driver.execute_script(f"window.open('{href_impressao}', '_blank');")
                                        time.sleep(2)
                                        self.driver.switch_to.window(self.driver.window_handles[-1])
                                        time.sleep(5) # Aguarda renderizar a página de impressão
                                    
                                        try:
                                            import base64
                                            from selenium.webdriver.common.print_page_options import PrintOptions
                                        
                                            print_options = PrintOptions()
                                            pdf_b64 = self.driver.print_page(print_options)
                                        
                                            pasta_pdfs = r"\SERVIDOR2\Publico\ALLAN\PEDIDOS"
                                            os.makedirs(pasta_pdfs, exist_ok=True)
                                            caminho_pdf = os.path.join(pasta_pdfs, f"Pedido_{num}.pdf")
                                        
                                            with open(caminho_pdf, "wb") as f:
                                                f.write(base64.b64decode(pdf_b64))
                                            
                                            logger.info(f" PDF do pedido {num} salvo com sucesso em {caminho_pdf}")
                                        except Exception as ex_print:
                                            logger.warning(f" Falha ao gerar PDF via print_page: {ex_print}")
                                        
                                        # Fecha a aba da impressão e volta para a aba do pedido
                                        if len(self.driver.window_handles) > 2:
                                            self.driver.close()
                                            self.driver.switch_to.window(self.driver.window_handles[-1])
                            except Exception as e:
                                logger.error(f" Erro ao tentar baixar a impressão do pedido {num}: {e}")

                            novo_pedido = {
                                'pedido': num,
                                'cidade_estado': cidade_estado,
                                'frete': frete,
                                'total': valor_total,
                                'evento': evento_num,
                                'vendedor': vendedor_encontrado,
                                'itens': itens,
                                'caminho_pdf': f"\\SERVIDOR2\Publico\ALLAN\PEDIDOS\Pedido_{num}.pdf"
                            }
                            pedidos_detalhes.append(novo_pedido)
                            pedidos_existentes.add(num)
                            
                            # Salva imediatamente na planilha para feedback em tempo real
                            self.atualizar_pedidos_excel([novo_pedido])
                            
                            # Salva o pedido no banco de dados JSON centralizado
                            try:
                                if hasattr(self, 'salvar_pedido_database'):
                                    self.salvar_pedido_database(novo_pedido)
                            except Exception as e:
                                logger.error(f" Falha ao salvar no banco: {e}")
                        
                        except Exception as e:
                            logger.error(f" Erro ao extrair detalhes do pedido {num}: {e}")
                    
                        finally:
                            # Fecha aba e volta pra principal (garante não deixar abas perdidas)
                            if len(self.driver.window_handles) > 1:
                                self.driver.close()
                                self.driver.switch_to.window(self.driver.window_handles[0])
                                time.sleep(1)
                
                    if paginas_sem_novo >= 3:
                        logger.info(f" ⏭️ 3 páginas vazias alcançadas para {cliente}. Avançando empresa...")
                        break
                        
                    # Mudar de pagina
                    try:
                        self.driver.switch_to.default_content()
                        try:
                            iframe = self.driver.find_element(By.TAG_NAME, 'iframe')
                            self.driver.switch_to.frame(iframe)
                        except: pass
                        btn_next = self.driver.find_elements(By.CSS_SELECTOR, 'a.next_page')
                        if btn_next and 'disabled' not in btn_next[0].get_attribute('class'):
                            logger.info(' Clicando para avancar para a proxima pagina...')
                            self.driver.execute_script('arguments[0].click();', btn_next[0])
                            time.sleep(5)
                            pagina_atual += 1
                        else:
                            logger.info(' Ultima pagina alcancada ou botao avancar indisponivel.')
                            break
                    except Exception as e:
                        logger.warning(f' Nao foi possivel avancar a pagina: {e}')
                        break

            self.sio.emit('tarefa_concluida', {'evento': 'Listagem de Pedidos', 'sucesso': True, 'total': len(pedidos_detalhes), 'detalhes': pedidos_detalhes})
            return pedidos_detalhes
            
        except Exception as e:
            logger.error(f" Erro Crítico na extração de pedidos: {e}")
            self.sio.emit('tarefa_concluida', {'evento': 'Listagem de Pedidos', 'sucesso': False, 'erro': str(e)})
            return []
