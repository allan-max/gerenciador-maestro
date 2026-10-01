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


from .mixins.utilitarios import UtilitariosMixin
from .mixins.banco_dados import Banco_dadosMixin
from .mixins.planilhas import PlanilhasMixin
from .mixins.autenticacao import AutenticacaoMixin
from .mixins.extracao import ExtracaoMixin
from .mixins.pedidos import PedidosMixin
from .mixins.inteligencia import InteligenciaMixin
from .mixins.verificacao import VerificacaoMixin
from .mixins.resposta import RespostaMixin

class CoupaScraper(UtilitariosMixin, Banco_dadosMixin, PlanilhasMixin, AutenticacaoMixin, ExtracaoMixin, PedidosMixin, InteligenciaMixin, VerificacaoMixin, RespostaMixin):
    def __init__(self, sio_instance, evento_clique, dict_coordenadas):
        self.sio = sio_instance
        self.aguardando_clique = evento_clique
        self.coordenadas_clique = dict_coordenadas

        self.config = {}
        try:
            if os.path.exists('CONFIG.json'):
                with open('CONFIG.json', 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
        except Exception:
            pass

        if os.environ.get("URL_SERVIDOR") == "http://localhost:8000":
            base_test = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            self.config["caminho_excel"] = os.path.join(base_test, "teste.xlsx")
            self.config["caminho_database_coupa"] = os.path.join(base_test, "banco_teste.jsonl")
            self.config["caminho_pedidos_json"] = os.path.join(base_test, "pedidos_teste.json")

        self.BASE_DIR = self.config.get("BASE_DIR", r"\\SERVIDOR2\Publico\ALLAN")
        self.PASTA_RELATORIOS = self.config.get("pasta_coupa_vale", os.path.join(self.BASE_DIR, "eventos do coupa"))
        self.PASTA_BLOQUEADOS = self.config.get("PASTA_BLOQUEADOS", os.path.join(self.BASE_DIR, "eventosBLOCK"))
        self.PASTA_DATABASE = self.config.get("PASTA_DATABASE", os.path.join(self.BASE_DIR, "database"))
        self.PASTA_BACKUPS = os.path.join(self.PASTA_DATABASE, "backups_excel")
        self.PASTA_LOGS = os.path.join(self.PASTA_DATABASE, "logs")
        
        self.ARQUIVO_JSONL = self.config.get("caminho_database_coupa", os.path.join(self.PASTA_DATABASE, "eventos.jsonl"))
        self.ARQUIVO_PEDIDOS_JSON = self.config.get("caminho_pedidos_json", os.path.join(self.PASTA_DATABASE, "pedidos.json"))
        self.ARQUIVO_MARCAS_BLOQUEADAS = os.path.join(self.PASTA_DATABASE, "marcas_bloqueadas.xlsx")
        self.ARQUIVO_PLANILHA_CONTROLE = self.config.get("caminho_excel", r"\\SERVIDOR2\Publico\PLANILHA DE CONTROLE VALE - ESTAGIARIOS (copia 1).xlsx")
        self.solicitacao_parada = False

        self.ARQUIVO_BASE_MARCAS = os.path.join(self.PASTA_DATABASE, "PLANILHA DE MARCAS.xlsx")
        self.ARQUIVO_MEMORIA_PART_NUMBERS = os.path.join(self.PASTA_DATABASE, "memoria_part_numbers.json")
        
        self.MODO_TESTE = False
        self.NOME_ABA_ALVO = "COTAÇãO" 
        self.BASE_SUPPLIER_URL = "https://supplier.coupahost.com"
        self.LOGIN_URL = f"{self.BASE_SUPPLIER_URL}/sessions/new"
        self.EVENTS_URL = "https://vale.coupahost.com/quote_supplier_land"

        self.TAMANHO_DO_LOTE = 15
        self.db_eventos: Dict[str, Dict] = {} 
        self.driver = None
        self.wait = None
        self.dados_processados_lote = []

        self.usar_groq_hoje = False
        self.dia_falha_gemini = None

    def iniciar_tarefa(self, modo, dados_extras=None):
        logger.info(f"📥 Ordem recebida da Nuvem! Comando: '{modo}' | Tem dados extras? {'Sim' if dados_extras else 'Não'}")
        
        # 👇 1. A NOVA BLINDAGEM DE PARADA IMEDIATA 👇
        # Alterado de "parar_extracao" para "solicitar_parada"
        if modo == "solicitar_parada":
            self.solicitacao_parada = True
            logger.info("🛑 Comando de Parada Segura ativado! O robô abortará no próximo ciclo.")
            self.sio.emit('relatar_progresso_coupa', {'mensagem': "🛑 Parando extração de forma segura..."})
            return
        try:
            self.solicitacao_parada = False # Reset before ANY task starts!
            # 👇 2. FLUXO NORMAL DE TRABALHO 👇
            if modo not in ["responder", "extrair_pedidos"]:
                self.navegacao_aquecimento()

            if modo == "extrair":
                self.varrer_painel_eventos()
                
            elif modo == "extrair_pedidos":
                self.extrair_pedidos()
                
            elif modo == "verificar":
                self.fase_verificacao()
                
            elif modo == "responder":
                if dados_extras:
                    self.responder_evento(dados_extras)
                else:
                    logger.error("❌ ERRO: O painel mandou responder, mas os dados vieram vazios!")
            else:
                logger.warning(f"⚠️ Comando ignorado. O robô não sabe o que fazer com o modo: '{modo}'")
                
        except Exception as e: 
            logger.error(f"❌ Erro Crítico na Tarefa: {e}")
            self.sio.emit('tarefa_concluida', {'evento': f'Comando {modo}', 'sucesso': False, 'erro': str(e)})

    # 5. RESPONDER EVENTO (ATUALIZADO PARA BASE64 E NUVEM)
