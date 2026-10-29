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
    def _selecionar_cliente_sourcing(self, nome_cliente):
        eh_metais = "metal" in str(nome_cliente).lower() or "metais" in str(nome_cliente).lower()
        rotulo = "Vale Base Metals" if eh_metais else "Vale"
        logger.info(f"🏢 Selecionando o cliente '{rotulo}' no campo customersList_1...")
        self.sio.emit('relatar_progresso', {'mensagem': f"🏢 Selecionando cliente '{rotulo}'..."})
        try:
            if hasattr(self.driver, 'switch_to') and hasattr(self.driver.switch_to, 'default_content'):
                self.driver.switch_to.default_content()
            input_cliente = self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input#customersList_1, input.s-inputCustomerList"))
            )
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", input_cliente)
            self.driver.execute_script("arguments[0].click();", input_cliente)
            time.sleep(1)

            if eh_metais:
                xpath_opcao = (
                    "//li[contains(@class, 'ComboBox__resultItem') and ("
                    "normalize-space(text())='Vale Base Metals' or "
                    "normalize-space(text())='Vale Base Metais' or "
                    "contains(translate(normalize-space(text()), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'VALE BASE METAL')"
                    ")]"
                )
            else:
                xpath_opcao = (
                    "//li[contains(@class, 'ComboBox__resultItem') and ("
                    "normalize-space(text())='Vale' or "
                    "normalize-space(text())='VALE'"
                    ")]"
                )

            opcoes = self.driver.find_elements(By.XPATH, xpath_opcao)
            if opcoes:
                self.driver.execute_script("arguments[0].click();", opcoes[0])
            else:
                input_cliente.clear()
                time.sleep(0.3)
                input_cliente.send_keys(rotulo)
                time.sleep(1)
                opcoes = self.driver.find_elements(By.XPATH, xpath_opcao)
                if opcoes:
                    self.driver.execute_script("arguments[0].click();", opcoes[0])
                else:
                    input_cliente.send_keys(Keys.ENTER)

            time.sleep(3)
            logger.info(f"   ✅ Cliente '{rotulo}' selecionado com sucesso!")
            return True
        except Exception as e:
            logger.warning(f"   ⚠️ Não foi possível selecionar o cliente '{rotulo}': {e}")
            return False

    def _pesquisar_link_evento(self, evento):
        logger.info(f"🔎 Pesquisando evento {evento}...")
        try:
            if hasattr(self.driver, 'switch_to') and hasattr(self.driver.switch_to, 'default_content'):
                self.driver.switch_to.default_content()
        except Exception:
            pass
        search = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input.s-toolbarSearch")))
        search.clear()
        time.sleep(0.3)
        try:
            if (search.get_attribute("value") or "").strip():
                self.driver.execute_script("arguments[0].value = '';", search)
        except Exception:
            pass
        search.send_keys(evento)
        search.send_keys(Keys.RETURN)
        time.sleep(4)

        xpath_preciso = f"//td[contains(@class, 'coupaTable__cell')]//a[contains(text(), '{evento}') or contains(@href, '{evento}')]"
        try:
            return WebDriverWait(self.driver, 6).until(EC.presence_of_element_located((By.XPATH, xpath_preciso)))
        except Exception:
            return None

    def _verificar_e_resolver_captcha(self):
        """Se aparecer um Captcha em qualquer etapa, transmite em alta velocidade (~5 FPS, estilo vídeo) para a tela principal de todos os vendedores até alguém resolver."""
        def _eh_recaptcha(f):
            try:
                src = f.get_attribute("src") or ""
                name = f.get_attribute("name") or ""
                return "anchor" in src or "bframe" in src or "recaptcha" in src or name.startswith("a-") or name.startswith("c-")
            except Exception:
                return False

        # Descobre onde os iframes do reCAPTCHA estão sem entrar em iframes ocultos na página de listagem
        dentro_iframe_evento = bool(getattr(self, '_dentro_iframe_evento', False))
        modo_contexto = "coupa_iframe" if dentro_iframe_evento else "top"
        try:
            frames_atuais = self.driver.find_elements(By.TAG_NAME, "iframe")
            tem_recaptcha = any(_eh_recaptcha(f) for f in frames_atuais)
            if not tem_recaptcha and dentro_iframe_evento and hasattr(self.driver.switch_to, 'default_content'):
                self.driver.switch_to.default_content()
                frames_top = self.driver.find_elements(By.TAG_NAME, "iframe")
                if any(_eh_recaptcha(f) for f in frames_top):
                    modo_contexto = "top"
                    tem_recaptcha = True
                else:
                    # Volta para o iframe principal do evento Coupa
                    for f_outer in frames_top:
                        if not _eh_recaptcha(f_outer):
                            try:
                                self.driver.switch_to.frame(f_outer)
                                break
                            except Exception:
                                pass
        except Exception:
            return False

        if not tem_recaptcha:
            return False

        def _posicionar_contexto():
            if not hasattr(self.driver.switch_to, 'default_content'):
                return
            if modo_contexto == "top":
                self.driver.switch_to.default_content()
            elif modo_contexto == "coupa_iframe":
                self.driver.switch_to.default_content()
                for f_o in self.driver.find_elements(By.TAG_NAME, "iframe"):
                    if not _eh_recaptcha(f_o):
                        try:
                            self.driver.switch_to.frame(f_o)
                            break
                        except Exception:
                            pass

        inicio_captcha = None
        ultimo_print = 0
        resolvido = False

        while True:
            try:
                _posicionar_contexto()
                frames = self.driver.find_elements(By.TAG_NAME, "iframe")
                iframe_caixinha = None
                iframe_desafio = None

                for f in frames:
                    src = f.get_attribute("src") or ""
                    name = f.get_attribute("name") or ""
                    if "anchor" in src or name.startswith("a-"):
                        iframe_caixinha = f
                    elif "bframe" in src or name.startswith("c-"):
                        iframe_desafio = f

                captcha_pendente = False
                if iframe_caixinha and iframe_caixinha.is_displayed():
                    captcha_pendente = True
                    try:
                        self.driver.switch_to.frame(iframe_caixinha)
                        if self.driver.find_element(By.ID, "recaptcha-anchor").get_attribute("aria-checked") == "true":
                            captcha_pendente = False
                    except Exception:
                        pass
                    finally:
                        if modo_contexto in ("top", "coupa_iframe"):
                            _posicionar_contexto()
                        elif hasattr(self.driver.switch_to, 'default_content'):
                            self.driver.switch_to.default_content()

                if iframe_desafio and iframe_desafio.is_displayed():
                    captcha_pendente = True

                if not captcha_pendente:
                    if inicio_captcha is not None:
                        self.sio.emit('captcha_resolvido_do_robo', {'resolvido': True})
                        resolvido = True
                    break

                if inicio_captcha is None:
                    inicio_captcha = time.time()
                    logger.warning("⚠️ Captcha detectado! Enviando em tempo real para a tela principal dos vendedores...")
                    self.sio.emit('relatar_progresso', {'mensagem': '⚠️ Captcha detectado! Resolva na tela principal de Minhas Cotações...'})
                elif time.time() - inicio_captcha > 300:
                    self.sio.emit('captcha_resolvido_do_robo', {'resolvido': True})
                    raise Exception("CAPTCHA_TIMEOUT: Captcha expirado sem solução em 5 minutos.")

                elemento_alvo = iframe_desafio if (iframe_desafio and iframe_desafio.is_displayed()) else iframe_caixinha
                if elemento_alvo:
                    aguardando = getattr(self, 'aguardando_clique', None)
                    coords = getattr(self, 'coordenadas_clique', None)
                    if aguardando is not None and aguardando.is_set() and coords:
                        try:
                            largura = (getattr(elemento_alvo, 'size', None) or {}).get('width', 0)
                            altura = (getattr(elemento_alvo, 'size', None) or {}).get('height', 0)
                            px = coords['x'] - (largura / 2)
                            py = coords['y'] - (altura / 2)
                            ActionChains(self.driver).move_to_element_with_offset(elemento_alvo, px, py).click().perform()
                            ultimo_print = 0  # força novo frame imediato após o clique
                        except Exception as e_click:
                            logger.warning(f"Erro ao clicar no Captcha: {e_click}")
                        finally:
                            aguardando.clear()

                    agora = time.time()
                    if agora - ultimo_print >= 0.2:
                        try:
                            png_bytes = getattr(elemento_alvo, 'screenshot_as_png', None)
                            if png_bytes:
                                png_b64 = base64.b64encode(png_bytes).decode('utf-8')
                                self.sio.emit('imagem_captcha_do_robo', {'imagem': png_b64})
                        except Exception:
                            pass
                        ultimo_print = agora

                time.sleep(0.08)
            except Exception as e_cap:
                if "CAPTCHA_TIMEOUT" in str(e_cap):
                    raise
                break

        if dentro_iframe_evento and modo_contexto == "top" and hasattr(self.driver.switch_to, 'default_content'):
            try:
                self.driver.switch_to.default_content()
                for f_o in self.driver.find_elements(By.TAG_NAME, "iframe"):
                    if not _eh_recaptcha(f_o):
                        self.driver.switch_to.frame(f_o)
                        break
            except Exception:
                pass

        return resolvido

    def _capturar_print_item(self, linha, idx_item, preco_str="", prazo_str=""):
        """Tira uma print da área dos campos preenchidos (focado no prazo de entrega e campos do item)."""
        try:
            # Rola até a área dos campos preenchidos do item (priorizando o campo de prazo de entrega e campos preenchidos)
            try:
                campo_prazo = None
                campos_prazo = linha.find_elements(By.CSS_SELECTOR, "input.s-lead_time_input")
                if campos_prazo:
                    campo_prazo = campos_prazo[0]

                self.driver.execute_script("""
                    const elLinha = arguments[0];
                    const elPrazo = arguments[1];
                    if (document.activeElement && typeof document.activeElement.blur === 'function') {
                        document.activeElement.blur();
                    }
                    const seletores = [
                        'input.s-lead_time_input',
                        'input.s-price_amount_input',
                        'input.s-custom_field_1',
                        'input.s-custom_field_3',
                        'input.s-custom_field_4',
                        'input.s-custom_field_6',
                        'input.s-custom_field_7',
                        'input.s-custom_field_8',
                        'input.s-custom_field_9'
                    ];
                    const visiveis = Array.from(elLinha.querySelectorAll(seletores.join(',')))
                        .filter(el => {
                            const r = el.getBoundingClientRect();
                            return r.width > 0 && r.height > 0;
                        });
                    if (visiveis.length > 0) {
                        let minTop = Infinity, maxBottom = -Infinity;
                        visiveis.forEach(c => {
                            const r = c.getBoundingClientRect();
                            if (r.top < minTop) minTop = r.top;
                            if (r.bottom > maxBottom) maxBottom = r.bottom;
                        });
                        let alvoCentro = (minTop + maxBottom) / 2;
                        const refPrazo = elPrazo || elLinha.querySelector('input.s-lead_time_input');
                        if (refPrazo) {
                            const rp = refPrazo.getBoundingClientRect();
                            if (rp.width > 0 && rp.height > 0) {
                                const margem = 90;
                                const delta = alvoCentro - (window.innerHeight / 2);
                                const novoTop = rp.top - delta;
                                const novoBottom = rp.bottom - delta;
                                if (novoBottom > window.innerHeight - margem) {
                                    alvoCentro += (novoBottom - (window.innerHeight - margem));
                                } else if (novoTop < margem) {
                                    alvoCentro -= (margem - novoTop);
                                }
                            }
                        }
                        window.scrollBy(0, alvoCentro - (window.innerHeight / 2));
                    } else if (elPrazo) {
                        elPrazo.scrollIntoView({ block: 'center' });
                    } else {
                        elLinha.scrollIntoView({ block: 'end' });
                    }
                """, linha, campo_prazo)
            except Exception:
                try:
                    campo_prazo = linha.find_element(By.CSS_SELECTOR, "input.s-lead_time_input")
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", campo_prazo)
                except Exception:
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'end'});", linha)

            time.sleep(0.6)
            # Importante: usa get_screenshot_as_png() da janela visível primeiro, pois linha.screenshot_as_png
            # no ChromeDriver força scroll para o topo da div.s-itemsAndServicesLine (cortando os campos preenchidos embaixo).
            png_bytes = None
            if hasattr(self.driver, 'get_screenshot_as_png'):
                png_bytes = self.driver.get_screenshot_as_png()
            if not png_bytes:
                png_bytes = getattr(linha, 'screenshot_as_png', None)
            if not png_bytes:
                return None
            png_b64 = base64.b64encode(png_bytes).decode('utf-8')
            return {
                'item': idx_item + 1,
                'imagem': png_b64,
                'preco': str(preco_str or '').strip(),
                'prazo': str(prazo_str or '').strip()
            }
        except Exception as e:
            logger.warning(f"   ⚠️ Não foi possível capturar print do Item {idx_item + 1}: {e}")
            return None

    def responder_evento(self, dados):
        evento = str(dados.get('evento', '')).strip()
        lista_precos = dados.get('precos', [])
        lista_prazos = dados.get('prazos', [])
        lista_ncms = dados.get('ncms', [])
        lista_origens = dados.get('origens', [])
        lista_icms = dados.get('icms', [])
        
        pasta_anexos = tempfile.mkdtemp(prefix='maestro-resposta-')   # apagada no fim, com ou sem erro
        falhas = []
        prints_itens_map = {}

        logger.info(f"🚀 Iniciando Resposta do Evento {evento}...")

        try:
            ds_paths = [self._gravar_anexo(pasta_anexos, f) for f in dados.get('datasheets', [])]
            dav_paths = [self._gravar_anexo(pasta_anexos, f) for f in dados.get('davs', [])]

            # 1. ROTA DIRETA PARA SOURCING (À PROVA DE FALHAS)
            self.sio.emit('relatar_progresso', {'mensagem': f" Acessando diretamente a página de Sourcing..."})
            
            # Sempre entra na página de pesquisa (inclusive ao puxar o próximo evento da fila)
            self._dentro_iframe_evento = False
            url_sourcing = f"{self.BASE_SUPPLIER_URL}/quotes/private_events"
            self.driver.get(url_sourcing)
            time.sleep(3)
            self._verificar_e_resolver_captcha()
            self.garantir_logado(url_sourcing)
            self._verificar_e_resolver_captcha()

            # 1.5 SELECIONA PRIMEIRO O CLIENTE 'Vale' E PROCURA O EVENTO
            self._selecionar_cliente_sourcing("Vale")
            link_evento = self._pesquisar_link_evento(evento)

            # 2. SE NÃO ACHAR NO 'Vale' NORMAL, MUDA O CLIENTE PARA 'Vale Base Metals' E PROCURA NOVAMENTE
            if link_evento is None:
                logger.info(f"   ⚠️ Evento {evento} não encontrado no cliente 'Vale'. Mudando para 'Vale Base Metals'...")
                self.sio.emit('relatar_progresso', {'mensagem': f"🔄 Evento {evento} não encontrado na Vale. Mudando cliente para Vale Base Metals..."})
                self._selecionar_cliente_sourcing("Vale Base Metals")
                link_evento = self._pesquisar_link_evento(evento)

            # 3. Entra no evento
            logger.info(f"   🖱️ Abrindo evento {evento}...")
            if link_evento is not None:
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link_evento)
                time.sleep(1)
                self.driver.execute_script("arguments[0].click();", link_evento)
                logger.info("   ✅ Clique efetuado com sucesso!")
                time.sleep(3)
                if len(self.driver.window_handles) > 1:
                    self.driver.switch_to.window(self.driver.window_handles[-1])
                    logger.info("   🔄 Foco alterado para a aba do evento.")
            else:
                logger.warning(f"   ⚠️ Link não encontrado na tabela em Vale nem em Vale Base Metals. Forçando acesso via URL (Nova Aba)...")
                self.driver.execute_script(f"window.open('{self.BASE_SUPPLIER_URL}/quotes/external_responses/{evento}/multi', '_blank');")
                time.sleep(3)
                self.driver.switch_to.window(self.driver.window_handles[-1])

            time.sleep(3)

            self.driver.switch_to.window(self.driver.window_handles[-1])
            time.sleep(3) 
            
            try: 
                self.driver.switch_to.frame(self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe"))))
                self._dentro_iframe_evento = True
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
            salvou_algum_item = False
            
            try:
                linhas_de_itens = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")
                total_linhas = len(linhas_de_itens)
                logger.info(f" O evento possui {total_linhas} linhas/itens no total.")
                
                for idx in range(total_linhas):
                    se_tem_preco = idx < len(lista_precos) and str(lista_precos[idx]).strip() != ""
                    se_tem_prazo = idx < len(lista_prazos) and str(lista_prazos[idx]).strip() != ""
                    
                    if se_tem_preco or se_tem_prazo:
                        linhas_atuais = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")
                        if idx >= len(linhas_atuais):
                            break
                        linha = linhas_atuais[idx]
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
                                    time.sleep(1.5) # O FREIO MÁGICO: Dá 1.5s para o servidor processar antes de ir pro próximo imposto

                            # Preencher NCM da Planilha Base (ComboBox - custom_field_9)
                            ncm_atual = str(lista_ncms[idx]).strip() if idx < len(lista_ncms) else ""
                            if ncm_atual:
                                ncm_input = linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_9")
                                if ncm_input:
                                    ncm_input[0].clear()
                                    time.sleep(0.3)
                                    ncm_input[0].send_keys(ncm_atual)
                                    time.sleep(1.5) # Mais tempo para o AJAX do dropdown do NCM carregar
                                    ncm_input[0].send_keys(Keys.ARROW_DOWN)
                                    time.sleep(0.5)
                                    ncm_input[0].send_keys(Keys.ENTER)
                                    logger.info(f"      ✅ NCM da planilha preenchido: {ncm_atual}")
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

                            # Preencher Origem (ComboBox - Campo 1: input.ComboBox__input.s-custom_field_1 com aria-owns="custom_field_1_..._results")
                            origem_atual = str(lista_origens[idx]).strip() if idx < len(lista_origens) else ""
                            if origem_atual:
                                cfs_origem = linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_1")
                                if cfs_origem:
                                    inp_origem = cfs_origem[0]
                                    try:
                                        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", inp_origem)
                                        self.driver.execute_script("arguments[0].click();", inp_origem)
                                        time.sleep(0.3)
                                    except Exception:
                                        pass
                                    inp_origem.clear()
                                    time.sleep(0.3)
                                    inp_origem.send_keys(origem_atual)
                                    time.sleep(1.5)
                                    # Seleciona a opção correspondente ao número digitado na lista de resultados (aria-owns="custom_field_1_..._results")
                                    selecionou_origem = False
                                    try:
                                        m_num = re.match(r'^(\d+)', origem_atual)
                                        num_origem = m_num.group(1) if m_num else origem_atual
                                        owns_id = inp_origem.get_attribute("aria-owns") or ""
                                        opcoes_origem = []
                                        if owns_id:
                                            opcoes_origem = self.driver.find_elements(By.CSS_SELECTOR, f"#{owns_id} li.ComboBox__resultItem, #{owns_id} li")
                                        if not opcoes_origem:
                                            opcoes_origem = [
                                                li for li in self.driver.find_elements(By.CSS_SELECTOR, "li.ComboBox__resultItem")
                                                if li.is_displayed()
                                            ]
                                        opcao_alvo = None
                                        for li in opcoes_origem:
                                            txt_li = (getattr(li, 'text', '') or '').strip()
                                            if not txt_li:
                                                continue
                                            if txt_li.lower() == origem_atual.lower() or re.match(rf'^{re.escape(num_origem)}(?:\b|\s|-|\.)', txt_li):
                                                opcao_alvo = li
                                                break
                                        if opcao_alvo is None and opcoes_origem:
                                            for li in opcoes_origem:
                                                txt_li = (getattr(li, 'text', '') or '').strip()
                                                if txt_li.startswith(num_origem):
                                                    opcao_alvo = li
                                                    break
                                        if opcao_alvo is not None:
                                            self.driver.execute_script("arguments[0].scrollIntoView({block: 'nearest'}); arguments[0].click();", opcao_alvo)
                                            selecionou_origem = True
                                        else:
                                            selecionou_js = self.driver.execute_script("""
                                                const inp = arguments[0];
                                                const valor = String(arguments[1] || '').trim();
                                                const m = valor.match(/^(\\d+)/);
                                                const numAlvo = m ? m[1] : valor;
                                                const ownsId = inp && inp.getAttribute ? inp.getAttribute('aria-owns') : '';
                                                let itens = [];
                                                if (ownsId) {
                                                    const ul = document.getElementById(ownsId);
                                                    if (ul) itens = Array.from(ul.querySelectorAll('li.ComboBox__resultItem, li[role="option"], li'));
                                                }
                                                if (!itens.length) {
                                                    itens = Array.from(document.querySelectorAll('li.ComboBox__resultItem'))
                                                        .filter(el => el.offsetWidth > 0 || el.offsetHeight > 0);
                                                }
                                                const norm = s => String(s || '').trim();
                                                let alvo = itens.find(li => {
                                                    const t = norm(li.innerText || li.textContent);
                                                    if (!t) return false;
                                                    if (t.toLowerCase() === valor.toLowerCase()) return true;
                                                    const mLi = t.match(/^(\\d+)\\b/);
                                                    return mLi && mLi[1] === numAlvo;
                                                });
                                                if (!alvo && itens.length) {
                                                    alvo = itens.find(li => norm(li.innerText || li.textContent).startsWith(numAlvo)) || itens[0];
                                                }
                                                if (alvo) {
                                                    alvo.scrollIntoView({ block: 'nearest' });
                                                    alvo.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                                                    alvo.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                                                    alvo.click();
                                                    return true;
                                                }
                                                return false;
                                            """, inp_origem, origem_atual)
                                            selecionou_origem = (selecionou_js is True and bool(owns_id or opcoes_origem))
                                    except Exception:
                                        selecionou_origem = False

                                    if not selecionou_origem:
                                        inp_origem.send_keys(Keys.ARROW_DOWN)
                                        time.sleep(0.4)
                                        inp_origem.send_keys(Keys.ENTER)
                                    logger.info(f"      ✅ Origem preenchida e selecionada: {origem_atual}")
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
                                falhas.append(f"item {idx+1} (preço)")
                                logger.warning(f"      ⚠️ Falha ao inserir preço: {e}")

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
                                falhas.append(f"item {idx+1} (prazo)")
                                logger.warning(f"      ⚠️ Falha ao inserir prazo: {e}")

                        # Captura print da área dos campos preenchidos do item
                        preco_log = str(lista_precos[idx]).strip() if se_tem_preco else ""
                        prazo_log = str(lista_prazos[idx]).strip() if se_tem_prazo else ""
                        p_item = self._capturar_print_item(linha, idx, preco_log, prazo_log)
                        if p_item:
                            prints_itens_map[idx] = p_item

                        # Salva o item atual (<button class="button primary s-save" type="button"><span>Salvar</span></button>)
                        try:
                            botoes_salvar_item = linha.find_elements(By.CSS_SELECTOR, "button.s-save")
                            if not botoes_salvar_item:
                                try:
                                    btn_unico = self.driver.find_element(By.CSS_SELECTOR, "button.s-save")
                                    if btn_unico:
                                        botoes_salvar_item = [btn_unico]
                                except Exception:
                                    botoes_salvar_item = self.driver.find_elements(By.CSS_SELECTOR, "button.s-save")
                            if botoes_salvar_item:
                                btn_salvar_item = botoes_salvar_item[0]
                                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn_salvar_item)
                                time.sleep(0.3)
                                self.driver.execute_script("arguments[0].click();", btn_salvar_item)
                                salvou_algum_item = True
                                logger.info(f"      💾 Item {idx+1} salvo (button.s-save).")
                                time.sleep(2.5)
                                self._verificar_e_resolver_captcha()
                        except Exception as e_save_it:
                            logger.warning(f"      ⚠️ Aviso ao clicar em Salvar no Item {idx+1}: {e_save_it}")
                    else:
                        logger.info(f"    Item {idx+1} ignorado (Você deixou os campos vazios).")
                        
            except Exception as e:
                falhas.append("preenchimento das linhas")
                logger.error(f" Erro crítico no preenchimento das linhas: {e}")

            # 8. Salvar Final (só salva o rascunho; o envio ao cliente é feito por uma pessoa no portal)
            logger.info("✅ Finalizando salvamento do evento...")
            try:
                btn_salvar = self.driver.find_element(By.CSS_SELECTOR, "button.s-save")
                self.driver.execute_script("arguments[0].click();", btn_salvar)
                time.sleep(3)
                self._verificar_e_resolver_captcha()
            except NoSuchElementException:
                if not salvou_algum_item:
                    raise Exception("Botão Salvar não encontrado: o rascunho não foi salvo.")

            # 8.5 Caso algum item preenchido ainda não tenha print capturada, captura como fallback
            try:
                linhas_salvas = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")
                for idx, linha_s in enumerate(linhas_salvas):
                    se_tem_preco = idx < len(lista_precos) and str(lista_precos[idx]).strip() != ""
                    se_tem_prazo = idx < len(lista_prazos) and str(lista_prazos[idx]).strip() != ""
                    if (se_tem_preco or se_tem_prazo) and idx not in prints_itens_map:
                        preco_log = str(lista_precos[idx]).strip() if se_tem_preco else ""
                        prazo_log = str(lista_prazos[idx]).strip() if se_tem_prazo else ""
                        p_salvo = self._capturar_print_item(linha_s, idx, preco_log, prazo_log)
                        if p_salvo:
                            prints_itens_map[idx] = p_salvo
            except Exception as e_print:
                logger.warning(f"   ⚠️ Falha ao atualizar prints pós-salvamento: {e_print}")

            prints_finais = [prints_itens_map[k] for k in sorted(prints_itens_map.keys())]
            if prints_finais:
                self.sio.emit('prints_resposta_evento', {'evento': str(evento), 'prints': prints_finais})

            self._marcar_rascunho(evento)
            if falhas:
                raise Exception(f"Rascunho salvo, mas com falha em: {', '.join(falhas)}. Confira no portal antes de enviar.")

            if len(self.driver.window_handles) > 1:
                self.driver.close()
                self.driver.switch_to.window(self.driver.window_handles[0])

            logger.info(f"🎉 Rascunho do evento {evento} salvo. O envio ao cliente é manual.")
            self.sio.emit('tarefa_concluida', {'evento': f'Evento {evento}', 'sucesso': True, 'prints': prints_finais})

        except Exception as e:
            logger.error(f"❌ Erro ao responder evento: {e}")
            # Se der erro, garante que fecha a aba extra e volta pra principal antes de liberar o próximo da fila
            try:
                if len(self.driver.window_handles) > 1:
                    self.driver.close()
                    self.driver.switch_to.window(self.driver.window_handles[0])
            except Exception:
                pass
            self.sio.emit('tarefa_concluida', {'evento': f'Evento {evento}', 'sucesso': False, 'erro': str(e)})
        finally:
            shutil.rmtree(pasta_anexos, ignore_errors=True)

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
