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
from bots.transmissor_captcha import manter_transmissao, parar_transmissao
from bots.termos_participacao import aceitar_termos

logger = logging.getLogger("CoupaApp")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')

if not logger.handlers:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


class RespostaMixin:
    # Mapa dos campos da linha do item no Vale Base Metals (conferido no portal em 10/11/2026, evento 59642):
    #   s-price_amount_input  *Preço por unidade        s-lead_time_input  *Tempo de espera (dias)
    #   s-price_currency_input *Moeda (já vem BRL)      s-custom_field_1    Preço líquido (NÃO preencher)
    #   s-custom_field_2       ISS %                    s-custom_field_3    ICMS %
    #   s-custom_field_4       ICMS ST%                 s-custom_field_5   *IPI %
    #   s-custom_field_6       PIS %                    s-custom_field_7    COFINS %
    #   s-custom_field_8      *NCM (ComboBox)           s-custom_field_9   *Material Origin (ComboBox)
    # Impostos fixos, com os mesmos valores usados no perfil Vale normal. Ordem = ordem de preenchimento.
    CAMPOS_FIXOS_VALE_METAIS = (
        ('COFINS (%)', 'input.s-custom_field_7', '3'),
        ('PIS (%)', 'input.s-custom_field_6', '0,65'),
        ('IPI (%)', 'input.s-custom_field_5', '0'),
        ('ISS (%)', 'input.s-custom_field_2', '0'),
        ('ICMS ST (%)', 'input.s-custom_field_4', '0'),
    )
    SELETOR_ICMS_VALE_METAIS = 'input.s-custom_field_3'

    # Setter nativo + eventos: faz o React/Coupa enxergar o valor (mesmo padrão do campo de prazo)
    JS_DEFINIR_VALOR_INPUT = """
        const el = arguments[0];
        const val = String(arguments[1] || '');
        if (!el || typeof el.dispatchEvent !== 'function') return;
        const desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
        if (el._valueTracker) el._valueTracker.setValue('');
        if (desc && desc.set) { desc.set.call(el, val); } else { el.value = val; }
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new Event('blur', { bubbles: true }));
    """

    @staticmethod
    def _valores_equivalentes(lido, esperado):
        """Compara o valor lido do input com o esperado ('0,65' == '0.65' == '0,6500'; '3' == '3,00')."""
        a = str(lido or '').strip()
        b = str(esperado or '').strip()
        if not a or not b:
            return a == b

        def _num(s):
            s = re.sub(r'[R$\s%]', '', s)
            if ',' in s and '.' in s:
                s = s.replace('.', '').replace(',', '.')
            elif ',' in s:
                s = s.replace(',', '.')
            try:
                return float(s)
            except ValueError:
                return None

        na, nb = _num(a), _num(b)
        if na is not None and nb is not None:
            return abs(na - nb) < 1e-9
        return a.lower() == b.lower()

    def _preencher_campo_texto_verificado(self, el, valor, nome_log, tentativas_js=2):
        """
        Preenche um input de texto e LÊ DE VOLTA para garantir que o valor ficou no campo.
        1ª tentativa: clear + send_keys + TAB. Se o valor não ficou (AJAX do Coupa limpou, foco perdido...),
        insiste via setter nativo do React até `tentativas_js` vezes. Retorna True se o valor ficou.
        """
        try:
            el.clear()
            time.sleep(0.2)
            el.send_keys(valor)
            el.send_keys(Keys.TAB)
        except Exception:
            pass
        time.sleep(0.3)
        if self._valores_equivalentes(self._obter_valor_input(el), valor):
            return True

        for tentativa in range(1, tentativas_js + 1):
            try:
                self.driver.execute_script(self.JS_DEFINIR_VALOR_INPUT, el, valor)
            except Exception:
                pass
            time.sleep(0.5)
            if self._valores_equivalentes(self._obter_valor_input(el), valor):
                logger.info(f"      🔁 {nome_log} confirmado com '{valor}' na tentativa extra {tentativa}.")
                return True

        logger.warning(f"      ⚠️ {nome_log} não ficou com '{valor}' (lido: '{self._obter_valor_input(el)}').")
        return False

    def _aguardar_campos_item(self, linha, timeout=8):
        """
        Protocolo de segurança: espera os campos da linha aparecerem depois de expandir.
        Sem isso, find_elements devolve [] e os campos ficam em branco sem nenhum aviso.
        Na metade do tempo, clica de novo em expandir.
        """
        inicio = time.time()
        reclicou = False
        while time.time() - inicio < timeout:
            try:
                if linha.find_elements(By.CSS_SELECTOR, "input.s-lead_time_input") or \
                        linha.find_elements(By.CSS_SELECTOR, "input.s-price_amount_input"):
                    return True
            except Exception:
                pass
            if not reclicou and time.time() - inicio >= timeout / 2:
                reclicou = True
                try:
                    btn = linha.find_elements(By.CSS_SELECTOR, "div.s-expandSidebar, img.s-expandLines")
                    if btn:
                        self.driver.execute_script("arguments[0].click();", btn[0])
                except Exception:
                    pass
            time.sleep(0.5)
        logger.warning("      ⚠️ Campos do item não apareceram após expandir a linha.")
        return False

    def _garantir_campos_item_antes_salvar(self, linha, idx, preco_alvo="", prazo_alvo="", ncm_alvo="",
                                           origem_alvo="", icms_alvo="", rodadas=2):
        """
        Protocolo de segurança antes de clicar em Salvar no item:
        inspeciona todos os campos mapeados; o que estiver vazio ou com valor diferente do esperado
        é preenchido de novo (até `rodadas` vezes). Retorna a lista de campos que continuaram vazios.
        """
        alvos = dict(preco_alvo=preco_alvo, prazo_alvo=prazo_alvo, ncm_alvo=ncm_alvo,
                     origem_alvo=origem_alvo, icms_alvo=icms_alvo)
        for rodada in range(1, rodadas + 1):
            insp = self._inspecionar_campos_linha(linha, **alvos)
            pendentes = {}
            for nome, info in insp.items():
                alvo = str(info.get('valor_alvo') or '').strip()
                if not alvo:
                    continue
                atual = info.get('valor_atual', '')
                tipo = info.get('tipo')
                if info.get('vazio'):
                    pendentes[nome] = info
                elif tipo in ('cf', 'preco') and not self._valores_equivalentes(atual, alvo):
                    pendentes[nome] = dict(info, vazio=True)
                elif tipo == 'prazo' and re.sub(r'\D', '', str(atual)) != re.sub(r'\D', '', self._normalizar_prazo_dias(alvo)):
                    pendentes[nome] = dict(info, vazio=True)
            if not pendentes:
                break
            logger.warning(
                f"      🛡️ Conferência {rodada}/{rodadas} do Item {idx+1}: refazendo {', '.join(pendentes)}..."
            )
            self._repreencher_campos_vazios_linha(linha, pendentes, idx)

        insp_final = self._inspecionar_campos_linha(linha, **alvos)
        vazios = [k for k, v in insp_final.items() if v.get('vazio')]
        if vazios:
            logger.warning(f"      ⚠️ Item {idx+1} segue com campo(s) vazio(s) antes de salvar: {', '.join(vazios)}")
            try:
                self.sio.emit('relatar_progresso', {
                    'mensagem': f"⚠️ Item {idx+1}: campo(s) vazio(s) antes de salvar: {', '.join(vazios)}"
                })
            except Exception:
                pass
        else:
            logger.info(f"      🛡️ Item {idx+1}: todos os campos conferidos antes de salvar.")
        return vazios

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
                        parar_transmissao(self)
                        self.sio.emit('captcha_resolvido_do_robo', {'resolvido': True})
                        resolvido = True
                    break

                if inicio_captcha is None:
                    inicio_captcha = time.time()
                    logger.warning("⚠️ Captcha detectado! Enviando em tempo real para a tela principal dos vendedores...")
                    self.sio.emit('relatar_progresso', {'mensagem': '⚠️ Captcha detectado! Resolva na tela principal de Minhas Cotações...'})
                elif time.time() - inicio_captcha > 300:
                    parar_transmissao(self)
                    self.sio.emit('captcha_resolvido_do_robo', {'resolvido': True})
                    raise Exception("CAPTCHA_TIMEOUT: Captcha expirado sem solução em 5 minutos.")

                elemento_alvo = iframe_desafio if (iframe_desafio and iframe_desafio.is_displayed()) else iframe_caixinha
                if elemento_alvo:
                    ultimo_check_dom = time.time()
                    rect = None
                    try:
                        rect = elemento_alvo.rect
                    except Exception:
                        rect = None

                    while True:
                        aguardando = getattr(self, 'aguardando_clique', None)
                        coords = getattr(self, 'coordenadas_clique', None)
                        if aguardando is not None and aguardando.is_set() and coords:
                            try:
                                cx = int(coords.get('x', 0))
                                cy = int(coords.get('y', 0))
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
                                    largura = (rect or {}).get('width') or (getattr(elemento_alvo, 'size', None) or {}).get('width', 0)
                                    altura = (rect or {}).get('height') or (getattr(elemento_alvo, 'size', None) or {}).get('height', 0)
                                    px = cx - (largura / 2)
                                    py = cy - (altura / 2)
                                    ActionChains(self.driver).move_to_element_with_offset(elemento_alvo, px, py).click().perform()
                            except Exception as e_click:
                                logger.warning(f"Erro ao clicar no Captcha: {e_click}")
                            finally:
                                aguardando.clear()

                        # Transmissão contínua (~60 FPS) numa thread própria, CDP direto: bots/transmissor_captcha.py
                        agora = time.time()
                        if manter_transmissao(self, self.driver, self.sio, rect) is None and agora - ultimo_print >= 0.5:
                            # iframe sem posição: caminho lento antigo (print do elemento)
                            try:
                                png_bytes = getattr(elemento_alvo, 'screenshot_as_png', None)
                                if png_bytes:
                                    png_b64 = base64.b64encode(png_bytes).decode('utf-8')
                                    self.sio.emit('imagem_captcha_do_robo', {'imagem': png_b64})
                            except Exception:
                                pass
                            ultimo_print = agora

                        if inicio_captcha and (time.time() - inicio_captcha > 300):
                            parar_transmissao(self)
                            self.sio.emit('captcha_resolvido_do_robo', {'resolvido': True})
                            raise Exception("CAPTCHA_TIMEOUT: Captcha expirado sem solução em 5 minutos.")

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
                        'input.s-custom_field_5',
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


    def _detectar_erro_salvar_item(self, linha=None):
        """
        Detecta se o Coupa exibiu o alerta de validação:
        <div class="innerbox"><div>Resolva todos os erros e tente outra vez.</div></div>
        """
        texto_alvo = "resolva todos os erros e tente outra vez"
        try:
            # 1. Verifica elementos com classe 'innerbox'
            innerboxes = self.driver.find_elements(By.CSS_SELECTOR, "div.innerbox")
            for ib in innerboxes:
                try:
                    txt = (ib.text or "").strip().lower()
                    if texto_alvo in txt:
                        return True
                except Exception:
                    pass

            # 2. Verifica mensagens flash ou de erro no Coupa
            caixas_erro = self.driver.find_elements(By.CSS_SELECTOR, ".flash_error, .flash_message, .error_messages, div.error")
            for ce in caixas_erro:
                try:
                    txt = (ce.text or "").strip().lower()
                    if texto_alvo in txt:
                        return True
                except Exception:
                    pass

            # 3. Busca por texto direto no DOM
            el_erros = self.driver.find_elements(By.XPATH, "//*[contains(translate(text(), 'RESOLVA', 'resolva'), 'resolva todos os erros e tente outra vez')]")
            for el in el_erros:
                try:
                    if el.is_displayed():
                        return True
                except Exception:
                    pass

            # 4. Se a linha foi informada, verifica dentro da linha
            if linha is not None:
                inners_linha = linha.find_elements(By.CSS_SELECTOR, "div.innerbox, .fieldWithErrors, .error")
                for ib in inners_linha:
                    try:
                        txt = (ib.text or "").strip().lower()
                        if texto_alvo in txt:
                            return True
                    except Exception:
                        pass
        except Exception as e_det:
            logger.debug(f"Aviso ao checar erro do portal: {e_det}")
        return False

    def _obter_valor_input(self, el):
        try:
            val = el.get_attribute("value")
            if val is None:
                val = el.get_property("value")
            return str(val or "").strip()
        except Exception:
            return ""

    def _identificar_rotulo_campo(self, el):
        try:
            label = el.get_attribute("aria-label") or el.get_attribute("placeholder") or ""
            if label.strip():
                return label.strip()

            el_id = el.get_attribute("id")
            if el_id:
                labels = self.driver.find_elements(By.CSS_SELECTOR, f"label[for='{el_id}']")
                if labels and labels[0].text.strip():
                    return labels[0].text.strip()

            pais = el.find_elements(By.XPATH, "ancestor::div[label]//label")
            if pais and pais[0].text.strip():
                return pais[0].text.strip()

            name = el.get_attribute("name") or ""
            if name:
                return name.split(".")[-1].replace("_", " ").title()
        except Exception:
            pass
        return "Campo do Item"

    def _eh_linha_vale_metais(self, linha, cliente_evento=""):
        """
        Detecta se a linha do item utiliza o layout do Vale Metais (Vale Base Metals):
        1. NCM: input.ComboBox__input.s-custom_field_8 (role="combobox", aria-owns="custom_field_8_..._results")
        2. ORIGEM: input.ComboBox__input.s-custom_field_9 (role="combobox", aria-owns="custom_field_9_..._results")
        3. COFINS %: input.s-custom_field_7
        4. IPI %: input.s-custom_field_5
        5. ICMS %: input.s-custom_field_3
        6. PRAZO: input.s-lead_time_input
        7. PREÇO POR UNIDADE: input.s-price_amount_input
        (Nota: no Vale Base Metals também existe input.s-custom_field_1, que é 'Preço Líquido', um input comum e NÃO ComboBox.)

        No perfil VALE normal:
        - Origem é input.ComboBox__input.s-custom_field_1 (ComboBox)
        - COFINS é input.s-custom_field_8 (campo de texto comum, NÃO é ComboBox)
        - NCM é input.ComboBox__input.s-custom_field_9 (ComboBox)
        """
        try:
            cli = str(cliente_evento or getattr(self, '_cliente_evento_atual', '') or '').strip().lower()
            if linha is not None:
                cf8 = (
                    linha.find_elements(By.CSS_SELECTOR, "input.ComboBox__input.s-custom_field_8")
                    or linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_8")
                )
                if cf8:
                    cls8 = str(cf8[0].get_attribute("class") or "")
                    role8 = str(cf8[0].get_attribute("role") or "")
                    owns8 = str(cf8[0].get_attribute("aria-owns") or "")
                    if "ComboBox__input" in cls8 or role8.lower() == "combobox" or owns8:
                        return True
                    # Se s-custom_field_8 existe mas NÃO é ComboBox, é o campo COFINS (%) do perfil VALE normal
                    return False

                # Se s-custom_field_1 for um ComboBox, é o campo Origem do perfil VALE normal
                cf1 = (
                    linha.find_elements(By.CSS_SELECTOR, "input.ComboBox__input.s-custom_field_1")
                    or linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_1")
                )
                if cf1:
                    cls1 = str(cf1[0].get_attribute("class") or "")
                    role1 = str(cf1[0].get_attribute("role") or "")
                    owns1 = str(cf1[0].get_attribute("aria-owns") or "")
                    if "ComboBox__input" in cls1 or role1.lower() == "combobox" or owns1:
                        return False

                if cli in ("vale base metals", "vale metais", "vale base metais"):
                    return True
        except Exception:
            pass
        return False

    def _campo_tem_erro_portal(self, el):
        if el is None:
            return False
        try:
            aria_inv = str(el.get_attribute("aria-invalid") or "").strip().lower()
            if aria_inv == "true":
                return True
            cls = str(el.get_attribute("class") or "").lower()
            if "error" in cls or "invalid" in cls:
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _valor_monetario_valido(valor):
        s = str(valor or "").strip()
        if not s:
            return False
        s_limpo = re.sub(r'[R$\s]', '', s)
        if ',' in s_limpo and '.' in s_limpo:
            s_limpo = s_limpo.replace('.', '').replace(',', '.')
        elif ',' in s_limpo:
            s_limpo = s_limpo.replace(',', '.')
        try:
            return float(s_limpo) > 0
        except Exception:
            return False

    def _motivo_curto_campo(self, nome_campo, info=None, ncm_alvo="", origem_alvo="", preco_alvo="", prazo_alvo="", icms_alvo=""):
        """Traduz um campo com problema em um alerta curto, minimalista e direto para o usuário."""
        info = info or {}
        nome_low = str(nome_campo or "").strip().lower()
        alvo_info = str(info.get('valor_alvo') or "").strip()

        if nome_low == "ncm" or "ncm" in nome_low:
            alvo = alvo_info or str(ncm_alvo or "").strip()
            return "NCM não existe" if alvo else "Faltou o NCM"

        if nome_low == "origem" or "origem" in nome_low:
            alvo = alvo_info or str(origem_alvo or "").strip()
            val_atual = str(info.get('valor_atual') or "").strip()
            if not alvo or not val_atual:
                return "Faltou a origem"
            return "Origem inválida"

        if "preço" in nome_low or "preco" in nome_low or "valor" in nome_low:
            alvo = alvo_info or str(preco_alvo or "").strip()
            return "Valor errado" if alvo else "Faltou o valor"

        if "prazo" in nome_low or "tempo de espera" in nome_low:
            alvo = alvo_info or str(prazo_alvo or "").strip()
            return "Prazo inválido" if alvo else "Faltou o prazo"

        if "icms" in nome_low:
            alvo = alvo_info or str(icms_alvo or "").strip()
            return "ICMS inválido" if alvo else "Faltou o ICMS"

        if "ipi" in nome_low:
            return "Faltou o IPI"
        if "cofins" in nome_low:
            return "Faltou o COFINS"
        if "pis" in nome_low:
            return "Faltou o PIS"
        if "iss" in nome_low:
            return "Faltou o ISS"

        return str(nome_campo or "Erro no campo").strip()

    def _gerar_motivos_curtos_item(self, campos_finais, campos_inspecionados, preco_alvo="", prazo_alvo="", ncm_alvo="", origem_alvo="", icms_alvo=""):
        """Gera lista de motivos curtos e diretos (ex: ['NCM não existe', 'Faltou a origem']) para um item."""
        motivos = []
        campos_inspecionados = campos_inspecionados or {}
        for c_nome in (campos_finais or []):
            if c_nome == "Campos obrigatórios do item não aceitos pelo portal":
                continue
            info_c = campos_inspecionados.get(c_nome)
            m = self._motivo_curto_campo(
                c_nome,
                info=info_c,
                ncm_alvo=ncm_alvo,
                origem_alvo=origem_alvo,
                preco_alvo=preco_alvo,
                prazo_alvo=prazo_alvo,
                icms_alvo=icms_alvo
            )
            if m and m not in motivos:
                motivos.append(m)

        if not motivos:
            # Se o portal recusou mas nenhum campo ficou literalmente em branco, deduz pelos valores informados
            info_ncm = campos_inspecionados.get('NCM') or {}
            info_ori = campos_inspecionados.get('Origem') or {}
            if info_ncm.get('ncm_inexistente') or info_ncm.get('invalido_portal'):
                motivos.append("NCM não existe" if str(ncm_alvo or "").strip() else "Faltou o NCM")
            elif info_ori.get('origem_inexistente') or info_ori.get('invalido_portal'):
                motivos.append("Faltou a origem" if not str(origem_alvo or "").strip() else "Origem inválida")
            elif not str(preco_alvo or "").strip():
                motivos.append("Faltou o valor")
            elif not self._valor_monetario_valido(preco_alvo):
                motivos.append("Valor errado")
            elif not str(prazo_alvo or "").strip():
                motivos.append("Faltou o prazo")
            elif not str(origem_alvo or "").strip() and 'Origem' in campos_inspecionados:
                motivos.append("Faltou a origem")
            elif not str(ncm_alvo or "").strip() and 'NCM' in campos_inspecionados:
                motivos.append("Faltou o NCM")
            elif str(ncm_alvo or "").strip() and 'NCM' in campos_inspecionados:
                motivos.append("NCM não existe")
            else:
                motivos.append("Valor errado")

        return motivos

    def _resumir_erro_curto(self, erro_bruto="", motivos_curtos=None):
        """Retorna mensagem curta, minimalista e direta para exibir em vermelho no card do evento."""
        if motivos_curtos:
            unicos = list(dict.fromkeys(str(m).strip() for m in motivos_curtos if str(m).strip()))
            if unicos:
                return " · ".join(unicos)

        txt = str(erro_bruto or "").strip()
        low = txt.lower()
        if not txt:
            return "Erro ao responder"
        if "ncm não existe" in low or "ncm nao existe" in low:
            return "NCM não existe"
        if "faltou o ncm" in low:
            return "Faltou o NCM"
        if "faltou a origem" in low or ("origem" in low and "vazio" in low):
            return "Faltou a origem"
        if "origem inválida" in low or "origem invalida" in low:
            return "Origem inválida"
        if "valor errado" in low or ("preço" in low and ("erro" in low or "falha" in low)):
            return "Valor errado"
        if "faltou o valor" in low or ("preço" in low and "vazio" in low):
            return "Faltou o valor"
        if "faltou o prazo" in low or ("prazo" in low and ("vazio" in low or "falha" in low)):
            return "Faltou o prazo"
        if "prazo inválido" in low or "prazo invalido" in low:
            return "Prazo inválido"
        if "faltou o icms" in low or ("icms" in low and "vazio" in low):
            return "Faltou o ICMS"
        if "ncm" in low:
            return "NCM não existe"
        if "origem" in low:
            return "Faltou a origem"
        if "preço" in low or "preco" in low:
            return "Valor errado"
        if "prazo" in low:
            return "Faltou o prazo"
        if "não tem botão de responder" in low or "encerrado" in low or "indisponível" in low or "aba de preços" in low:
            return "Evento encerrado"
        if "botão salvar não encontrado" in low:
            return "Erro ao salvar"
        if "desligado" in low:
            return "Robô desligado"
        if "login" in low or "autentica" in low:
            return "Falha no login"
        if "captcha" in low:
            return "Erro no Captcha"
        if "resolva todos os erros" in low:
            return "Valor errado"
        return txt[:48] + ("…" if len(txt) > 48 else "")

    def _inspecionar_campos_linha(self, linha, preco_alvo="", prazo_alvo="", ncm_alvo="", origem_alvo="", icms_alvo="", cliente_evento=""):
        """
        Inspeciona todos os campos da linha do item e retorna dicionário com o status de cada um:
        se está vazio e qual o valor alvo esperado. Suporta tanto o layout Vale comum quanto Vale Metais.
        """
        resultado = {}
        eh_vale_metais = self._eh_linha_vale_metais(linha, cliente_evento)

        if eh_vale_metais:
            # 1. NCM (Vale Metais: custom_field_8 ComboBox)
            try:
                inp_ncm = linha.find_elements(By.CSS_SELECTOR, "input.ComboBox__input.s-custom_field_8") or linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_8")
                if inp_ncm:
                    val = self._obter_valor_input(inp_ncm[0])
                    resultado['NCM'] = {
                        'nome': 'NCM',
                        'elemento': inp_ncm[0],
                        'vazio': (val == ""),
                        'ncm_inexistente': bool(getattr(inp_ncm[0], '_ncm_nao_encontrado', False)),
                        'invalido_portal': self._campo_tem_erro_portal(inp_ncm[0]),
                        'valor_atual': val,
                        'valor_alvo': ncm_alvo,
                        'tipo': 'ncm'
                    }
            except Exception:
                pass

            # 2. Origem (Vale Metais: custom_field_9 ComboBox)
            try:
                inp_origem = linha.find_elements(By.CSS_SELECTOR, "input.ComboBox__input.s-custom_field_9") or linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_9")
                if inp_origem:
                    val = self._obter_valor_input(inp_origem[0])
                    resultado['Origem'] = {
                        'nome': 'Origem',
                        'elemento': inp_origem[0],
                        'vazio': (val == ""),
                        'origem_inexistente': bool(getattr(inp_origem[0], '_origem_nao_encontrada', False)),
                        'invalido_portal': self._campo_tem_erro_portal(inp_origem[0]),
                        'valor_atual': val,
                        'valor_alvo': origem_alvo,
                        'tipo': 'origem'
                    }
            except Exception:
                pass

            # 3. Impostos fixos (COFINS, PIS, IPI, ISS, ICMS ST) + ICMS da planilha
            campos_cf = list(self.CAMPOS_FIXOS_VALE_METAIS) + [
                ('ICMS (%)', self.SELETOR_ICMS_VALE_METAIS, icms_alvo if icms_alvo else "0")
            ]
            for nome_cf, seletor_cf, valor_cf in campos_cf:
                try:
                    inp_cf = linha.find_elements(By.CSS_SELECTOR, seletor_cf)
                    if inp_cf:
                        val = self._obter_valor_input(inp_cf[0])
                        resultado[nome_cf] = {
                            'nome': nome_cf,
                            'elemento': inp_cf[0],
                            'vazio': (val == ""),
                            'invalido_portal': self._campo_tem_erro_portal(inp_cf[0]),
                            'valor_atual': val,
                            'valor_alvo': valor_cf,
                            'tipo': 'cf'
                        }
                except Exception:
                    pass

            # 6. Prazo (Tempo de espera (dias): s-lead_time_input / id^='lead_time_' / name^='lead_time_')
            try:
                el_prazo = self._localizar_input_prazo(linha)
                if el_prazo is not None:
                    val = self._obter_valor_input(el_prazo)
                    resultado['Prazo'] = {
                        'nome': 'Prazo',
                        'elemento': el_prazo,
                        'vazio': (val == ""),
                        'invalido_portal': self._campo_tem_erro_portal(el_prazo),
                        'valor_atual': val,
                        'valor_alvo': prazo_alvo,
                        'tipo': 'prazo'
                    }
            except Exception:
                pass

            # 7. Preço por unidade (por último: s-price_amount_input)
            try:
                inp_preco = linha.find_elements(By.CSS_SELECTOR, "input.s-price_amount_input")
                if inp_preco:
                    val = self._obter_valor_input(inp_preco[0])
                    resultado['Preço'] = {
                        'nome': 'Preço',
                        'elemento': inp_preco[0],
                        'vazio': (val == ""),
                        'invalido_portal': self._campo_tem_erro_portal(inp_preco[0]),
                        'valor_atual': val,
                        'valor_alvo': preco_alvo,
                        'tipo': 'preco'
                    }
            except Exception:
                pass
        else:
            # Layout Vale Padrão
            # 1. NCM
            try:
                inp_ncm = linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_9")
                if inp_ncm:
                    val = self._obter_valor_input(inp_ncm[0])
                    resultado['NCM'] = {
                        'nome': 'NCM',
                        'elemento': inp_ncm[0],
                        'vazio': (val == ""),
                        'ncm_inexistente': bool(getattr(inp_ncm[0], '_ncm_nao_encontrado', False)),
                        'invalido_portal': self._campo_tem_erro_portal(inp_ncm[0]),
                        'valor_atual': val,
                        'valor_alvo': ncm_alvo,
                        'tipo': 'ncm'
                    }
            except Exception:
                pass

            # 2. COFINS (%)
            try:
                inp_cofins = linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_8")
                if inp_cofins:
                    val = self._obter_valor_input(inp_cofins[0])
                    resultado['COFINS (%)'] = {
                        'nome': 'COFINS (%)',
                        'elemento': inp_cofins[0],
                        'vazio': (val == ""),
                        'invalido_portal': self._campo_tem_erro_portal(inp_cofins[0]),
                        'valor_atual': val,
                        'valor_alvo': "3",
                        'tipo': 'cf'
                    }
            except Exception:
                pass

            # 3. PIS (%)
            try:
                inp_pis = linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_7")
                if inp_pis:
                    val = self._obter_valor_input(inp_pis[0])
                    resultado['PIS (%)'] = {
                        'nome': 'PIS (%)',
                        'elemento': inp_pis[0],
                        'vazio': (val == ""),
                        'invalido_portal': self._campo_tem_erro_portal(inp_pis[0]),
                        'valor_atual': val,
                        'valor_alvo': "0,65",
                        'tipo': 'cf'
                    }
            except Exception:
                pass

            # 4. IPI (%)
            try:
                inp_ipi = linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_6")
                if inp_ipi:
                    val = self._obter_valor_input(inp_ipi[0])
                    resultado['IPI (%)'] = {
                        'nome': 'IPI (%)',
                        'elemento': inp_ipi[0],
                        'vazio': (val == ""),
                        'invalido_portal': self._campo_tem_erro_portal(inp_ipi[0]),
                        'valor_atual': val,
                        'valor_alvo': "0",
                        'tipo': 'cf'
                    }
            except Exception:
                pass

            # 5. ISS (%)
            try:
                inp_iss = linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_3")
                if inp_iss:
                    val = self._obter_valor_input(inp_iss[0])
                    resultado['ISS (%)'] = {
                        'nome': 'ISS (%)',
                        'elemento': inp_iss[0],
                        'vazio': (val == ""),
                        'invalido_portal': self._campo_tem_erro_portal(inp_iss[0]),
                        'valor_atual': val,
                        'valor_alvo': "0",
                        'tipo': 'cf'
                    }
            except Exception:
                pass

            # 6. ICMS (%)
            try:
                inp_icms = linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_4")
                if inp_icms:
                    val = self._obter_valor_input(inp_icms[0])
                    resultado['ICMS (%)'] = {
                        'nome': 'ICMS (%)',
                        'elemento': inp_icms[0],
                        'vazio': (val == ""),
                        'invalido_portal': self._campo_tem_erro_portal(inp_icms[0]),
                        'valor_atual': val,
                        'valor_alvo': icms_alvo,
                        'tipo': 'cf'
                    }
            except Exception:
                pass

            # 7. Origem
            try:
                inp_origem = linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_1")
                if inp_origem:
                    val = self._obter_valor_input(inp_origem[0])
                    resultado['Origem'] = {
                        'nome': 'Origem',
                        'elemento': inp_origem[0],
                        'vazio': (val == ""),
                        'origem_inexistente': bool(getattr(inp_origem[0], '_origem_nao_encontrada', False)),
                        'invalido_portal': self._campo_tem_erro_portal(inp_origem[0]),
                        'valor_atual': val,
                        'valor_alvo': origem_alvo,
                        'tipo': 'origem'
                    }
            except Exception:
                pass

            # 8. Prazo
            try:
                el_prazo = self._localizar_input_prazo(linha)
                if el_prazo is not None:
                    val = self._obter_valor_input(el_prazo)
                    resultado['Prazo'] = {
                        'nome': 'Prazo',
                        'elemento': el_prazo,
                        'vazio': (val == ""),
                        'invalido_portal': self._campo_tem_erro_portal(el_prazo),
                        'valor_atual': val,
                        'valor_alvo': prazo_alvo,
                        'tipo': 'prazo'
                    }
            except Exception:
                pass

            # 9. Preço (por último)
            try:
                inp_preco = linha.find_elements(By.CSS_SELECTOR, "input.s-price_amount_input")
                if inp_preco:
                    val = self._obter_valor_input(inp_preco[0])
                    resultado['Preço'] = {
                        'nome': 'Preço',
                        'elemento': inp_preco[0],
                        'vazio': (val == ""),
                        'invalido_portal': self._campo_tem_erro_portal(inp_preco[0]),
                        'valor_atual': val,
                        'valor_alvo': preco_alvo,
                        'tipo': 'preco'
                    }
            except Exception:
                pass

        # 10. Campos explicitamente marcados com erro pelo Coupa (.fieldWithErrors ou .error)
        try:
            campos_erro = linha.find_elements(By.CSS_SELECTOR, ".fieldWithErrors input, .fieldWithErrors select, input.error, select.error")
            for c_err in campos_erro:
                val_err = self._obter_valor_input(c_err)
                if val_err == "":
                    rotulo = self._identificar_rotulo_campo(c_err)
                    if rotulo and rotulo not in resultado:
                        resultado[rotulo] = {
                            'nome': rotulo,
                            'elemento': c_err,
                            'vazio': True,
                            'valor_atual': "",
                            'valor_alvo': "",
                            'tipo': 'extra_obrigatorio'
                        }
        except Exception:
            pass

        return resultado

    def _selecionar_origem_dropdown(self, linha, inp_origem, origem_atual):
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", inp_origem)
            self.driver.execute_script("arguments[0].click();", inp_origem)
            time.sleep(0.3)
        except Exception:
            pass
        try:
            inp_origem.clear()
            time.sleep(0.2)
            if self._obter_valor_input(inp_origem):
                try:
                    inp_origem.send_keys(Keys.CONTROL, "a")
                    inp_origem.send_keys(Keys.BACKSPACE)
                    time.sleep(0.2)
                except Exception:
                    pass
            inp_origem.send_keys(origem_atual)
            time.sleep(1.5)
            selecionou_origem = False
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

            if not selecionou_origem:
                try:
                    if owns_id:
                        setattr(inp_origem, '_origem_nao_encontrada', True)
                except Exception:
                    pass
                inp_origem.send_keys(Keys.ARROW_DOWN)
                time.sleep(0.4)
                inp_origem.send_keys(Keys.ENTER)
            else:
                try:
                    setattr(inp_origem, '_origem_nao_encontrada', False)
                except Exception:
                    pass
        except Exception as e_ori:
            logger.warning(f"      ⚠️ Erro ao selecionar dropdown de origem: {e_ori}")

    def _formatar_ncm(self, ncm_bruto):
        """
        Formata o NCM colocando ponto nos dois últimos pares de dois dígitos:
        Ex: '85411019' -> '8541.10.19', '85234900' -> '8523.49.00'.
        Se já estiver com os pontos nos dois últimos pares (ex: '8523.49.00'), mantém como está.
        """
        s = str(ncm_bruto or "").strip()
        if not s:
            return ""
        # Remove sufixo decimal .0 caso venha como float do Excel/pandas (ex: "85411019.0")
        s = re.sub(r'^(\d{5,})[.,]0+$', r'\1', s)
        # Se já tiver ponto nos dois últimos pares de dois dígitos (ex: "8523.49.00"), mantém como está
        if re.match(r'^\d+\.\d{2}\.\d{2}$', s):
            return s
        digitos = re.sub(r'\D', '', s)
        if len(digitos) >= 5:
            return f"{digitos[:-4]}.{digitos[-4:-2]}.{digitos[-2:]}"
        return s

    def _preencher_ncm_combobox(self, inp_ncm, ncm_atual):
        ncm_formatado = self._formatar_ncm(ncm_atual)
        if not ncm_formatado:
            return False
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", inp_ncm)
            self.driver.execute_script("arguments[0].click();", inp_ncm)
            time.sleep(0.2)
        except Exception:
            pass
        inp_ncm.clear()
        time.sleep(0.2)
        if self._obter_valor_input(inp_ncm):
            try:
                inp_ncm.send_keys(Keys.CONTROL, "a")
                inp_ncm.send_keys(Keys.BACKSPACE)
                time.sleep(0.2)
            except Exception:
                pass
        inp_ncm.send_keys(ncm_formatado)
        time.sleep(1.5)
        selecionou_ncm = False
        tinha_owns = False
        try:
            owns_id = inp_ncm.get_attribute("aria-owns") or ""
            if owns_id:
                tinha_owns = True
                opcoes_ncm = self.driver.find_elements(By.CSS_SELECTOR, f"#{owns_id} li.ComboBox__resultItem, #{owns_id} li")
                ncm_limpo = re.sub(r'\D', '', ncm_formatado)
                for li in opcoes_ncm:
                    txt_li = (getattr(li, 'text', '') or '').strip()
                    if not txt_li:
                        continue
                    txt_limpo = re.sub(r'\D', '', txt_li)
                    if ncm_formatado in txt_li or (ncm_limpo and txt_limpo.startswith(ncm_limpo)):
                        self.driver.execute_script("arguments[0].scrollIntoView({block: 'nearest'}); arguments[0].click();", li)
                        selecionou_ncm = True
                        break
        except Exception:
            selecionou_ncm = False

        try:
            if tinha_owns and not selecionou_ncm:
                setattr(inp_ncm, '_ncm_nao_encontrado', True)
            elif selecionou_ncm:
                setattr(inp_ncm, '_ncm_nao_encontrado', False)
        except Exception:
            pass

        if not selecionou_ncm:
            inp_ncm.send_keys(Keys.ARROW_DOWN)
            time.sleep(0.5)
            inp_ncm.send_keys(Keys.ENTER)
        return selecionou_ncm

    def _normalizar_prazo_dias(self, prazo_bruto):
        """Extrai o número inteiro de dias para o campo s-numberInput s-lead_time_input."""
        s = str(prazo_bruto or "").strip()
        if not s:
            return ""
        # Se vier como float do Excel/pandas (ex: "15.0" ou "15,0") ou com unidade ("15 dias")
        m_int_dec = re.match(r'^(\d+)(?:[.,]0+)?(?:\s*(?:d|dia|dias))?$', s, re.IGNORECASE)
        if m_int_dec:
            return m_int_dec.group(1)
        m_dig = re.search(r'\d+', s)
        return m_dig.group(0) if m_dig else s

    def _localizar_input_prazo(self, linha, idx=None):
        """
        Localiza o campo de prazo de entrega (Tempo de espera (dias)) na linha do item,
        compatível com Vale padrão e Vale Base Metals:
        <input type="text" id="lead_time_831667_8" class="s-numberInput s-lead_time_input" name="lead_time_831667" ...>
        """
        seletores = (
            "input.s-lead_time_input",
            "input.s-numberInput.s-lead_time_input",
            "input[id^='lead_time_']",
            "input[name^='lead_time_']",
            "div.s-lead_time input.s-numberInput",
            "div.s-lead_time input",
        )
        alvos_busca = []
        if linha is not None:
            alvos_busca.append(linha)
        if idx is not None and hasattr(self, 'driver') and self.driver is not None:
            try:
                linhas_dom = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")
                if 0 <= idx < len(linhas_dom) and linhas_dom[idx] is not linha:
                    alvos_busca.append(linhas_dom[idx])
            except Exception:
                pass

        for escopo in alvos_busca:
            for sel in seletores:
                try:
                    encontrados = escopo.find_elements(By.CSS_SELECTOR, sel)
                    if encontrados:
                        for c in encontrados:
                            try:
                                if c.is_displayed():
                                    return c
                            except Exception:
                                pass
                        return encontrados[0]
                except Exception:
                    continue

        if linha is not None:
            try:
                return linha.find_element(By.CSS_SELECTOR, "input.s-lead_time_input")
            except Exception:
                pass
        return None

    def _preencher_prazo_item(self, linha, prazo_bruto, idx=None, el_prazo=None):
        """
        Preenche de forma resiliente o campo de prazo de entrega (lead_time / s-lead_time_input)
        no Coupa / Vale / Vale Base Metals:
        1. Rola até o campo, dá foco e clica;
        2. Faz clear() + send_keys(prazo) + Keys.TAB diretamente no elemento;
        3. Confere se o valor realmente permaneceu no input; caso contrário, usa ActionChains e
           o setter nativo de HTMLInputElement (React _valueTracker + eventos input/change/blur).
        """
        prazo_atual = self._normalizar_prazo_dias(prazo_bruto)
        if not prazo_atual:
            return False

        input_prazo = el_prazo if el_prazo is not None else self._localizar_input_prazo(linha, idx=idx)
        if input_prazo is None:
            raise NoSuchElementException("Campo de prazo (input.s-lead_time_input / lead_time) não encontrado na linha.")

        # 1. Garante visibilidade e foco real no elemento (fecha dropdowns abertos de NCM/Origem)
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'}); arguments[0].focus(); arguments[0].click();",
                input_prazo
            )
            time.sleep(0.25)
        except Exception:
            pass

        # 2. Preenchimento direto via WebDriver (mesmo padrão estável dos campos de impostos e preço)
        preencheu_direto = False
        try:
            input_prazo.clear()
            time.sleep(0.2)
            input_prazo.send_keys(prazo_atual)
            input_prazo.send_keys(Keys.TAB)
            preencheu_direto = True
            time.sleep(0.25)
        except Exception:
            preencheu_direto = False

        def _valor_bate(el_ref):
            try:
                v_lido = str(self._obter_valor_input(el_ref) or "").strip()
                if not v_lido:
                    return False
                return re.sub(r'\D', '', v_lido) == re.sub(r'\D', '', prazo_atual)
            except Exception:
                return False

        # 3. Se o envio direto falhou ou o campo continuou vazio, tenta também via ActionChains
        if not preencheu_direto or not _valor_bate(input_prazo):
            novo_el = self._localizar_input_prazo(linha, idx=idx)
            if novo_el is not None:
                input_prazo = novo_el
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
                time.sleep(0.25)
            except Exception:
                pass

        # 4. Sincronização garantida com o estado interno do React/Coupa (s-numberInput / _valueTracker)
        try:
            self.driver.execute_script("""
                const el = arguments[0];
                const val = String(arguments[1] || '');
                if (!el || typeof el.dispatchEvent !== 'function') return;
                const desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
                if (el._valueTracker) {
                    el._valueTracker.setValue('');
                }
                if (desc && desc.set) {
                    desc.set.call(el, val);
                } else {
                    el.value = val;
                }
                if (el.setAttribute) el.setAttribute('value', val);
                let evInput = new Event('input', { bubbles: true });
                evInput.simulated = true;
                el.dispatchEvent(evInput);
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            """, input_prazo, prazo_atual)
            time.sleep(0.25)
        except Exception:
            pass

        return True

    def _repreencher_campos_vazios_linha(self, linha, campos_inspecionados, idx):
        """
        Para cada campo que está em branco e tem valor alvo definido, realiza o preenchimento novamente,
        garantindo que Prazo e por último Preço por unidade sejam preenchidos no final.
        """
        def _prioridade_campo(item_kv):
            t = item_kv[1].get('tipo', '')
            if t == 'preco':
                return 2
            if t == 'prazo':
                return 1
            return 0

        for nome_campo, info in sorted(campos_inspecionados.items(), key=_prioridade_campo):
            if not info.get('vazio'):
                continue
            valor_alvo = str(info.get('valor_alvo') or "").strip()
            el = info.get('elemento')
            tipo = info.get('tipo')

            if not valor_alvo:
                logger.info(f"      ℹ️ Campo '{nome_campo}' está em branco no Item {idx+1}, mas não há valor informado para preencher.")
                continue

            logger.info(f"      [INFO] Repreenchendo campo em branco do Item {idx+1}: {nome_campo} com '{valor_alvo}'...")
            try:
                if tipo == 'preco':
                    self._preencher_campo_texto_verificado(el, valor_alvo, nome_campo)
                    time.sleep(1.0)

                elif tipo == 'prazo':
                    self._preencher_prazo_item(linha, valor_alvo, idx=idx, el_prazo=el)
                    time.sleep(1.0)

                elif tipo == 'ncm':
                    try:
                        self._preencher_ncm_combobox(el, valor_alvo)
                    except Exception:
                        pass
                    time.sleep(1.0)

                elif tipo == 'origem':
                    self._selecionar_origem_dropdown(linha, el, valor_alvo)
                    time.sleep(1.0)

                else: # cf ou genérico
                    self._preencher_campo_texto_verificado(el, valor_alvo, nome_campo)
                    time.sleep(1.0)

            except Exception as e_rep:
                logger.warning(f"      ⚠️ Falha ao repreencher {nome_campo}: {e_rep}")

    def _registrar_erro_no_evento(self, evento, item_num, erro_portal, campos_vazios):
        try:
            import eventos
            ev_limpo = str(evento).replace("Evento ", "").strip()
            campos_str = ", ".join(campos_vazios)
            resumo = f"Item {item_num} (Cotação {ev_limpo}): '{erro_portal}'. Campo(s) vazio(s): {campos_str}"
            detalhe = {
                'evento': ev_limpo,
                'item': item_num,
                'erro_portal': erro_portal,
                'campos_vazios': campos_vazios,
                'sucesso': False
            }
            servico = "coupa" if "coupa" in self.__class__.__name__.lower() else "vale"
            eventos.registrar(servico, "erro_salvar_item", "erro", resumo, ref=f"Evento {ev_limpo}", detalhe=detalhe)
            logger.info(f"      [EVENTO] Erro registrado no evento {ev_limpo} (logs/eventos.jsonl): {resumo}")
        except Exception as e_reg:
            logger.warning(f"      ⚠️ Não foi possível registrar erro no eventos.jsonl: {e_reg}")

    def _capturar_screenshot_seguro(self):
        try:
            if hasattr(self, 'driver') and self.driver:
                return self.driver.get_screenshot_as_png()
        except Exception as e_sc:
            logger.warning(f"Não foi possível capturar screenshot da tela: {e_sc}")
        return None

    def _notificar_erro_resposta(self, evento, erro, etapa="Processamento da Resposta", print_bytes=None, campos_vazios=None, item_num=None, itens_sucesso=None, total_itens=1):
        if getattr(self, '_email_erro_enviado', False):
            return
        try:
            try:
                from bots.relatorio_email import enviar_email_erro_resposta
            except ImportError:
                try:
                    from relatorio_email import enviar_email_erro_resposta
                except ImportError:
                    from ..relatorio_email import enviar_email_erro_resposta

            nome_bot = "Coupa Bot" if "coupa" in self.__class__.__name__.lower() else "Vale Bot"
            cfg = getattr(self, 'config', {}) or {}
            if not print_bytes:
                print_bytes = self._capturar_screenshot_seguro()

            enviou = enviar_email_erro_resposta(
                nome_bot=nome_bot,
                evento=str(evento),
                erro_detalhado=str(erro),
                etapa=etapa,
                campos_vazios=campos_vazios,
                item_num=item_num,
                print_bytes=print_bytes,
                config=cfg,
                logger_inst=logger,
                itens_sucesso=itens_sucesso,
                total_itens=total_itens,
                destinatario_obrigatorio="vendas7@venturainformatica.com.br"
            )
            if enviou:
                self._email_erro_enviado = True
                logger.info(f"      [EMAIL] E-mail de alerta com print enviado para vendas7@venturainformatica.com.br (Evento {evento}).")
        except Exception as e_mail:
            logger.warning(f"      [AVISO] Erro ao disparar e-mail de alerta de resposta: {e_mail}")

    def _enviar_email_erro_item(self, evento, item_num, erro_portal, campos_vazios, itens_sucesso=None, total_itens=1, print_bytes=None):
        try:
            if not print_bytes:
                print_bytes = self._capturar_screenshot_seguro()
            try:
                from bots.relatorio_email import enviar_email_erro_salvar_item
            except ImportError:
                try:
                    from relatorio_email import enviar_email_erro_salvar_item
                except ImportError:
                    from ..relatorio_email import enviar_email_erro_salvar_item

            nome_bot = "Coupa Bot" if "coupa" in self.__class__.__name__.lower() else "Vale Bot"
            cfg = getattr(self, 'config', {}) or {}
            itens_ok = list(itens_sucesso or [])
            enviou = enviar_email_erro_salvar_item(
                nome_bot=nome_bot,
                evento=str(evento),
                item_num=item_num,
                erro_portal=erro_portal,
                campos_vazios=campos_vazios,
                config=cfg,
                logger_inst=logger,
                itens_sucesso=itens_ok,
                total_itens=total_itens,
                print_bytes=print_bytes,
                destinatario_obrigatorio="vendas7@venturainformatica.com.br"
            )
            if enviou:
                self._email_erro_enviado = True
                logger.info(f"      [EMAIL] E-mail de alerta com print enviado para vendas7@venturainformatica.com.br reportando o erro no Item {item_num}.")
            else:
                logger.warning(f"      [EMAIL] Não foi possível enviar e-mail de alerta para o Item {item_num}.")
        except Exception as e_mail:
            logger.warning(f"      [AVISO] Erro ao disparar e-mail de alerta de salvamento: {e_mail}")

    def _tratar_erro_validacao_item(self, linha, idx, evento, itens, preco_alvo, prazo_alvo, ncm_alvo, origem_alvo, icms_alvo, btn_salvar_item, itens_sucesso):
        logger.warning(f"      [AVISO] Detectado erro de validação do Coupa no Item {idx+1}: 'Resolva todos os erros e tente outra vez'.")
        
        # 1. Garante que a barra lateral de campos do item esteja expandida
        try:
            btn_expandir = linha.find_elements(By.CSS_SELECTOR, "div.s-expandSidebar, img.s-expandLines")
            if btn_expandir:
                self.driver.execute_script("arguments[0].click();", btn_expandir[0])
                time.sleep(1.5)
        except Exception:
            pass

        # 2. Inspeciona campos para identificar os que estão em branco
        campos_iniciais = self._inspecionar_campos_linha(
            linha=linha,
            preco_alvo=preco_alvo,
            prazo_alvo=prazo_alvo,
            ncm_alvo=ncm_alvo,
            origem_alvo=origem_alvo,
            icms_alvo=icms_alvo
        )
        campos_em_branco = [k for k, v in campos_iniciais.items() if v.get('vazio')]
        logger.info(f"      [INFO] Item {idx+1} campos em branco identificados: {', '.join(campos_em_branco) if campos_em_branco else 'nenhum explicitamente vazio'}.")

        # 3. Preenche novamente os campos em branco
        self._repreencher_campos_vazios_linha(linha, campos_iniciais, idx)

        # 4. Tenta salvar o item novamente
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn_salvar_item)
            time.sleep(0.3)
            self.driver.execute_script("arguments[0].click();", btn_salvar_item)
            time.sleep(2.5)
            self._verificar_e_resolver_captcha()
        except Exception as e_re_save:
            logger.warning(f"      ⚠️ Erro ao clicar em Salvar na nova tentativa do Item {idx+1}: {e_re_save}")

        # 5. Verifica se o erro persiste ou se ainda restaram campos em branco
        erro_persistiu = self._detectar_erro_salvar_item(linha)
        campos_reinspecionados = self._inspecionar_campos_linha(
            linha=linha,
            preco_alvo=preco_alvo,
            prazo_alvo=prazo_alvo,
            ncm_alvo=ncm_alvo,
            origem_alvo=origem_alvo,
            icms_alvo=icms_alvo
        )
        campos_ainda_vazios = [k for k, v in campos_reinspecionados.items() if v.get('vazio')]

        if erro_persistiu or campos_ainda_vazios:
            campos_invalidos = [
                k for k, v in campos_reinspecionados.items()
                if v.get('vazio') or v.get('invalido_portal') or v.get('ncm_inexistente') or v.get('origem_inexistente')
            ]
            campos_finais = campos_ainda_vazios or campos_invalidos or campos_em_branco or ["Campos obrigatórios do item não aceitos pelo portal"]
            motivos_curtos = self._gerar_motivos_curtos_item(
                campos_finais=campos_finais,
                campos_inspecionados=campos_reinspecionados,
                preco_alvo=preco_alvo,
                prazo_alvo=prazo_alvo,
                ncm_alvo=ncm_alvo,
                origem_alvo=origem_alvo,
                icms_alvo=icms_alvo
            )
            if not hasattr(self, '_ultimos_motivos_curtos_itens'):
                self._ultimos_motivos_curtos_itens = {}
            self._ultimos_motivos_curtos_itens[idx] = motivos_curtos
            logger.error(f"      [ERRO] Falha no salvamento do Item {idx+1}: persistiu 'Resolva todos os erros e tente outra vez'. Campo(s) vazio(s): {', '.join(campos_finais)} | Motivo: {', '.join(motivos_curtos)}")
            
            # Registra no evento (eventos.py)
            self._registrar_erro_no_evento(evento, idx + 1, "Resolva todos os erros e tente outra vez", campos_finais)
            
            # Envia e-mail de alerta
            self._enviar_email_erro_item(evento, idx + 1, "Resolva todos os erros e tente outra vez", campos_finais, itens_sucesso, len(itens))

            return False, campos_finais

        logger.info(f"      [OK] Item {idx+1} salvo com sucesso após repreenchimento dos campos em branco!")
        return True, []

    def _anexar_datasheets_no_item(self, linha, idx, caminhos_arquivos):
        """Anexa um ou mais arquivos de datasheet diretamente dentro da linha do item clicando em
        <button class="s-addFile buttonAsLink" type="button" aria-label="Adicionar anexo de arquivo" aria-haspopup="dialog"><span>Arquivo</span></button>.
        """
        if not caminhos_arquivos:
            return
        for path in caminhos_arquivos:
            if not path or not os.path.exists(path):
                continue
            try:
                botoes_add_file = linha.find_elements(
                    By.CSS_SELECTOR,
                    "button.s-addFile.buttonAsLink, button.s-addFile, button[aria-label='Adicionar anexo de arquivo']"
                )
                if not botoes_add_file:
                    try:
                        botoes_add_file = linha.find_elements(
                            By.XPATH,
                            ".//button[contains(@class, 's-addFile') or @aria-label='Adicionar anexo de arquivo' or .//span[normalize-space(text())='Arquivo']]"
                        )
                    except Exception:
                        botoes_add_file = []

                try:
                    inputs_antes = list(self.driver.find_elements(By.CSS_SELECTOR, "input[type='file']"))
                except Exception:
                    inputs_antes = []

                # Intercepta clique programático em <input type="file"> para evitar abrir janela nativa do SO
                try:
                    self.driver.execute_script("""
                        window.__ultimoInputFileClicado = null;
                        if (!window.__hookInputFileInstalado) {
                            window.__hookInputFileInstalado = true;
                            const origClick = HTMLInputElement.prototype.click;
                            HTMLInputElement.prototype.click = function() {
                                if (this && this.type === 'file') {
                                    window.__ultimoInputFileClicado = this;
                                    return;
                                }
                                return origClick.apply(this, arguments);
                            };
                        }
                    """)
                except Exception:
                    pass

                if botoes_add_file:
                    btn_add_file = botoes_add_file[0]
                    try:
                        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn_add_file)
                        time.sleep(0.3)
                    except Exception:
                        pass
                    try:
                        self.driver.execute_script("arguments[0].click();", btn_add_file)
                    except Exception:
                        try:
                            btn_add_file.click()
                        except Exception:
                            pass
                    logger.info(f"      📎 Botão 'Adicionar anexo de arquivo' (button.s-addFile) clicado no Item {idx+1}.")
                    time.sleep(1.5)

                inp_alvo = None

                # 1. Verifica se o clique em button.s-addFile acionou um <input type="file"> via JS
                try:
                    inp_hook = self.driver.execute_script("return window.__ultimoInputFileClicado;")
                    if inp_hook is not None and hasattr(inp_hook, 'send_keys'):
                        inp_alvo = inp_hook
                except Exception:
                    pass

                # 2. Procura <input type="file"> dentro da própria linha do item
                if inp_alvo is None:
                    try:
                        inps_linha = linha.find_elements(By.CSS_SELECTOR, "input[type='file'][name='attachment[file]'], input[type='file']")
                        if inps_linha:
                            inp_alvo = inps_linha[-1]
                    except Exception:
                        pass

                # 3. Procura <input type="file"> em modal/dialog aberto pelo botão (aria-haspopup="dialog")
                if inp_alvo is None:
                    try:
                        inps_dialog = self.driver.find_elements(
                            By.CSS_SELECTOR,
                            "[role='dialog'] input[type='file'], .ui-dialog input[type='file'], .modal input[type='file'], .s-attachmentDialog input[type='file']"
                        )
                        if inps_dialog:
                            inp_alvo = inps_dialog[-1]
                    except Exception:
                        pass

                # 4. Procura qualquer novo <input type="file"> criado na página após clicar em button.s-addFile
                if inp_alvo is None:
                    try:
                        inputs_depois = list(self.driver.find_elements(By.CSS_SELECTOR, "input[type='file']"))
                        novos_inputs = [i for i in inputs_depois if i not in inputs_antes]
                        if novos_inputs:
                            inp_alvo = novos_inputs[-1]
                        elif inputs_depois:
                            inp_alvo = inputs_depois[-1]
                    except Exception:
                        pass

                if inp_alvo is not None:
                    inp_alvo.send_keys(path)
                    logger.info(f"      ✅ Datasheet anexado no Item {idx+1}: {os.path.basename(path)}")
                    time.sleep(3.5)

                    # Se abriu um modal de anexo com botão de confirmação/upload, clica para concluir
                    try:
                        botoes_confirmar_modal = self.driver.find_elements(
                            By.CSS_SELECTOR,
                            "[role='dialog'] button.primary, .ui-dialog button.primary, .ui-dialog .ui-dialog-buttonpane button.primary, button.s-uploadAttachment, button.s-attachFile"
                        )
                        for b_conf in botoes_confirmar_modal:
                            try:
                                if b_conf.is_displayed():
                                    self.driver.execute_script("arguments[0].click();", b_conf)
                                    time.sleep(1.5)
                                    break
                            except Exception:
                                pass
                    except Exception:
                        pass
                else:
                    logger.warning(f"      ⚠️ Nenhum campo input[type='file'] encontrado para anexar Datasheet no Item {idx+1}.")
            except Exception as e_ds_it:
                logger.warning(f"      ⚠️ Erro ao anexar Datasheet no Item {idx+1} ({path}): {e_ds_it}")

    def _marcar_containers_anexo_topo(self):
        """
        Identifica e marca no DOM os containers exclusivos dos campos de anexo do topo:
        índice 0 = Datasheet geral, índice 1 = DAV geral.
        Garante que jamais englobe campos de texto/preço/impostos ou linhas de itens (.s-itemsAndServicesLine).
        """
        try:
            self.driver.execute_script("""
                const todosInputs = Array.from(
                    document.querySelectorAll("input[type='file'][name='attachment[file]']")
                ).filter(inp => !inp.closest('.s-itemsAndServicesLine'));

                todosInputs.forEach((inp, idx) => {
                    inp.setAttribute('data-maestro-top-input', String(idx));
                    let atual = inp.parentElement;
                    let melhorContainer = inp.parentElement;
                    while (atual && atual !== document.body) {
                        const inputsFileDentro = atual.querySelectorAll("input[type='file'][name='attachment[file]']");
                        const temLinhaItem = atual.querySelector('.s-itemsAndServicesLine');
                        const temInputsDados = atual.querySelector(
                            "input[type='text']:not([type='file']), input.s-numberInput, input.s-moneyInput, input.ComboBox__input, select, textarea"
                        );
                        if (inputsFileDentro.length > 1 || temLinhaItem || temInputsDados) {
                            break;
                        }
                        melhorContainer = atual;
                        atual = atual.parentElement;
                    }
                    if (melhorContainer) {
                        melhorContainer.setAttribute('data-maestro-top-anexo', String(idx));
                    }
                });
            """)
        except Exception:
            pass

    def _limpar_anexos_topo_campo(self, indice_campo, nome_campo):
        """
        Limpa apenas os arquivos já anexados anteriormente no campo de anexo do topo (0 = Datasheet, 1 = DAV),
        evitando acúmulo de PDFs antigos sem apagar nenhuma outra informação do evento.
        """
        seletores_excluir = (
            "a.rollover_icon.delete_icon, "
            "a.s-deleteAttachment, button.s-deleteAttachment, "
            "a.delete_attachment, button.delete_attachment, "
            "a.remove_attachment, button.s-removeAttachment, "
            ".s-attachmentDelete, a.delete, button.delete, "
            "a[aria-label*='Excluir'], button[aria-label*='Excluir'], "
            "a[aria-label*='Remover'], button[aria-label*='Remover'], "
            "a[aria-label*='Delete'], button[aria-label*='Delete'], "
            "a[aria-label*='Remove'], button[aria-label*='Remove'], "
            "a[title*='Excluir'], button[title*='Excluir'], "
            "a[title*='Remover'], button[title*='Remover'], "
            "a[title*='Delete'], button[title*='Delete']"
        )
        try:
            self._marcar_containers_anexo_topo()
            botoes_remover = []

            # 1. Busca botões de remoção dentro do container exclusivo marcado no DOM
            try:
                containers = self.driver.find_elements(
                    By.CSS_SELECTOR, f"[data-maestro-top-anexo='{indice_campo}']"
                )
                if containers:
                    botoes_remover = containers[0].find_elements(By.CSS_SELECTOR, seletores_excluir)
            except Exception:
                botoes_remover = []

            # 2. Se o input de arquivo em si expuser elementos de exclusão (ou via container pai seguro)
            if not botoes_remover:
                try:
                    inputs_arquivo = self.driver.find_elements(
                        By.CSS_SELECTOR, "input[type='file'][name='attachment[file]']"
                    )
                    if len(inputs_arquivo) > indice_campo:
                        inp_ref = inputs_arquivo[indice_campo]
                        if hasattr(inp_ref, 'find_elements'):
                            botoes_remover = inp_ref.find_elements(By.CSS_SELECTOR, seletores_excluir)
                except Exception:
                    botoes_remover = []

            if botoes_remover:
                logger.info(
                    f"   🧹 Limpando {len(botoes_remover)} anexo(s) antigo(s) do campo {nome_campo} no topo..."
                )
                try:
                    self.driver.execute_script("window.confirm = function() { return true; };")
                except Exception:
                    pass

                for btn_rem in botoes_remover:
                    try:
                        self.driver.execute_script(
                            "arguments[0].scrollIntoView({block: 'center'}); arguments[0].click();",
                            btn_rem
                        )
                        time.sleep(0.8)
                        try:
                            self.driver.switch_to.alert.accept()
                            time.sleep(0.4)
                        except Exception:
                            pass
                    except Exception:
                        pass

            # Limpa também o valor pendente do próprio <input type="file"> do campo
            try:
                inputs_arquivo = self.driver.find_elements(
                    By.CSS_SELECTOR, "input[type='file'][name='attachment[file]']"
                )
                if len(inputs_arquivo) > indice_campo:
                    inp_alvo = inputs_arquivo[indice_campo]
                    if hasattr(inp_alvo, 'clear'):
                        try:
                            inp_alvo.clear()
                        except Exception:
                            pass
                    try:
                        self.driver.execute_script("try { arguments[0].value = ''; } catch(e) {}", inp_alvo)
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception as e_limp:
            logger.warning(f"   ⚠️ Aviso ao limpar campo de {nome_campo} no topo: {e_limp}")

    def _anexar_arquivos_topo_campo(self, indice_campo, caminhos_arquivos, nome_campo):
        """
        Limpa o campo do topo correspondente (somente porque o usuário anexou arquivos para ele no Maestro)
        e anexa 1 ou mais arquivos PDF (suportando múltiplos Datasheets ou múltiplos DAVs).
        """
        arquivos_validos = [p for p in (caminhos_arquivos or []) if p]
        if not arquivos_validos:
            return

        # Só limpa o campo do topo porque o usuário anexou arquivo(s) para este campo no Maestro
        self._limpar_anexos_topo_campo(indice_campo, nome_campo)

        for idx_arq, path in enumerate(arquivos_validos):
            try:
                self._marcar_containers_anexo_topo()

                # Instala hook para impedir abertura de janela nativa do sistema operacional
                try:
                    self.driver.execute_script("""
                        window.__ultimoInputFileClicado = null;
                        if (!window.__hookInputFileInstalado) {
                            window.__hookInputFileInstalado = true;
                            const origClick = HTMLInputElement.prototype.click;
                            HTMLInputElement.prototype.click = function() {
                                if (this && this.type === 'file') {
                                    window.__ultimoInputFileClicado = this;
                                    return;
                                }
                                return origClick.apply(this, arguments);
                            };
                        }
                    """)
                except Exception:
                    pass

                inp_alvo = None

                # 1. Procura no container exclusivo do campo (Datasheet=0 ou DAV=1)
                try:
                    containers = self.driver.find_elements(
                        By.CSS_SELECTOR, f"[data-maestro-top-anexo='{indice_campo}']"
                    )
                    if containers:
                        cont = containers[0]
                        if idx_arq > 0:
                            # Ao anexar o 2º, 3º... arquivo, se houver botão "Arquivo" / "Adicionar anexo" no container, clica nele
                            botoes_add = cont.find_elements(
                                By.CSS_SELECTOR,
                                "button.s-addFile, a.add_attachment, button.s-addAttachment, a.s-addFile, button[aria-label*='Adicionar anexo']"
                            )
                            if botoes_add:
                                try:
                                    self.driver.execute_script("arguments[0].click();", botoes_add[0])
                                    time.sleep(1.0)
                                except Exception:
                                    pass
                        try:
                            inp_hook = self.driver.execute_script("return window.__ultimoInputFileClicado;")
                            if inp_hook is not None and hasattr(inp_hook, 'send_keys'):
                                inp_alvo = inp_hook
                        except Exception:
                            pass

                        if inp_alvo is None:
                            inps_cont = cont.find_elements(
                                By.CSS_SELECTOR, "input[type='file'][name='attachment[file]'], input[type='file']"
                            )
                            if inps_cont:
                                inp_alvo = inps_cont[-1]
                except Exception:
                    pass

                # 2. Fallback direto pelos inputs de anexo do topo
                if inp_alvo is None:
                    inputs_arquivo = self.driver.find_elements(
                        By.CSS_SELECTOR, "input[type='file'][name='attachment[file]']"
                    )
                    if len(inputs_arquivo) > indice_campo:
                        inp_alvo = inputs_arquivo[indice_campo]
                    elif inputs_arquivo:
                        inp_alvo = inputs_arquivo[0]

                if inp_alvo is not None:
                    # Reseta value='' antes de cada send_keys para permitir múltiplos arquivos seguidos no mesmo input
                    if idx_arq > 0:
                        try:
                            self.driver.execute_script("try { arguments[0].value = ''; } catch(e) {}", inp_alvo)
                        except Exception:
                            pass
                    inp_alvo.send_keys(path)
                    logger.info(f"   ✅ {nome_campo} ({idx_arq + 1}/{len(arquivos_validos)}) anexado: {os.path.basename(path)}")
                    time.sleep(4)
            except Exception as e:
                logger.warning(f" Erro ao anexar {nome_campo} ({path}): {e}")

    def _preencher_nome_vendedor(self, nome):
        """Preenche o nome de quem respondeu (input#quote_response_name) no fim da resposta."""
        nome = str(nome or '').strip()
        if not nome:
            logger.warning("   ⚠️ Resposta sem nome do vendedor: campo 'Nome' não preenchido.")
            return False
        try:
            campos = self.driver.find_elements(By.ID, "quote_response_name")
            if not campos:
                logger.warning("   ⚠️ Campo de nome do vendedor (quote_response_name) não encontrado.")
                return False
            campo = campos[0]
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", campo)
            time.sleep(0.3)
            try:
                campo.clear()
                campo.send_keys(nome)
                campo.send_keys(Keys.TAB)
            except Exception:
                self.driver.execute_script("""
                    arguments[0].value = arguments[1];
                    arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                    arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                    arguments[0].dispatchEvent(new Event('blur', { bubbles: true }));
                """, campo, nome)
            logger.info(f"   ✅ Nome do vendedor preenchido: {nome}")
            time.sleep(1)
            return True
        except Exception as e:
            logger.warning(f"   ⚠️ Falha ao preencher o nome do vendedor: {e}")
            return False

    def responder_evento(self, dados):
        evento = str(dados.get('evento', '')).strip()
        lista_precos = dados.get('precos', [])
        lista_prazos = dados.get('prazos', [])
        lista_ncms = dados.get('ncms', [])
        lista_origens = dados.get('origens', [])
        lista_icms = dados.get('icms', [])
        itens = dados.get('itens', lista_precos or [])
        datasheet_por_linha = bool(dados.get('datasheet_por_linha', False))
        raw_ds_por_item = dados.get('datasheets_por_item', []) or []
        
        pasta_anexos = tempfile.mkdtemp(prefix='maestro-resposta-')   # apagada no fim, com ou sem erro
        falhas = []
        motivos_curtos_evento = []
        self._ultimos_motivos_curtos_itens = {}
        itens_salvos_sucesso = []
        prints_itens_map = {}
        self._email_erro_enviado = False
        self._cliente_evento_atual = "Vale"

        logger.info(f"🚀 Iniciando Resposta do Evento {evento}...")

        try:
            ds_paths = [self._gravar_anexo(pasta_anexos, f) for f in dados.get('datasheets', [])]
            dav_paths = [self._gravar_anexo(pasta_anexos, f) for f in dados.get('davs', [])]
            ds_por_item_paths = []
            if datasheet_por_linha and isinstance(raw_ds_por_item, list):
                for lista_arq_it in raw_ds_por_item:
                    if isinstance(lista_arq_it, list):
                        ds_por_item_paths.append([
                            p for p in (self._gravar_anexo(pasta_anexos, f) for f in lista_arq_it) if p
                        ])
                    elif isinstance(lista_arq_it, dict):
                        p = self._gravar_anexo(pasta_anexos, lista_arq_it)
                        ds_por_item_paths.append([p] if p else [])
                    else:
                        ds_por_item_paths.append([])

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
                self._cliente_evento_atual = "Vale Base Metals"
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
                        aceitar_termos(self.driver)   # "Aceita os Termos e condições?" -> Sim, antes do Pretendo
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

            # 5. Anexar MÚLTIPLOS arquivos (limpando o respectivo campo do topo apenas se o usuário anexou arquivo para ele)
            logger.info(
                f" Injetando PDFs ({len(ds_paths)} Datasheets gerais, {len(dav_paths)} DAVs, "
                f"datasheet_por_linha={datasheet_por_linha})..."
            )
            
            if ds_paths and not datasheet_por_linha:
                self._anexar_arquivos_topo_campo(0, ds_paths, "Datasheet")
            
            if dav_paths:
                self._anexar_arquivos_topo_campo(1, dav_paths, "DAV")
            
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
                    ds_item_atual = ds_por_item_paths[idx] if (datasheet_por_linha and idx < len(ds_por_item_paths)) else []
                    se_tem_ds_item = bool(ds_item_atual)
                    
                    if se_tem_preco or se_tem_prazo or se_tem_ds_item:
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
                        # Segurança: só segue quando os campos da linha estiverem na tela
                        self._aguardar_campos_item(linha)

                        if se_tem_ds_item:
                            self._anexar_datasheets_no_item(linha, idx, ds_item_atual)
                        
                        try:
                            # Função interna reescrita para evitar bloqueio por Rate Limit (Bombardeio AJAX)
                            def preencher_cf(seletor, valor_fixo, nome_log):
                                cfs = linha.find_elements(By.CSS_SELECTOR, seletor)
                                if cfs:
                                    # Preenche e lê de volta; insiste via setter nativo se o valor não ficou
                                    if self._preencher_campo_texto_verificado(cfs[0], valor_fixo, nome_log):
                                        logger.info(f"      ✅ {nome_log} preenchido com '{valor_fixo}'")
                                    time.sleep(1.5) # O FREIO MÁGICO: Dá 1.5s para o servidor processar antes de ir pro próximo imposto
                                elif eh_vale_metais:
                                    logger.warning(f"      ⚠️ Campo {nome_log} ({seletor}) não encontrado na linha do Vale Base Metais.")

                            eh_vale_metais = self._eh_linha_vale_metais(linha, self._cliente_evento_atual)
                            ncm_atual = self._formatar_ncm(lista_ncms[idx]) if idx < len(lista_ncms) else ""
                            origem_atual = str(lista_origens[idx]).strip() if idx < len(lista_origens) else ""
                            icms_atual = str(lista_icms[idx]).strip() if idx < len(lista_icms) else ""

                            if eh_vale_metais:
                                logger.info(f"      [VALE METAIS] Preenchendo campos do Item {idx+1} na sequência: NCM -> ORIGEM -> COFINS -> IPI -> ICMS -> PRAZO -> PREÇO POR UNIDADE...")
                                # 0. No Vale Base Metals, s-custom_field_1 é 'Preço Líquido' e NÃO deve ser preenchido.
                                # Se houver algum valor residual nele, limpa o campo.
                                try:
                                    cf1_preco_liq = linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_1")
                                    if cf1_preco_liq:
                                        val_cf1 = self._obter_valor_input(cf1_preco_liq[0])
                                        if val_cf1:
                                            cf1_preco_liq[0].clear()
                                            self.driver.execute_script("""
                                                arguments[0].value = '';
                                                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                                                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                                                arguments[0].dispatchEvent(new Event('blur', { bubbles: true }));
                                            """, cf1_preco_liq[0])
                                            logger.info("      🧹 Campo Preço Líquido (s-custom_field_1) limpo no Vale Metais.")
                                except Exception:
                                    pass

                                # 1. NCM (ComboBox - custom_field_8)
                                if ncm_atual:
                                    ncm_input = linha.find_elements(By.CSS_SELECTOR, "input.ComboBox__input.s-custom_field_8") or linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_8")
                                    if ncm_input:
                                        self._preencher_ncm_combobox(ncm_input[0], ncm_atual)
                                        logger.info(f"      ✅ NCM (Vale Metais - s-custom_field_8) preenchido: {ncm_atual}")
                                        time.sleep(1.5)

                                # 2. ORIGEM (ComboBox - custom_field_9)
                                if origem_atual:
                                    cfs_origem = linha.find_elements(By.CSS_SELECTOR, "input.ComboBox__input.s-custom_field_9") or linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_9")
                                    if cfs_origem:
                                        self._selecionar_origem_dropdown(linha, cfs_origem[0], origem_atual)
                                        logger.info(f"      ✅ Origem (Vale Metais - s-custom_field_9) preenchida e selecionada: {origem_atual}")
                                        time.sleep(1.5)

                                # NCM e Material Origin são obrigatórios (*) no Vale Base Metais
                                if not ncm_atual:
                                    logger.warning(f"      ⚠️ Item {idx+1} sem NCM informado: campo obrigatório no Vale Base Metais.")
                                if not origem_atual:
                                    logger.warning(f"      ⚠️ Item {idx+1} sem Origem informada: campo obrigatório no Vale Base Metais.")

                                # 3. Impostos fixos: COFINS (cf7) -> PIS (cf6) -> IPI* (cf5) -> ISS (cf2) -> ICMS ST (cf4)
                                for nome_cf, seletor_cf, valor_cf in self.CAMPOS_FIXOS_VALE_METAIS:
                                    preencher_cf(seletor_cf, valor_cf, nome_cf)

                                # 4. ICMS % (custom_field_3)
                                preencher_cf(self.SELETOR_ICMS_VALE_METAIS, icms_atual if icms_atual else "0", "ICMS (%)")
                            else:
                                # Layout Vale Padrão
                                # 1. NCM da Planilha Base (ComboBox - custom_field_9)
                                if ncm_atual:
                                    ncm_input = linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_9")
                                    if ncm_input:
                                        self._preencher_ncm_combobox(ncm_input[0], ncm_atual)
                                        logger.info(f"      ✅ NCM da planilha preenchido: {ncm_atual}")
                                        time.sleep(1.5)

                                # 2. Impostos Fixos
                                preencher_cf("input.s-custom_field_8", "3", "COFINS (%)")    # Campo 8
                                preencher_cf("input.s-custom_field_7", "0,65", "PIS (%)")    # Campo 7
                                preencher_cf("input.s-custom_field_6", "0", "IPI (%)")       # Campo 6
                                preencher_cf("input.s-custom_field_5", "0", "Campo 5")       # Campo 5 (sempre 0)
                                preencher_cf("input.s-custom_field_3", "0", "ISS (%)")       # Campo 3 (0 para material)

                                # 3. ICMS se o utilizador informou (Campo 4)
                                if icms_atual:
                                    preencher_cf("input.s-custom_field_4", icms_atual, "ICMS (%)")

                                # 4. Origem (ComboBox - Campo 1: input.ComboBox__input.s-custom_field_1)
                                if origem_atual:
                                    cfs_origem = linha.find_elements(By.CSS_SELECTOR, "input.s-custom_field_1")
                                    if cfs_origem:
                                        self._selecionar_origem_dropdown(linha, cfs_origem[0], origem_atual)
                                        logger.info(f"      ✅ Origem preenchida e selecionada: {origem_atual}")
                                        time.sleep(1.5)

                        except Exception as e:
                            logger.warning(f"       Erro ao processar formulário extra do Coupa: {e}")

                        # 6. PRAZO (Tempo de espera (dias) - s-lead_time_input / lead_time)
                        if se_tem_prazo:
                            prazo_atual = self._normalizar_prazo_dias(lista_prazos[idx])
                            try:
                                self._preencher_prazo_item(linha, prazo_atual, idx=idx)
                                logger.info(f"      ✅ Prazo {prazo_atual} inserido.")
                                time.sleep(1.5) # Freio após injetar o Prazo
                            except Exception as e:
                                falhas.append(f"item {idx+1} (prazo)")
                                m_prz = "Prazo inválido" if prazo_atual else "Faltou o prazo"
                                motivos_curtos_evento.append(f"Item {idx+1}: {m_prz}" if total_linhas > 1 else m_prz)
                                logger.warning(f"      ⚠️ Falha ao inserir prazo: {e}")

                        # 7. PREÇO POR UNIDADE (por último - s-price_amount_input)
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
                                m_prc = "Valor errado" if preco_atual else "Faltou o valor"
                                motivos_curtos_evento.append(f"Item {idx+1}: {m_prc}" if total_linhas > 1 else m_prc)
                                logger.warning(f"      ⚠️ Falha ao inserir preço: {e}")

                        # Verificação extra do Prazo antes de salvar (caso AJAX do Coupa tenha limpado lead_time após o preço/impostos)
                        if se_tem_prazo:
                            try:
                                prazo_esperado = self._normalizar_prazo_dias(lista_prazos[idx])
                                el_prz_check = self._localizar_input_prazo(linha, idx=idx)
                                val_prz_atual = self._obter_valor_input(el_prz_check) if el_prz_check is not None else ""
                                if el_prz_check is not None and re.sub(r'\D', '', str(val_prz_atual or '')) != re.sub(r'\D', '', prazo_esperado):
                                    logger.info(f"      🔄 Reconfirmando preenchimento do Prazo ({prazo_esperado}) antes de salvar o Item {idx+1}...")
                                    self._preencher_prazo_item(linha, prazo_esperado, idx=idx, el_prazo=el_prz_check)
                                    time.sleep(0.8)
                            except Exception:
                                pass

                        # Protocolo de segurança: confere TODOS os campos mapeados e refaz o que ficou vazio/errado
                        try:
                            self._garantir_campos_item_antes_salvar(
                                linha, idx,
                                preco_alvo=str(lista_precos[idx]).strip() if se_tem_preco else "",
                                prazo_alvo=str(lista_prazos[idx]).strip() if se_tem_prazo else "",
                                ncm_alvo=str(lista_ncms[idx]).strip() if idx < len(lista_ncms) else "",
                                origem_alvo=str(lista_origens[idx]).strip() if idx < len(lista_origens) else "",
                                icms_alvo=str(lista_icms[idx]).strip() if idx < len(lista_icms) else "",
                            )
                        except Exception as e_conf:
                            logger.warning(f"      ⚠️ Falha na conferência dos campos do Item {idx+1}: {e_conf}")

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

                                # VERIFICAÇÃO DE ERRO DO COUPA: Resolva todos os erros e tente outra vez
                                if self._detectar_erro_salvar_item(linha):
                                    preco_alvo_it = str(lista_precos[idx]).strip() if idx < len(lista_precos) else ""
                                    prazo_alvo_it = str(lista_prazos[idx]).strip() if idx < len(lista_prazos) else ""
                                    ncm_alvo_it = str(lista_ncms[idx]).strip() if idx < len(lista_ncms) else ""
                                    origem_alvo_it = str(lista_origens[idx]).strip() if idx < len(lista_origens) else ""
                                    icms_alvo_it = str(lista_icms[idx]).strip() if idx < len(lista_icms) else ""

                                    sucesso_retry, campos_vazios_ret = self._tratar_erro_validacao_item(
                                        linha=linha,
                                        idx=idx,
                                        evento=evento,
                                        itens=itens,
                                        preco_alvo=preco_alvo_it,
                                        prazo_alvo=prazo_alvo_it,
                                        ncm_alvo=ncm_alvo_it,
                                        origem_alvo=origem_alvo_it,
                                        icms_alvo=icms_alvo_it,
                                        btn_salvar_item=btn_salvar_item,
                                        itens_sucesso=itens_salvos_sucesso
                                    )
                                    if not sucesso_retry:
                                        motivos_it = (
                                            getattr(self, '_ultimos_motivos_curtos_itens', {}).get(idx)
                                            or self._gerar_motivos_curtos_item(
                                                campos_finais=campos_vazios_ret,
                                                campos_inspecionados={},
                                                preco_alvo=preco_alvo_it,
                                                prazo_alvo=prazo_alvo_it,
                                                ncm_alvo=ncm_alvo_it,
                                                origem_alvo=origem_alvo_it,
                                                icms_alvo=icms_alvo_it
                                            )
                                        )
                                        for m_it in motivos_it:
                                            motivos_curtos_evento.append(f"Item {idx+1}: {m_it}" if total_linhas > 1 else m_it)
                                        falhas.append(f"item {idx+1} (erro ao salvar: campo(s) vazio(s) [{', '.join(campos_vazios_ret)}] - 'Resolva todos os erros e tente outra vez')")
                                    else:
                                        itens_salvos_sucesso.append(idx + 1)
                                else:
                                    itens_salvos_sucesso.append(idx + 1)
                        except Exception as e_save_it:
                            logger.warning(f"      ⚠️ Aviso ao clicar em Salvar no Item {idx+1}: {e_save_it}")
                    else:
                        logger.info(f"    Item {idx+1} ignorado (Você deixou os campos vazios).")
                        
            except Exception as e:
                falhas.append("preenchimento das linhas")
                logger.error(f" Erro crítico no preenchimento das linhas: {e}")

            # 8. Salvar Final (só salva o rascunho; o envio ao cliente é feito por uma pessoa no portal)
            logger.info("✅ Finalizando salvamento do evento...")
            self._preencher_nome_vendedor(dados.get('vendedor'))
            try:
                # "Salvar rascunho" do menu da resposta; sem ele, cai no Salvar comum
                btns_rascunho = self.driver.find_elements(By.CSS_SELECTOR, "button.s-menuSaveResponse")
                btn_salvar = btns_rascunho[0] if btns_rascunho else self.driver.find_element(By.CSS_SELECTOR, "button.s-save")
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn_salvar)
                self.driver.execute_script("arguments[0].click();", btn_salvar)
                logger.info(f"   💾 Clique em '{'Salvar rascunho' if btns_rascunho else 'Salvar'}'.")
                time.sleep(3)
                self._verificar_e_resolver_captcha()

                if self._detectar_erro_salvar_item():
                    logger.warning("[AVISO] Detectado erro no salvamento final: 'Resolva todos os erros e tente outra vez'.")
                    campos_vazios_finais_linhas = []
                    linhas_f = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")
                    for i_f, l_f in enumerate(linhas_f):
                        preco_f = str(lista_precos[i_f]).strip() if i_f < len(lista_precos) else ""
                        prazo_f = str(lista_prazos[i_f]).strip() if i_f < len(lista_prazos) else ""
                        ncm_f = str(lista_ncms[i_f]).strip() if i_f < len(lista_ncms) else ""
                        origem_f = str(lista_origens[i_f]).strip() if i_f < len(lista_origens) else ""
                        icms_f = str(lista_icms[i_f]).strip() if i_f < len(lista_icms) else ""
                        ins = self._inspecionar_campos_linha(
                            l_f,
                            preco_alvo=preco_f,
                            prazo_alvo=prazo_f,
                            ncm_alvo=ncm_f,
                            origem_alvo=origem_f,
                            icms_alvo=icms_f
                        )
                        vazios_l = [
                            k for k, v in ins.items()
                            if v.get('vazio') or v.get('invalido_portal') or v.get('ncm_inexistente') or v.get('origem_inexistente')
                        ]
                        if vazios_l:
                            campos_vazios_finais_linhas.append(f"Item {i_f+1} ({', '.join(vazios_l)})")
                            motivos_l = self._gerar_motivos_curtos_item(
                                campos_finais=vazios_l,
                                campos_inspecionados=ins,
                                preco_alvo=preco_f,
                                prazo_alvo=prazo_f,
                                ncm_alvo=ncm_f,
                                origem_alvo=origem_f,
                                icms_alvo=icms_f
                            )
                            for m_l in motivos_l:
                                motivos_curtos_evento.append(f"Item {i_f+1}: {m_l}" if len(linhas_f) > 1 else m_l)
                    campos_reportar = campos_vazios_finais_linhas or ["Campos obrigatórios do rascunho não aceitos pelo portal"]
                    self._registrar_erro_no_evento(evento, 1, "Resolva todos os erros e tente outra vez", campos_reportar)
                    self._enviar_email_erro_item(evento, 1, "Resolva todos os erros e tente outra vez", campos_reportar, itens_salvos_sucesso, len(itens))
                    falhas.append(f"salvamento final recusado pelo portal ('Resolva todos os erros e tente outra vez'). Campo(s) que ficou(ram) vazio(s): {', '.join(campos_reportar)}")
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
            erro_curto = self._resumir_erro_curto(str(e), motivos_curtos_evento)
            print_erro = self._capturar_screenshot_seguro()
            if not getattr(self, '_email_erro_enviado', False):
                self._notificar_erro_resposta(
                    evento=evento,
                    erro=f"{erro_curto} — {str(e)}" if erro_curto and erro_curto not in str(e) else str(e),
                    etapa="Processamento da Resposta",
                    print_bytes=print_erro,
                    campos_vazios=motivos_curtos_evento or None,
                    itens_sucesso=itens_salvos_sucesso,
                    total_itens=len(dados.get('precos', [])) or 1
                )
            # Se der erro, garante que fecha a aba extra e volta pra principal antes de liberar o próximo da fila
            try:
                if len(self.driver.window_handles) > 1:
                    self.driver.close()
                    self.driver.switch_to.window(self.driver.window_handles[0])
            except Exception:
                pass
            self.sio.emit('tarefa_concluida', {
                'evento': f'Evento {evento}',
                'sucesso': False,
                'erro': erro_curto,
                'erro_detalhado': str(e)
            })
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
