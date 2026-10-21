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


PASTA_RELATORIOS = Path(__file__).resolve().parents[3] / 'relatorios'
JANELA_NAO_RESPONDIDO_DIAS = 7   # NÃO RESPONDIDO recente continua sendo conferido


class VerificacaoMixin:
    def fase_verificacao(self):
        """Lê a coluna de respostas enviadas da lista do Coupa e aplica as regras do planilha_manager.novo_status.
        Para quando acha todas as cotações pendentes, quando a lista acaba ou no teto de páginas."""
        logger.info("--- FASE 2: VERIFICAÇÃO ---")
        try:
            self.garantir_logado(self.EVENTS_URL)
            pendentes = self._pendentes_coupa()
            import ambiente
            max_paginas = ambiente.paginas_verificacao('coupa')   # CONFIG.json, editável no painel (padrão 60)
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
            import eventos
            eventos.registrar('coupa', 'verificacao_concluida', 'ok', resumo, detalhe={'encontradas': achadas, 'total': len(pendentes), 'mudancas': len(mudancas), 'paginas': paginas})
            self.sio.emit('relatar_progresso_coupa', {'mensagem': f"✅ {resumo}"})
            self.sio.emit('tarefa_concluida', {'evento': 'Verificação de Status', 'sucesso': True})

        except Exception as e:
            logger.exception(f" Erro geral na Fase de Verificação: {e}")
            self.sio.emit('tarefa_concluida', {'evento': 'Verificação de Status', 'sucesso': False, 'erro': str(e)})

    def _ler_lista(self, pendentes, max_paginas):
        """{número: respostas} da lista do portal. Se o portal falhar no meio, devolve o que já leu e o erro."""
        vistos, ilegiveis, paginas, anterior = {}, 0, 0, None
        ordem = lambda q: -1 if q is None else q   # número repetido na lista: vale a maior quantidade lida
        try:
            while not self.solicitacao_parada and paginas < max_paginas:
                # Página explícita, como na extração: sem ?page= o Coupa reabre a última página visitada (a extração
                # para na 7 ou 8) e as cotações mais novas, das primeiras páginas, nunca eram lidas (20/10).
                self.driver.get(f"{self.EVENTS_URL}?page={paginas + 1}")
                if not self._esperar_tabela(paginas + 1):
                    logger.warning(f"    Página {paginas + 1}: tabela não carregou nem na segunda tentativa; encerrando a leitura.")
                    break
                rows = self.driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
                if not rows:
                    break
                numeros = self._numeros_da_pagina()
                if numeros and numeros == anterior:
                    logger.info(f"    Página {paginas + 1} igual à anterior: fim da lista.")
                    break
                anterior = numeros
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
                if not self._tem_proxima():
                    logger.info("    Fim da lista.")
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
        caminho = PASTA_RELATORIOS / f"verificacao_coupa_{datetime.now():%Y-%m-%d}.csv"
        if not faltando:
            caminho.unlink(missing_ok=True)   # relatório de uma rodada anterior do dia não vale mais
            return None
        PASTA_RELATORIOS.mkdir(exist_ok=True)
        with open(caminho, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(['COTAÇÃO', 'VENCIMENTO', 'RESPOSTA', 'VENDEDOR'])
            w.writerows([r.get('COTAÇÃO'), r.get('VENCIMENTO'), r.get('RESPOSTA') or '', r.get('VENDEDOR')] for r in faltando)
        return str(caminho)

    def _numeros_da_pagina(self):
        """Números da lista na tela: página igual à anterior = o Coupa repetiu a última (fim da lista)."""
        try:
            return tuple(r.find_element(By.CSS_SELECTOR, "a span.dt_open_link").text.strip()
                         for r in self.driver.find_elements(By.CSS_SELECTOR, "table tbody tr"))
        except (StaleElementReferenceException, NoSuchElementException):
            return None   # sessão caída e outros erros sobem: a verificação termina com erro

    def _esperar_tabela(self, pagina):
        """Coupa lento às vezes não entrega a página (20/10, página 27): recarrega uma vez antes de desistir."""
        for tentativa in (1, 2):
            try:
                WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "table")))
                return True
            except Exception:
                if tentativa == 1:
                    logger.warning(f"    Página {pagina}: tabela não carregou; tentando de novo.")
                    self.driver.get(f"{self.EVENTS_URL}?page={pagina}")
        return False

    def _tem_proxima(self):
        try:
            return bool(self.driver.find_element(By.CSS_SELECTOR, "a.next_page:not(.disabled)"))
        except NoSuchElementException:
            return False
