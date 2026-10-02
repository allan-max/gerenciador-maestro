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

logger = logging.getLogger("CoupaApp")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')

if not logger.handlers:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


class InteligenciaMixin:
    def encontrar_vendedor_similar(self, item_dict, dados_ia):
        historico = self.carregar_historico()
        if not historico: return None
        
        import difflib
        import re
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

        # ETAPA 2: Texto do Item
        res = buscar_por_similaridade(texto_item_list, "Texto do Item")
        if res: return res
        
        # ETAPA 3: Descricao
        res = buscar_por_similaridade(descricao_list, "Descricao")
        if res: return res
        
        # ETAPA 4: Texto de Compra
        res = buscar_por_similaridade(texto_compra_list, "Texto de Compra")
        if res: return res
        
        logger.info(f"   ? Nenhum vendedor similar encontrado no historico para este item.")
        return None

    def extrair_dados_gemini(self, texto_item):
        """Usa o Gemini para extrair dados. Se der limite excedido (429), joga pra Groq."""
        
        hoje = datetime.now().date()
        
        # 1. Verifica se já falhou hoje. Se sim, nem tenta o Gemini, vai direto pra Groq!
        if self.usar_groq_hoje and self.dia_falha_gemini == hoje:
            return self.extrair_dados_groq(texto_item)
        elif self.dia_falha_gemini and self.dia_falha_gemini != hoje:
            # Virou o dia! Vamos dar uma nova chance ao Gemini
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
            - Não invente dados. Use apenas o que está no texto.
            
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
            # 2. SE O ERRO FOR DE LIMITE (429 ou RESOURCE_EXHAUSTED), CHAMA A GROQ!
            if "429" in erro_str or "RESOURCE_EXHAUSTED" in erro_str:
                logger.warning("   🔄 Limite diário do Gemini atingido (429). Alternando para GROQ pelo resto do dia!")
                self.usar_groq_hoje = True
                self.dia_falha_gemini = hoje
                return self.extrair_dados_groq(texto_item) # Executa a Groq imediatamente
            else:
                logger.warning(f"   ⚠️ Falha na extração com Gemini: {e}")
                return {"marca": "", "modelo": "", "part_number": ""}
        

    def extrair_dados_groq(self, texto_item):
        """Motor Reserva: Usa a Groq (Llama 3) quando o Gemini atinge o limite."""
        
        # 👇 COLOQUE SUA CHAVE DA GROQ AQUI 👇
        GROQ_API_KEY = os.environ.get("GROQ_API_KEY_COUPA") 
        
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

