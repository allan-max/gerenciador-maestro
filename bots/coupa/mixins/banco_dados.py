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


class Banco_dadosMixin:
    def criar_backup_inicial(self):
        if not os.path.exists(self.ARQUIVO_PLANILHA_CONTROLE): return
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            nm = f"BACKUP_SESSAO_{ts}.xlsx"
            shutil.copy2(self.ARQUIVO_PLANILHA_CONTROLE, os.path.join(self.PASTA_BACKUPS, nm))
            logger.info(f" Backup INICIAL criado: {nm}")
        except Exception as e: logger.warning(f" Falha no backup inicial: {e}")

    def carregar_memoria_part_numbers(self):
        try:
            if os.path.exists(self.ARQUIVO_MEMORIA_PART_NUMBERS):
                with open(self.ARQUIVO_MEMORIA_PART_NUMBERS, 'r', encoding='utf-8') as f: self.memoria_associativa = json.load(f)
            else:
                self.memoria_associativa = {}
                with open(self.ARQUIVO_MEMORIA_PART_NUMBERS, 'w', encoding='utf-8') as f: json.dump({}, f)
            logger.info(f" Memória carregada: {len(self.memoria_associativa)} padrões.")
        except: self.memoria_associativa = {}

    def salvar_memoria_part_numbers(self):
        try:
            with open(self.ARQUIVO_MEMORIA_PART_NUMBERS, 'w', encoding='utf-8') as f: json.dump(self.memoria_associativa, f, indent=4, ensure_ascii=False)
        except: pass

    def aprender_padroes_historico(self):
        logger.info(" Analisando histórico da planilha...")
        if not os.path.exists(self.ARQUIVO_PLANILHA_CONTROLE): return
        df = self._carregar_dataframe_aba_correta()
        if df is None or len(df.columns) < 8: return
        novos = 0
        try:
            for _, row in df.iterrows():
                mod = str(row.iloc[6]).strip() if pd.notna(row.iloc[6]) else ""
                mar = str(row.iloc[7]).strip() if pd.notna(row.iloc[7]) else ""
                if len(mod) > 2 and len(mar) > 2 and self._validar_txt(mod) and self._validar_txt(mar):
                    m_clean = re.sub(r'\(Item \d+\)\s*', '', mod).strip()
                    mr_clean = re.sub(r'\(Item \d+\)\s*', '', mar).strip()
                    if m_clean and mr_clean and m_clean not in self.memoria_associativa:
                        self.memoria_associativa[m_clean] = mr_clean; novos += 1
            if novos > 0: self.salvar_memoria_part_numbers()
        except: pass

    def sincronizar_base_de_marcas(self):
        logger.info(" Sincronizando marcas...")
        if not os.path.exists(self.ARQUIVO_PLANILHA_CONTROLE): return
        df = self._carregar_dataframe_aba_correta()
        if df is None: return
        try:
            base = set([str(x).strip().lower() for x in pd.read_excel(self.ARQUIVO_BASE_MARCAS).iloc[:,0] if pd.notna(x)])
            novas = []
            if len(df.columns) >= 8:
                for item in df.iloc[:, 7]:
                    if pd.notna(item):
                        txt = re.sub(r'\(Item \d+\)\s*', '', str(item).strip()).strip()
                        if self._validar_txt(txt) and txt.lower() not in base:
                            novas.append(txt); base.add(txt.lower())
            if novas:
                d_old = pd.read_excel(self.ARQUIVO_BASE_MARCAS)
                pd.concat([d_old, pd.DataFrame({d_old.columns[0]: novas})], ignore_index=True).to_excel(self.ARQUIVO_BASE_MARCAS, index=False)
        except: pass

    def carregar_banco_dados(self):
        self.db_eventos = {}
        if os.path.exists(self.ARQUIVO_JSONL):
            with open(self.ARQUIVO_JSONL, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        import json
                        d = json.loads(line)
                        if d.get('titulo'): self.db_eventos[d['titulo']] = d
                    except: pass
        logger.info(f" Banco interno carregado: {len(self.db_eventos)} registros.")

    def salvar_evento_jsonl(self, numero: str, respondido: bool = False, vencimento_tabela: str = ""):
        try:
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

    def atualizar_status_respondido_jsonl(self, numero: str):
        if numero not in self.db_eventos: return
        self.db_eventos[numero]['respondido'] = True
        try:
            temp = self.ARQUIVO_JSONL + ".tmp"
            with open(temp, 'w', encoding='utf-8') as f:
                for _, d in self.db_eventos.items(): f.write(json.dumps(d) + '\n')
            shutil.move(temp, self.ARQUIVO_JSONL)
        except: pass

    def evento_ja_processado(self, num: str) -> bool: return num in self.db_eventos

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


    def salvar_pedido_database(self, pedido_dict: dict):
        arq = getattr(self, 'ARQUIVO_PEDIDOS_JSON', None)
        if not arq: return
        
        try:
            import json, os
            os.makedirs(os.path.dirname(arq), exist_ok=True)
            dados = []
            if os.path.exists(arq):
                try:
                    with open(arq, 'r', encoding='utf-8') as f:
                        dados = json.load(f)
                except:
                    dados = []
            
            # Evitar duplicação (atualizar se já existir)
            existe = False
            for i, p in enumerate(dados):
                if p.get('pedido') == pedido_dict.get('pedido'):
                    dados[i] = pedido_dict
                    existe = True
                    break
            
            if not existe:
                dados.append(pedido_dict)
                
            with open(arq, 'w', encoding='utf-8') as f:
                json.dump(dados, f, indent=4, ensure_ascii=False)
                
            logger.info(f" Pedido {pedido_dict.get('pedido')} salvo no banco de dados JSON.")
        except Exception as e:
            logger.error(f" Erro ao salvar pedido no JSON: {e}")
