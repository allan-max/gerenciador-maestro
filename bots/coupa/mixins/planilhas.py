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


class PlanilhasMixin:
    def _carregar_dataframe_aba_correta(self):
        try:
            with pd.ExcelFile(self.ARQUIVO_PLANILHA_CONTROLE) as xls:
                tgt = next((s for s in xls.sheet_names if "COTACAO" in s.upper() or "COTAÇãO" in s.upper()), None)
                if not tgt:
                    for s in xls.sheet_names:
                        df = pd.read_excel(xls, sheet_name=s, nrows=1)
                        if any("COTAÇãO" in str(c).upper() for c in df.columns): tgt = s; break
                return pd.read_excel(xls, sheet_name=tgt) if tgt else None
        except: return None

    def enviar_relatorio_email(self):
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        if getattr(self, 'relatorio_email_stats', None) is None:
            return

        remetente = self.config.get("SMTP_USER", os.environ.get("SMTP_USER", ""))
        senha = self.config.get("SMTP_PASSWORD", os.environ.get("SMTP_PASSWORD", ""))
        
        emails_destino_raw = self.config.get("emails_relatorio", os.environ.get("EMAILS_RELATORIO", ""))
        destinatarios = [e.strip() for e in emails_destino_raw.split(',') if e.strip()]
        
        if not destinatarios:
            logger.warning("Nenhum email de destino configurado em emails_relatorio.")
            return

        assunto = "Relatório de Cotações - Coupa Bot"
        
        sem_desc_nums = ", ".join(self.relatorio_email_stats.get('sem_descricao', [])) if self.relatorio_email_stats.get('sem_descricao') else "Nenhuma"
        falt_info_nums = ", ".join(self.relatorio_email_stats.get('faltando_info', [])) if self.relatorio_email_stats.get('faltando_info') else "Nenhuma"
        reabertas_nums = ", ".join(self.relatorio_email_stats.get('reabertas_ou_data_mudou', [])) if self.relatorio_email_stats.get('reabertas_ou_data_mudou') else "Nenhuma"
        sem_itens_nums = ", ".join(self.relatorio_email_stats.get('eventos_sem_itens', [])) if self.relatorio_email_stats.get('eventos_sem_itens') else "Nenhuma"
        erros_plan_nums = ", ".join(self.relatorio_email_stats.get('erros_planilha', [])) if self.relatorio_email_stats.get('erros_planilha') else "Nenhuma"
        
        tempo_total = 0
        if 'hora_inicio_extracao' in self.relatorio_email_stats:
            tempo_total = int(time.time() - self.relatorio_email_stats['hora_inicio_extracao'])
            minutos, segundos = divmod(tempo_total, 60)
            tempo_str = f"{minutos}m {segundos}s"
        else:
            tempo_str = "Desconhecido"

        erros_ext_nums = ", ".join(self.relatorio_email_stats.get('erros_extracao', [])) if self.relatorio_email_stats.get('erros_extracao') else "Nenhuma"
        erro_critico = self.relatorio_email_stats.get('erro_critico_bot')

        vendedores_str = "\n".join([f"- {num}: {vend}" for num, vend in self.relatorio_email_stats.get('vendedores_atribuidos', {}).items()])
        if not vendedores_str: vendedores_str = "Nenhum vendedor atribuído nesta rodada."

        corpo = f"""Relatório de Extração de Cotações Finalizado

Tempo total de execução: {tempo_str}
Total de cotações extraídas: {self.relatorio_email_stats.get('total_cotacoes', 0)}

--- VENDEDORES SUGERIDOS PELO ROBÔ ---
{vendedores_str}

--- ERROS CRÍTICOS DO ROBÔ ---
{"Nenhum erro fatal ocorreu." if not erro_critico else f"⚠️ O robô encontrou uma falha grave e precisou ser interrompido:\n{erro_critico}"}

--- PROBLEMAS DE EXTRAÇãO ---
Cotações em que o robô não conseguiu acessar ou extrair ({len(self.relatorio_email_stats.get('erros_extracao', []))}):
{erros_ext_nums}

Cotações sem itens detectados ({len(self.relatorio_email_stats.get('eventos_sem_itens', []))}):
{sem_itens_nums}

Cotações que falharam ao salvar na planilha ({len(self.relatorio_email_stats.get('erros_planilha', []))}):
{erros_plan_nums}

--- ALERTAS DE CONTEÚDO ---
Cotações sem descrição ({len(self.relatorio_email_stats.get('sem_descricao', []))}):
{sem_desc_nums}

Cotações com informações faltantes (ex: Vencimento, Local) ({len(self.relatorio_email_stats.get('faltando_info', []))}):
{falt_info_nums}

Cotações reabertas ou com mudança de data ({len(self.relatorio_email_stats.get('reabertas_ou_data_mudou', []))}):
{reabertas_nums}

Atenciosamente,
Coupa Bot
"""
        try:
            msg = MIMEMultipart()
            msg['From'] = remetente
            msg['To'] = ", ".join(destinatarios)
            msg['Subject'] = assunto
            msg.attach(MIMEText(corpo, 'plain'))

            try:
                server = smtplib.SMTP_SSL('email-ssl.com.br', 465, timeout=30)
                server.login(remetente, senha)
                server.sendmail(remetente, destinatarios, msg.as_string())
                server.quit()
            except Exception as e1:
                logger.warning(f"Erro no SMTP_SSL: {e1}. Tentando STARTTLS na porta 587...")
                server = smtplib.SMTP('email-ssl.com.br', 587, timeout=30)
                server.starttls()
                server.login(remetente, senha)
                server.sendmail(remetente, destinatarios, msg.as_string())
                server.quit()
            logger.info(f"Email de relatrio enviado com sucesso para {', '.join(destinatarios)}")
        except Exception as e:
            try: planilha_manager.lock.release()
            except: pass
            logger.error(f"Erro ao enviar email de relatrio: {e}")

    def buscar_vendedor_por_evento(self, nums_evento: list):
        arq = getattr(self, 'ARQUIVO_PLANILHA_CONTROLE', None)
        if not arq or not os.path.exists(arq): return "N/A"
        vendedor = "N/A"
        try:
            from openpyxl import load_workbook
            wb = load_workbook(arq, read_only=True, data_only=True)
            
            ws = None
            for s in wb.sheetnames:
                if "COTA" in s.upper():
                    ws = wb[s]
                    break
            
            if not ws: ws = wb.active
            
            for row in ws.iter_rows(min_row=2, values_only=True):
                cot = str(row[0] or "").strip()
                vend = str(row[5] or "").strip()
                if vend and vend.lower() not in ["none", "", "nan"]:
                    for n in nums_evento:
                        if n in cot:
                            vendedor = vend
            wb.close()
        except Exception as e:
            try: planilha_manager.lock.release()
            except: pass
            pass
        return vendedor

    def gerar_word(self):
        if not self.dados_processados_lote: return
        logger.info("\n GERANDO DOCUMENTOS WORD (Agrupados por Data e Complexidade)...")
        
        for ev in self.dados_processados_lote:
            vendedores_deste_evento = set()
            for it in ev.get('itens', []):
                descricoes_unicas = []
                for k in ['descricao', 'texto_item', 'texto_compra']:
                    v = str(it.get(k, '')).strip()
                    if v and v.lower() not in [x.lower() for x in descricoes_unicas]:
                        descricoes_unicas.append(v)
                it['descricoes_unicas'] = descricoes_unicas
                txt_completo = f"{it.get('titulo','')} {' '.join(descricoes_unicas)} {it.get('part_number','')}"
                dados_ia = self.extrair_dados_gemini(txt_completo)
                it['dados_ia'] = dados_ia
                match_hist = self.encontrar_vendedor_similar(it, dados_ia)
                if match_hist:
                    it['historico_match'] = match_hist
                    m = re.search(r'Ultimo Vendedor:\s*([^\(]+)', match_hist)
                    if m: vendedores_deste_evento.add(m.group(1).strip())
            
            if not hasattr(self, 'relatorio_email_stats'): self.relatorio_email_stats = {}
            if 'vendedores_atribuidos' not in self.relatorio_email_stats: self.relatorio_email_stats['vendedores_atribuidos'] = {}
            
            num = ev.get('numero_evento', 'Desconhecido')
            if vendedores_deste_evento:
                self.relatorio_email_stats['vendedores_atribuidos'][num] = ", ".join(vendedores_deste_evento)
            else:
                self.relatorio_email_stats['vendedores_atribuidos'][num] = "Nenhum"

        
        marcas_bloq = []
        try: marcas_bloq = [str(x).strip().lower() for x in pd.read_excel(self.ARQUIVO_MARCAS_BLOQUEADAS).iloc[:,0] if pd.notna(x)]
        except: pass
        
        bloqs = []
        complexos = []
        agrupados_por_data = {}
        
        for d in self.dados_processados_lote:
            # 1. Verifica marcas bloqueadas
            todos_itens_bloqueados = True
            marca_ultima = ""
            
            for it in d['itens']:
                txt = f"{it.get('titulo', '')} {it.get('descricao', '')}".lower()
                item_tem_bloqueio = False
                for m in marcas_bloq:
                    if m and len(m) > 1 and re.search(r'\b'+re.escape(m)+r'\b', txt): 
                        item_tem_bloqueio = True
                        marca_ultima = m.upper()
                        break
                
                if not item_tem_bloqueio:
                    todos_itens_bloqueados = False
                    break
            
            if todos_itens_bloqueados and len(d['itens']) > 0 and marca_ultima:
                d['marca_bloqueada'] = marca_ultima
                bloqs.append(d)
                continue
                
            # 2. Verifica complexidade (Mais de 3 itens OU mais de 40 palavras)
            muitos_itens = len(d['itens']) > 4
            desc_longa = False
            for it in d['itens']:
                # Conta as palavras separadas por espaço
                if len(str(it['descricao']).split()) > 250:
                    desc_longa = True
                    break
            
            if muitos_itens or desc_longa:
                complexos.append(d) # Vai ser salvo sozinho
            else:
                # 3. Agrupa por Data de Vencimento
                data_venc = str(d.get('data_vencimento', 'N/A')).split(' - ')[0].strip()
                if data_venc not in agrupados_por_data:
                    agrupados_por_data[data_venc] = []
                agrupados_por_data[data_venc].append(d)
        
        validos_para_excel = []
        
        # Complexos: Salva 1 por 1 em arquivos isolados
        if complexos:
            for c in complexos: 
                self.salvar_doc([c], self.PASTA_RELATORIOS, "COMPLEXO")
                
        # Bloqueados: Agrupa e salva na pasta de bloqueio
        if bloqs: 
            self.salvar_doc(bloqs, self.PASTA_BLOQUEADOS, "BLOQUEADO")

        # Padrões: Salva agrupado por data, no máximo 5 por arquivo
        for data, eventos in agrupados_por_data.items():
            data_limpa = data.replace('/', '-') # Troca a / por - para o Windows não dar erro no nome do arquivo
            
            # Organizar também por descrição, para que eventos com a mesma descrição fiquem juntos
            eventos.sort(key=lambda ev: ev.get('itens', [{}])[0].get('descricao', '').strip().lower() if ev.get('itens') else '')
            
            # Fatiar a lista de eventos de 5 em 5
            for i in range(0, len(eventos), 5):
                lote_5 = eventos[i:i+5]
                self.salvar_doc(lote_5, self.PASTA_RELATORIOS, f"Vencimento_{data_limpa}_Eventos")
                validos_para_excel.extend(lote_5)

        todos_para_excel = validos_para_excel + complexos
        sucesso_excel = True
        if todos_para_excel: 
            sucesso_excel = self.atualizar_excel(todos_para_excel)
        
        if sucesso_excel:
            for d in self.dados_processados_lote:
                try:
                    venc_tab = d.get('vencimento_tabela', "")
                    novo = {
                        "titulo": d['numero_evento'], 
                        "data": str(datetime.now().date()), 
                        "respondido": True, 
                        "vencimento_tabela": venc_tab
                    }
                    with open(self.ARQUIVO_JSONL, 'a', encoding='utf-8') as f: 
                        f.write(json.dumps(novo) + '\n')
                    self.db_eventos[d['numero_evento']] = novo
                except: pass
            
        self.dados_processados_lote.clear()
        logger.info("✅ Lote de Word finalizado.")

    def salvar_doc(self, lista, pasta, prefixo):
        if not lista: return
        try:
            nome = f"{prefixo}_{'_'.join([x['numero_evento'] for x in lista])}.docx"
            doc = Document()

            for section in doc.sections:
                section.top_margin = Cm(1.0)
                section.bottom_margin = Cm(1.0)
                section.left_margin = Cm(1.0)
                section.right_margin = Cm(1.0)

            style = doc.styles['Normal']
            style.font.name = 'Arial'
            style.font.size = Pt(9)
            style.paragraph_format.space_after = Pt(0) 
            style.paragraph_format.line_spacing = 1.0  

            for i, ev in enumerate(lista):
                if i > 0: 
                    p_linha = doc.add_paragraph("_" * 100)
                    p_linha.paragraph_format.space_before = Pt(10)
                
                if ev.get('marca_bloqueada'): 
                    r = doc.add_paragraph().add_run(f"âš ï¸ BLOQUEADO: {ev['marca_bloqueada']}")
                    r.bold = True; r.font.color.rgb = RGBColor(255,0,0)
                
                p_cabecalho = doc.add_paragraph()
                texto_evento = f"REQ: {ev['req_num']} | EVENTO: {ev['numero_evento']}"
                if ev.get('tem_anexo'):
                    texto_evento += " *contem anexo*"
                texto_evento += "\t"
                p_cabecalho.add_run(texto_evento).bold = True
                p_cabecalho.add_run("VENCIMENTO: ").bold = True
                p_cabecalho.add_run(f"{ev['data_vencimento']}").bold = True
                
                doc.add_paragraph("RESPONDIDO POR: ______________ CONFERIDO POR: ______________ PRECIFICADO POR:_____________")
                
                p_frete = doc.add_paragraph()
                p_frete.add_run("FRETE: ").bold = True
                p_frete.add_run(f" {ev['tipo_frete']}")

                for item in ev['itens']:
                    p_titulo = doc.add_paragraph()
                    if item['titulo'] != 'Não':
                        p_titulo.add_run(f"ITEM {item['item_num']} (Título): ").bold = True
                        p_titulo.add_run(f" {item['titulo']}")
                        
                        p_desc = doc.add_paragraph()
                        p_desc.add_run("DESCRIÇãO:\n").bold = True
                        p_desc.add_run(f"{item['descricao']}")
                    else:
                        p_titulo.add_run(f"ITEM {item['item_num']}: ").bold = True
                        p_titulo.add_run(f" {item['descricao']}")
                    
                    import re
                    def _normalize(t):
                        return str(t).strip().lower() if t else ""

                    desc_norm = _normalize(item.get('descricao', ''))
                    ti_norm = _normalize(item.get('texto_item', ''))
                    tc_norm = _normalize(item.get('texto_compra', ''))

                    if item.get('texto_item') and ti_norm != desc_norm:
                        p_texto_item = doc.add_paragraph()
                        p_texto_item.add_run("TEXTO DO ITEM:\n").bold = True
                        p_texto_item.add_run(f"{item['texto_item']}")
                        
                    if item.get('texto_compra') and tc_norm != desc_norm and tc_norm != ti_norm:
                        p_texto_compra = doc.add_paragraph()
                        p_texto_compra.add_run("TEXTO DE COMPRA DO MATERIAL:\n").bold = True
                        p_texto_compra.add_run(f"{item['texto_compra']}")
                    p_qtd = doc.add_paragraph()
                    p_qtd.add_run("QTD: ").bold = True
                    p_qtd.add_run(f" {item['quantidade']} - LOCAL: {item['local_entrega']}")
                    
                    doc.add_paragraph("VALOR DE VENDA: R$ __________________ CUSTO UNIT: R$__________________")
                    
                    if item.get('historico_match'):
                        p_hist = doc.add_paragraph()
                        p_hist.add_run(item['historico_match']).bold = True
            
            doc.save(os.path.join(pasta, nome))
            logger.info(f"    Doc Salvo Compactado: {nome}")
        except Exception as e: 
            try: planilha_manager.lock.release()
            except: pass
            logger.error(f"Erro no Word: {e}")


    def atualizar_excel(self, dados):
        if not dados: return
        
        try:
            logger.info("Gravando dados JSON da Coupa...")
            
            from planilha_manager import planilha_manager
            
            linhas_inserir = []
            cnt = 0
            for ev in dados:
                d_agg, q_agg, mo_agg, ma_agg = [], [], [], []
                pref = len(ev['itens']) > 1
                for it in ev['itens']:
                    p = f"(Item {it['item_num']}) " if pref else ""
                    q_agg.append(f"{p}{it['quantidade']}")
                
                    dados_ia = it.get('dados_ia', {})
                    ma = str(dados_ia.get("marca", "")).strip()
                    mo_extraido = str(dados_ia.get("modelo", "")).strip()
                    pn_extraido = str(dados_ia.get("part_number", "")).strip()
                    mo = pn_extraido if pn_extraido else mo_extraido
                
                    if mo: mo_agg.append(f"{p}{mo}")
                    if ma: ma_agg.append(f"{p}{ma}")
                
                    tit = str(it.get('titulo', '')).strip()
                    desc = str(it.get('descricao', '')).strip()
                    melhor_desc = f"{tit} - {desc}" if desc else tit
                    d_agg.append(f"{p}{melhor_desc[:150000]}")
                
                vendedor_str = ""
                st = ""
                
                loc_str = str(ev['itens'][0].get('local_entrega', '')).strip()

                vals = [ev['numero_evento'], ev['data_vencimento'].split(' - ')[0], "\n".join(d_agg), "\n".join(q_agg), loc_str, vendedor_str, "\n".join(mo_agg), "\n".join(ma_agg), st]
                linhas_inserir.append(vals)
            
                cnt += 1
        
            planilha_manager.adicionar_linhas(self.NOME_ABA_ALVO, linhas_inserir)
        
            logger.info(f"JSON salvo com sucesso (+{cnt} linhas).")
            return True
            
        except Exception as e:
            logger.error(f"ERRO AO SALVAR JSON: {e}")
            return False

    def atualizar_status(self, num, resp):
        try:
            from planilha_manager import planilha_manager
            
            # Formata a string corretamente para o JSON
            texto_resp = "RESPONDIDO" if resp else "NÃO RESPONDIDO"
            
            # Atualiza direto no JSON!
            # 1 = COTAÇÃO (chave), 9 = RESPOSTA (status)
            salvou = planilha_manager.atualizar_status_respostas(self.NOME_ABA_ALVO, {str(num): texto_resp}, 1, 9)
            if salvou:
                logger.info(f"   ✅ Status atualizado no JSON: {num} -> {texto_resp}")
        except Exception as e:
            logger.error(f"Erro ao atualizar_status: {e}")

    def atualizar_pedidos_excel(self, pedidos: list):
        logger.info(f" --- INICIANDO GRAVACAO DE PEDIDOS NO EXCEL --- ")
        if not pedidos:
            logger.info(" Lista de pedidos vazia. Nao ha o que gravar no Excel.")
            return
            
        arq = getattr(self, 'ARQUIVO_PLANILHA_CONTROLE', None)
        if not arq or not __import__('os').path.exists(arq): return
        
        try:
            from planilha_manager import planilha_manager
            planilha_manager.iniciar(arq)
            cnt = planilha_manager.adicionar_linhas_pedidos("PEDIDO", pedidos)
            if cnt:
                logger.info(f" Excel de Pedidos salvo (+{cnt} linhas).")
        except Exception as e:
            logger.error(f" Erro ao salvar pedidos no Excel: {e}")
