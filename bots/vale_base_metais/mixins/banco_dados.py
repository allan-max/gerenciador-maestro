# -*- coding: utf-8 -*-
"""Pastas e banco local de eventos (eventos_nimbi.jsonl) do robô da Vale."""
import logging
import os
import json
import pandas as pd
from datetime import datetime
from openpyxl import Workbook
from nucleo import ambiente

logger = logging.getLogger("ValeApp")


class Banco_dadosMixin:
    def verificar_acesso_pastas(self):
        pastas = [self.PASTA_RELATORIOS, self.PASTA_BLOQUEADOS, self.PASTA_DATABASE, self.PASTA_BACKUPS, self.PASTA_LOGS]
        for p in pastas: os.makedirs(p, exist_ok=True)
        if not os.path.exists(self.ARQUIVO_MARCAS_BLOQUEADAS):
            pd.DataFrame({"Marcas Bloqueadas": ["SCHNEIDER"]}).to_excel(self.ARQUIVO_MARCAS_BLOQUEADAS, index=False)
        if not os.path.exists(self.ARQUIVO_BASE_MARCAS):
            pd.DataFrame({"Marcas Conhecidas": ["WEG"]}).to_excel(self.ARQUIVO_BASE_MARCAS, index=False)
        if not os.path.exists(self.ARQUIVO_PLANILHA_CONTROLE):
            wb = Workbook(); ws = wb.active; ws.title = self.NOME_ABA_ALVO
            ws.append(["COTAÇÃO", "VENCIMENTO", "ITEM", "QUANTIDADE", "LOCALIDADE", "VENDEDOR", "MODELOS", "MARCAS", "RESPOSTA"])
            wb.save(self.ARQUIVO_PLANILHA_CONTROLE)
        if not os.path.exists(self.ARQUIVO_JSONL):
            with open(self.ARQUIVO_JSONL, 'w', encoding='utf-8') as f: f.write("")

    def carregar_banco_dados(self):
        self.db_eventos = {}
        if os.path.exists(self.ARQUIVO_JSONL):
            with open(self.ARQUIVO_JSONL, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        if d.get('titulo'): self.db_eventos[d['titulo']] = d
                    except: pass
        logger.info(f" Banco interno carregado: {len(self.db_eventos)} registros.")

    def salvar_evento_jsonl(self, numero: str, respondido: bool = False, vencimento_tabela: str = ""):
        if numero:
            try:
                ambiente.exigir_trava(self.ARQUIVO_JSONL)
                # Guarda o vencimento_tabela no JSONL
                novo = {
                    "titulo": numero, 
                    "data": str(datetime.now().date()), 
                    "respondido": respondido,
                    "vencimento_tabela": vencimento_tabela
                }
                ambiente.acrescentar_jsonl(self.ARQUIVO_JSONL, novo)
                self.db_eventos[numero] = novo
            except Exception: logger.exception(f'Falha ao gravar o evento {numero} no JSONL')

    def evento_ja_processado(self, num: str) -> bool: 
        return num in self.db_eventos
