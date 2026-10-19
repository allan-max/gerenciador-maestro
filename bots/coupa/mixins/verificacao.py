# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
Módulo Especialista: Coupa Enterprise (Trabalhador do Maestro)
Autor: Allan Simão
"""
import os
import csv
from pathlib import Path
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
from datetime import datetime, timedelta
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
from selenium.common.exceptions import NoAlertPresentException, NoSuchElementException, StaleElementReferenceException
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


MAX_PAGINAS_PADRAO = 60   # teto de segurança; CONFIG.json "verificacao_max_paginas" muda
PASTA_RELATORIOS = Path(__file__).resolve().parents[3] / 'relatorios'
JANELA_NAO_RESPONDIDO_DIAS = 7   # NÃO RESPONDIDO recente continua sendo conferido
ESPERA_PAGINA_S = 20             # quanto esperar a lista trocar depois do clique em "Próximo"
CLIQUES_POR_PAGINA = 2           # o Coupa às vezes ignora o clique: insiste uma vez antes de dar a lista por travada


class VerificacaoMixin:
    def fase_verificacao(self):
        """Lê a coluna de respostas enviadas da lista do Coupa e aplica as regras do planilha_manager.novo_status.
        Para quando acha todas as cotações pendentes, quando a lista acaba ou no teto de páginas."""
        logger.info("--- FASE 2: VERIFICAÇÃO ---")
        try:
            self.garantir_logado(self.EVENTS_URL)
            self.driver.get(self.EVENTS_URL)
            time.sleep(3)

            pendentes = self._pendentes_coupa()
            max_paginas = int(getattr(self, 'config', {}).get('verificacao_max_paginas', MAX_PAGINAS_PADRAO))
            logger.info(f" Verificando {len(pendentes)} cotações pendentes (até {max_paginas} páginas)...")
            lidos, ilegiveis, paginas, erro = self._ler_lista(pendentes, max_paginas)

            # Só números deste robô: um número da Vale igual ao de um evento do Coupa não é tocado
            vistos = {n: q for n, q in lidos.items() if n in self.db_eventos}
            from planilha_manager import planilha_manager
            mudancas = planilha_manager.atualizar_status_respostas(self.NOME_ABA_ALVO, vistos, 1, 9)
            for num, qtd in vistos.items():
                if qtd and not self.db_eventos.get(num, {}).get('respondido'):
                    self.atualizar_status_respondido_jsonl(num)
            if erro:
                raise erro   # o que foi lido já está gravado

            achadas = sum(1 for n in pendentes if n in vistos)
            relatorio = self._relatorio_nao_encontradas(pendentes, vistos)
            resumo = (f"Verificação: {len(vistos)} eventos lidos em {paginas} página(s), {achadas}/{len(pendentes)} pendentes encontradas, "
                      f"{len(mudancas)} status alterados, {ilegiveis} linhas ilegíveis")
            logger.info(f"✅ {resumo}" + (f". Não encontradas: {relatorio}" if relatorio else ""))
            self.sio.emit('relatar_progresso_coupa', {'mensagem': f"✅ {resumo}"})
            self.sio.emit('tarefa_concluida', {'evento': 'Verificação de Status', 'sucesso': True})

        except Exception as e:
            logger.exception(f" Erro geral na Fase de Verificação: {e}")
            self.sio.emit('tarefa_concluida', {'evento': 'Verificação de Status', 'sucesso': False, 'erro': str(e)})

    def _ler_lista(self, pendentes, max_paginas):
        """{número: respostas} da lista do portal. Se o portal falhar no meio, devolve o que já leu e o erro."""
        vistos, ilegiveis, paginas = {}, 0, 0
        ordem = lambda q: -1 if q is None else q   # número repetido na lista: vale a maior quantidade lida
        try:
            while not self.solicitacao_parada and paginas < max_paginas:
                try:
                    WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "table")))
                except Exception:
                    logger.warning(f"    Página {paginas + 1}: tabela não carregou; encerrando a leitura.")
                    break
                rows = self.driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
                if not rows:
                    break
                paginas += 1
                for r in rows:
                    try:
                        num, qtd = self._ler_linha_verificacao(r)
                    except Exception:
                        ilegiveis += 1
                        continue
                    if num and (num not in vistos or ordem(qtd) > ordem(vistos[num])):
                        vistos[num] = qtd

                faltam = sum(1 for n in pendentes if n not in vistos)
                logger.info(f"    Página {paginas}: {len(rows)} linhas, faltam {faltam} pendentes.")
                self.sio.emit('relatar_progresso_coupa', {'mensagem': f"🔍 Verificando página {paginas} (faltam {faltam} pendentes)..."})
                if not faltam:
                    logger.info("    Todas as pendentes encontradas.")
                    break
                if not self._proxima_pagina():
                    break
        except Exception as e:
            logger.exception(f"Leitura interrompida na página {paginas}; gravando o que já foi lido.")
            return vistos, ilegiveis, paginas, e
        return vistos, ilegiveis, paginas, None

    def _pendentes_coupa(self):
        """Cotações deste robô (as do eventos.jsonl do Coupa) sem resposta confirmada: vazias, RASCUNHO e
        NÃO RESPONDIDO que venceram há poucos dias (prazo prorrogado ou resposta enviada no limite)."""
        from planilha_manager import planilha_manager, status_atual, data_vencimento, RASCUNHO, NAO_RESPONDIDO
        planilha_manager.iniciar()
        limite = datetime.now().date() - timedelta(days=JANELA_NAO_RESPONDIDO_DIAS)

        def alvo(r):
            s = status_atual(r.get('RESPOSTA'))
            d = data_vencimento(r.get('VENCIMENTO'))
            return s in ('', RASCUNHO) or (s == NAO_RESPONDIDO and d is not None and d >= limite)
        return {str(r.get('COTAÇÃO')).strip(): r for r in planilha_manager.cotacoes.get('COTAÇÃO', [])
                if isinstance(r, dict) and str(r.get('COTAÇÃO') or '').strip() in self.db_eventos and alvo(r)}

    @staticmethod
    def _ler_linha_verificacao(linha):
        """(número, respostas enviadas). Célula ausente ou sem número = None: não dá para afirmar que é 0."""
        num = linha.find_element(By.CSS_SELECTOR, "a span.dt_open_link").text.strip()
        try:
            qtd = int(linha.find_element(By.CLASS_NAME, "s-datatable-cell-num_responses").text.strip())
        except (NoSuchElementException, ValueError):
            qtd = None
        return num, qtd

    def _relatorio_nao_encontradas(self, pendentes, vistos):
        faltando = [r for n, r in pendentes.items() if n not in vistos]
        if not faltando:
            return None
        PASTA_RELATORIOS.mkdir(exist_ok=True)
        caminho = PASTA_RELATORIOS / f"verificacao_coupa_{datetime.now():%Y-%m-%d}.csv"
        with open(caminho, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(['COTAÇÃO', 'VENCIMENTO', 'RESPOSTA', 'VENDEDOR'])
            w.writerows([r.get('COTAÇÃO'), r.get('VENCIMENTO'), r.get('RESPOSTA') or '', r.get('VENDEDOR')] for r in faltando)
        return str(caminho)

    def _numeros_da_pagina(self):
        """Números da lista na tela, para saber se a página trocou. None enquanto a tabela está sendo redesenhada."""
        try:
            return tuple(r.find_element(By.CSS_SELECTOR, "a span.dt_open_link").text.strip()
                         for r in self.driver.find_elements(By.CSS_SELECTOR, "table tbody tr"))
        except (StaleElementReferenceException, NoSuchElementException):
            return None   # sessão caída e outros erros sobem: a verificação termina com erro

    def _esperar_pagina_mudar(self, antes):
        fim = time.time() + ESPERA_PAGINA_S
        while time.time() < fim:
            time.sleep(1)
            agora = self._numeros_da_pagina()
            if agora and agora != antes:
                time.sleep(1)   # deixa a tabela terminar de desenhar
                return True
        return False

    def _proxima_pagina(self):
        """Avança e confirma que a lista mudou. Antes esperava 4 s fixos: quando o Coupa não trocava a página,
        a mesma página era relida até o teto (19/10: 200 páginas, só 986 eventos diferentes)."""
        antes = self._numeros_da_pagina()
        for tentativa in range(1, CLIQUES_POR_PAGINA + 1):
            if not self._clicar_proxima():
                return False
            if self._esperar_pagina_mudar(antes):
                return True
            logger.warning(f"    A lista não mudou depois do 'Próximo' (tentativa {tentativa}/{CLIQUES_POR_PAGINA}).")
        logger.warning("    Paginação travada: o Coupa continua mostrando a mesma página. Encerrando a leitura com o que já foi lido.")
        return False

    def _clicar_proxima(self):
        try:
            next_btn = None
            seletores = [
                (By.CSS_SELECTOR, "a.next_page"),
                (By.LINK_TEXT, "Próximo"),
                (By.LINK_TEXT, "Next"),
                (By.XPATH, "//a[contains(@class, 'next_page')]")
            ]
            for metodo, query in seletores:
                try:
                    el = self.driver.find_element(metodo, query)
                    if el.is_displayed():
                        next_btn = el; break
                except Exception: continue

            if not next_btn:
                logger.info("    Fim da paginação (Botão não encontrado).")
                return False

            cls = next_btn.get_attribute("class") or ""
            try: p_cls = next_btn.find_element(By.XPATH, "..").get_attribute("class") or ""
            except Exception: p_cls = ""
            if "disabled" in cls or "disabled" in p_cls:
                logger.info("    Fim da paginação (Botão desabilitado).")
                return False

            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_btn)
            time.sleep(1)
            self.driver.execute_script("arguments[0].click();", next_btn)
            return True
        except Exception as e:
            logger.error(f" Erro crítico na paginação da verificação: {e}")
            return False
