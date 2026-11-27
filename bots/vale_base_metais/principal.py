# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
Módulo Especialista: Vale Base Metals (A partir do Sourcing Privado)
Construído do Zero com Paginação e Dropdown Específicos.
"""
import os
from dotenv import load_dotenv
from nucleo.ambiente import arquivo_dado
load_dotenv(arquivo_dado('.env'))   # dados/.env
import logging
import json
from typing import Dict

from nucleo import ambiente
from nucleo import log_robos

logger = logging.getLogger("ValeApp")
logger.setLevel(logging.INFO)

# 👇 GARANTE A GRAVAÇÃO SEM APAGAR O HISTÓRICO 👇
log_robos.configurar(logger, 'log-vale.txt')   # data, máquina e rotação; sem o servidor, só console

from .mixins.banco_dados import Banco_dadosMixin
from .mixins.autenticacao import AutenticacaoMixin
from .mixins.extracao import ExtracaoMixin
from .mixins.planilhas import PlanilhasMixin
from .mixins.inteligencia import InteligenciaMixin
from .mixins.verificacao import VerificacaoMixin
from .mixins.resposta import RespostaMixin


# Os mixins da Vale vêm antes do RespostaMixin (que herda o do Coupa): o que a Vale define vale sobre o do Coupa
class ValeScraper(Banco_dadosMixin, AutenticacaoMixin, ExtracaoMixin, PlanilhasMixin, InteligenciaMixin,
                  VerificacaoMixin, RespostaMixin):
    def __init__(self, sio_instance, evento_clique, dict_coordenadas):
        # --- FERRAMENTAS DO MAESTRO ---
        self.sio = sio_instance
        self.aguardando_clique = evento_clique
        self.coordenadas_clique = dict_coordenadas
        self.solicitacao_parada = False

        # --- CARREGAR CONFIG ---
        self.config = {}
        try:
            if os.path.exists(os.path.join('dados', 'CONFIG.json')):
                with open(os.path.join('dados', 'CONFIG.json'), 'r', encoding='utf-8-sig') as f:   # aceita BOM (Bloco de Notas)
                    self.config = json.load(f)
        except Exception:
            pass

        from nucleo import ambiente
        if ambiente.modo_teste():   # testes não tocam a base de produção
            self.config["BASE_DIR"] = ambiente.BASE_TESTE
            self.config["pasta_coupa_vale"] = os.path.join(ambiente.BASE_TESTE, "eventos do coupa")
            cand = os.path.join(ambiente.RAIZ, "teste", "teste.xlsx")
            self.config["caminho_excel"] = cand if os.path.exists(cand) else os.path.join(ambiente.RAIZ, "teste.xlsx")
            for chave in ("PASTA_DATABASE", "PASTA_BLOQUEADOS"):
                self.config.pop(chave, None)

        # --- PASTAS ---
        self.BASE_DIR = self.config.get("BASE_DIR", r"\\SERVIDOR2\Publico\ALLAN")
        self.PASTA_RELATORIOS = self.config.get("pasta_coupa_vale", os.path.join(self.BASE_DIR, "eventos do coupa"))
        self.PASTA_BLOQUEADOS = self.config.get("PASTA_BLOQUEADOS", os.path.join(self.BASE_DIR, "eventosBLOCK"))
        self.PASTA_DATABASE = self.config.get("PASTA_DATABASE", os.path.join(self.BASE_DIR, "database"))
        self.PASTA_BACKUPS = os.path.join(self.PASTA_DATABASE, "backups_excel")
        self.PASTA_LOGS = os.path.join(self.PASTA_DATABASE, "logs")
        
        self.ARQUIVO_JSONL = os.path.join(self.PASTA_DATABASE, "eventos_nimbi.jsonl")
        self.ARQUIVO_MARCAS_BLOQUEADAS = os.path.join(self.PASTA_DATABASE, "marcas_bloqueadas.xlsx")
        self.ARQUIVO_PLANILHA_CONTROLE = self.config.get("caminho_excel", r"\\SERVIDOR2\Publico\PLANILHA DE CONTROLE VALE - ESTAGIARIOS (copia 1).xlsx")
        self.ARQUIVO_BASE_MARCAS = os.path.join(self.PASTA_DATABASE, "PLANILHA DE MARCAS.xlsx")
        self.ARQUIVO_MEMORIA_PART_NUMBERS = os.path.join(self.PASTA_DATABASE, "memoria_part_numbers.json")
        
        self.NOME_ABA_ALVO = "COTAÇÃO" 
        
        # --- URLs BASE ---
        self.BASE_SUPPLIER_URL = "https://supplier.coupahost.com"
        self.LOGIN_URL = f"{self.BASE_SUPPLIER_URL}/sessions/new"
        self.EVENTS_URL = f"{self.BASE_SUPPLIER_URL}/quotes/private_events/"

        self.TAMANHO_DO_LOTE = 15
        self.db_eventos: Dict[str, Dict] = {} 
        self.driver = None
        self.wait = None
        self.dados_processados_lote = []
        self.memoria_associativa = {}

        self.usar_groq_hoje = False
        self.dia_falha_gemini = None

    # ==========================================

    def iniciar_tarefa(self, modo, dados_extras=None):
        logger.info(f"📥 Ordem recebida da Nuvem! Comando: '{modo}' | Tem dados extras? {'Sim' if dados_extras else 'Não'}")
        
        # 👇 1. A NOVA BLINDAGEM DE PARADA IMEDIATA 👇
        if modo in ["solicitar_parada", "parar_extracao"]:
            self.solicitacao_parada = True
            logger.info("🛑 Comando de Parada Segura ativado! O robô abortará no próximo ciclo.")
            self.sio.emit('relatar_progresso_vale', {'mensagem': "🛑 Parando extração de forma segura..."})
            return # Impede que o código continue
            
        try:
            self.solicitacao_parada = False   # cada tarefa começa sem a parada da anterior
            self.motivo_abandono = None
            if modo == "extrair":
                self.varrer_painel_eventos()
            elif modo == "responder" and dados_extras:
                self.responder_evento(dados_extras)
            elif modo == "verificar":             # <--- ADICIONE ESTA LINHA
                self.verificar_eventos()          # <--- E ESTA LINHA AQUI!
        except Exception as e: 
            logger.error(f" Erro Crítico na Tarefa: {e}")
