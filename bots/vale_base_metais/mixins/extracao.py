# -*- coding: utf-8 -*-
"""Varredura do painel e leitura de cada evento e dos itens (Vale Base Metals)."""
import logging
import re
import time
from datetime import datetime
from typing import Optional
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoAlertPresentException
from selenium.webdriver.common.action_chains import ActionChains
from bots.portal.termos_participacao import aceitar_termos, termos_pendentes, ler_termos
from bots.avisos import aviso_termos, vencimento_alterado

try:
    from bots.itens.validacao_cotacao import validar_itens_evento, contem_expected_quantity
except ImportError:
    from validacao_cotacao import validar_itens_evento, contem_expected_quantity

logger = logging.getLogger("ValeApp")


# Portal instável (docs/melhorias-bots, fase 4): 3 'Oops' seguidos = pausa; depois de 3 pausas, abandona o ciclo
OOPS_POR_PAUSA = 3
PAUSA_OOPS_S = 600
MAX_PAUSAS_OOPS = 3


class ExtracaoMixin:
    # ============================ 3. VARREDURA (LISTA E PAGINAÇÃO) ============================
    def _pausa_por_oops(self):
        """Conta telas 'Oops' seguidas. A cada OOPS_POR_PAUSA pausa PAUSA_OOPS_S; passou de MAX_PAUSAS_OOPS,
        abandona o ciclo (liga a parada e guarda o motivo). Devolve True quando é para parar de tentar."""
        self.oops_seguidos = getattr(self, 'oops_seguidos', 0) + 1
        if self.oops_seguidos % OOPS_POR_PAUSA:
            return False
        pausa = self.oops_seguidos // OOPS_POR_PAUSA
        if pausa > MAX_PAUSAS_OOPS:
            self.motivo_abandono = f"Portal instável: {self.oops_seguidos} telas 'Oops' seguidas; ciclo abandonado."
            logger.error(self.motivo_abandono)
            self.solicitacao_parada = True
            return True
        logger.warning(f"{self.oops_seguidos} telas 'Oops' seguidas: pausa de {PAUSA_OOPS_S // 60} min ({pausa}/{MAX_PAUSAS_OOPS}).")
        fim = time.time() + PAUSA_OOPS_S
        while time.time() < fim and not self.solicitacao_parada:
            time.sleep(5)
        return self.solicitacao_parada

    def varrer_painel_eventos(self):
        logger.info("\n--- INICIANDO VARREDURA DE EVENTOS (VALE BASE METALS) ---")
        self.oops_seguidos = 0   # a parada é zerada ao iniciar a tarefa; aqui ela se perdia no aquecimento
        
        self.relatorio_email_stats = {
            'total_cotacoes': 0,
            'sem_descricao': [],
            'faltando_info': [],
            'reabertas_ou_data_mudou': [],
            'eventos_sem_itens': [],
            'erros_planilha': [],
            'erros_extracao': [],
            'erro_critico_bot': None,
            'hora_inicio_extracao': time.time()
        }

        try:
            self.garantir_logado()
            
            # 1. Vai para Sourcing Privado
            self.driver.get(self.EVENTS_URL)
            time.sleep(4)
            
            # 2. Selecionar o Cliente Específico
            self.selecionar_cliente_vale_base_metals()
            
            # 3. Mudar para 90 itens por página
            logger.info("🔎 Mudando para 90 resultados por página...")
            try:
                btn_90 = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[.//span[text()='90']]")))
                self.driver.execute_script("arguments[0].click();", btn_90)
                time.sleep(4)
            except:
                logger.warning(" Botão de 90 itens não encontrado. Prosseguindo com paginação padrão.")

            # 4. Iniciar Paginação
            pag = 1
            paginas_vazias_seguidas = 0
            while True:
                if self.solicitacao_parada: break
                logger.info(f"📄 Analisando Página {pag}...")
                
                try: self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "tr.coupaTable__row")))
                except: logger.info("Nenhuma linha encontrada."); break
                    
                rows = self.driver.find_elements(By.CSS_SELECTOR, "tr.coupaTable__row")
                tarefas = []
                
                for r in rows:
                    try:
                        # Pega todas as colunas daquela linha
                        celulas = r.find_elements(By.CSS_SELECTOR, "td.coupaTable__cell")
                        if not celulas: continue
                        
                        # A primeira coluna tem o link e o número
                        a_tag = celulas[0].find_element(By.TAG_NAME, "a")
                        num = a_tag.text.strip()
                        url = a_tag.get_attribute('href')
                        
                        # A 6ª coluna (índice 5) tem a Data de Vencimento
                        data_venc_tabela = ""
                        try:
                            if len(celulas) >= 6:
                                data_venc_tabela = celulas[5].text.strip()
                        except: pass

                        re_extrair = False
                        data_salva = ""
                        if self.evento_ja_processado(num):
                            evento_salvo = self.db_eventos.get(num, {})
                            data_salva = evento_salvo.get("vencimento_tabela", "")
                            
                            if data_venc_tabela and data_salva and data_venc_tabela != data_salva:
                                logger.info(f"🔄 Evento {num} REABERTO! Data mudou de {data_salva} para {data_venc_tabela}.")
                                re_extrair = True
                            else:
                                continue 
                        
                        tarefas.append({
                            'num': num, 
                            'url': url, 
                            'venc_tabela': data_venc_tabela,
                            're_extrair': re_extrair,
                            'venc_anterior': data_salva if re_extrair else ""
                        })
                    except: continue
                
                logger.info(f"   📊 Encontrados {len(tarefas)} eventos a processar nesta página.")
                
                if not tarefas:
                    paginas_vazias_seguidas += 1
                else:
                    paginas_vazias_seguidas = 0
                    
                if paginas_vazias_seguidas >= 3:
                    logger.info("3 páginas seguidas sem novos eventos encontradas. Parando extração de forma segura...")
                    self.sio.emit('relatar_progresso_vale', {'mensagem': '🛑 3 páginas seguidas sem novos eventos. Encerrando paginação...'})
                    break

                self.sio.emit('relatar_progresso', {'mensagem': f"🔍 Página {pag}: {len(tarefas)} eventos a processar encontrados.", 'total': len(tarefas)})

                # Processar eventos da página atual
                for idx, t in enumerate(tarefas):
                    if self.solicitacao_parada: break
                    
                    self.sio.emit('relatar_progresso', {'mensagem': f"🎯 Iniciando extração do evento {t['num']}...", 'atual': idx + 1})

                    self.driver.execute_script(f"window.open('{t['url']}', '_blank');")
                    self.driver.switch_to.window(self.driver.window_handles[-1])
                    
                    res = self.extrair_dados_do_evento(t['url'], t['num'], re_extrair=t.get('re_extrair', False), venc_tabela=t.get('venc_tabela', ''))
                    
                    if res == "ENCERRADO":
                        # "Já terminou" é definitivo: grava (novo ou reaberto) com a data da tabela, senão volta em todo
                        # ciclo. Se reabrir de novo, a data muda e ele volta como REABERTO. Oops/0 itens tentam de novo.
                        anterior = self.db_eventos.get(t['num'], {})
                        self.salvar_evento_jsonl(t['num'], anterior.get('respondido', bool(t.get('re_extrair'))), t.get('venc_tabela', ''))

                    if res is None:
                        self.relatorio_email_stats['erros_extracao'].append(t['num'])
                    
                    if res and res not in ("SKIPPED", "ENCERRADO"):
                        res['vencimento_tabela'] = t.get('venc_tabela', "")
                        vencimento_alterado.anotar(res, t)   # aviso "Vencimento alterado..." no Word e no JSON
                        self.dados_processados_lote.append(res)
                        
                        self.relatorio_email_stats['total_cotacoes'] += 1
                        if t.get('re_extrair'):
                            self.relatorio_email_stats['reabertas_ou_data_mudou'].append(t['num'])
                            
                        sem_desc = False
                        falt_info = False
                        for item in res.get('itens', []):
                            desc = str(item.get('descricao', '')).strip().lower()
                            if not desc or desc in ['nenhum', 'n/a']: sem_desc = True
                            loc = str(item.get('local_entrega', '')).strip()
                            if not loc or loc in ['n/a', 'N/A']: falt_info = True
                            
                        if res.get('data_vencimento', 'N/A') in ['N/A', '']: falt_info = True
                        if res.get('tipo_frete', 'N/A') in ['N/A', '']: falt_info = True
                        
                        if sem_desc: self.relatorio_email_stats['sem_descricao'].append(t['num'])
                        if falt_info: self.relatorio_email_stats['faltando_info'].append(t['num'])
                        if not res.get('itens'): self.relatorio_email_stats['eventos_sem_itens'].append(t['num'])
                    aviso_termos.concluir(self, t['num'], res)   # situação do evento no e-mail de termos

                    self.driver.close()
                    self.driver.switch_to.window(self.driver.window_handles[0])

                # Gera os ficheiros Word para a página que acabou de ser lida
                if self.dados_processados_lote:
                    self.gerar_word()

                if self.solicitacao_parada: break
                
                # 5. Ir para a próxima página
                pag += 1
                try:
                    # Busca o botão com o número da PRÓXIMA página
                    btn_next = self.driver.find_element(By.XPATH, f"//button[.//span[text()='{pag}']]")
                    
                    # Se ele estiver desativado ou for a página atual, terminamos.
                    if "disabled" in btn_next.get_attribute("class") or btn_next.get_attribute("currentitem") == "true":
                        logger.info("Fim das páginas alcançado.")
                        break
                        
                    logger.info(f" Avançando para a página {pag}...")
                    self.driver.execute_script("arguments[0].click();", btn_next)
                    time.sleep(4)
                except Exception as e:
                    logger.info("Botão da próxima página não encontrado. Fim da extração.")
                    break
                
            if self.dados_processados_lote: 
                self.gerar_word()
            
            logger.info("✅ Extração concluída!")
            self.enviar_relatorio_email()
            motivo = getattr(self, 'motivo_abandono', None)   # abandonado (Oops/login) não é sucesso
            aviso_termos.enviar(self, "Vale Base Metals")   # eventos com "Aceita os Termos e condições?"
            self.sio.emit('tarefa_concluida', {'evento': 'Lote de Extração', 'sucesso': not motivo, **({'erro': motivo} if motivo else {})})

        except Exception as e:
            logger.error(f"❌ Erro Crítico na varredura: {e}")
            if hasattr(self, 'relatorio_email_stats'):
                self.relatorio_email_stats['erro_critico_bot'] = str(e)
                self.enviar_relatorio_email()
            aviso_termos.enviar(self, "Vale Base Metals")   # eventos com "Aceita os Termos e condições?"
            self.sio.emit('tarefa_concluida', {'evento': 'Lote de Extração Vale', 'sucesso': False, 'erro': str(e)})

    # ============================ 4. EXTRAÇÃO DO EVENTO (Interno) ============================
    def _filter_desc(self, t):
        if "PT ||" in t:
            try:
                s = t.index("PT ||")
                ends = [x for x in [t.find("***", s), t.find("ES ||", s), t.find("EN ||", s)] if x != -1]
                return t[s:min(ends) if ends else len(t)].strip()
            except: pass
        return t

    def parse_local(self, t):
        """Filtra a string gigante da Vale e retorna apenas 'Cidade - UF'"""
        t = t.replace("Local de entrega:", "").strip()
        p = [x.strip() for x in t.split('-')]
        ufs = {"AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA","PB","PR","PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO"}
        
        # Lê de trás para a frente procurando o Estado (UF)
        for i in range(len(p)-1, 0, -1):
            if p[i].upper().replace(".", "") in ufs:
                c = p[i-1]
                if c.upper() not in ["S/N", "S/N."]: 
                    return f"{c} - {p[i].upper().replace('.', '')}"
        return t

    def _verificar_tela_erro(self):
        """Verifica se o portal crashou na tela 'Oops! Algo inesperado aconteceu.'"""
        try:
            erros = self.driver.find_elements(By.XPATH, "//h1[@id='error-message' or contains(text(), 'Oops!')]")
            return len(erros) > 0
        except:
            return False

    def _obter_novo_link_evento(self, target_num):
        logger.info(f"🔍 Buscando novo link para o evento {target_num} no painel...")
        try:
            self.garantir_logado(self.EVENTS_URL)
            self.driver.get(self.EVENTS_URL)
            curr = 1
            while True:
                if curr > 1: self.driver.get(f"{self.EVENTS_URL}?page={curr}")
                
                try: WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "table")))
                except: return None
                
                rows = self.driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
                if not rows: return None
                
                for r in rows:
                    try:
                        num = r.find_element(By.CSS_SELECTOR, "a span.dt_open_link").text.strip()
                        if num == target_num:
                            return r.find_element(By.XPATH, ".//a").get_attribute('href')
                    except: continue
                    
                has_next = False
                try: 
                    if self.driver.find_element(By.CSS_SELECTOR, "a.next_page:not(.disabled)"): has_next = True
                except: pass
                
                if not has_next: return None
                curr += 1
        except Exception as e:
            logger.error(f"Erro ao buscar novo link: {e}")
            return None

    @staticmethod
    def parsear_data_vencimento(data_str: str) -> Optional[str]:
        return vencimento_alterado.parsear_data_vencimento(data_str)

    def capturar_end_time_elemento(self) -> Optional[str]:
        """
        Captura a data de vencimento real do elemento HTML do Coupa:
        <div class="img" id="end_time">
          <div class="month">Out</div>
          <div class="date">23</div>
        </div>
        """
        try:
            elementos = self.driver.find_elements(By.CSS_SELECTOR, "#end_time, div.end_time, .end_time")
            for el in elementos:
                try:
                    data_attr = el.get_attribute("data-end-date")
                    parsed_attr = self.parsear_data_vencimento(data_attr)
                    if parsed_attr:
                        return parsed_attr
                except: pass

                try:
                    mes_el = el.find_elements(By.CLASS_NAME, "month")
                    dia_el = el.find_elements(By.CLASS_NAME, "date")
                    if mes_el and dia_el:
                        mes_raw = mes_el[0].text.strip().lower()
                        dia_raw = dia_el[0].text.strip()
                        mapa_meses = {
                            'jan': 1, 'fev': 2, 'feb': 2, 'mar': 3, 'abr': 4, 'apr': 4,
                            'mai': 5, 'may': 5, 'jun': 6, 'jul': 7, 'ago': 8, 'aug': 8,
                            'set': 9, 'sep': 9, 'out': 10, 'oct': 10, 'nov': 11, 'dez': 12, 'dec': 12
                        }
                        mes_prefix = mes_raw[:3]
                        if mes_prefix in mapa_meses and dia_raw.isdigit():
                            m = mapa_meses[mes_prefix]
                            y = datetime.now().year
                            hora_str = ""
                            try:
                                h_els = el.find_elements(By.CSS_SELECTOR, "span.quotes_h1, .time, .hour")
                                for h_el in h_els:
                                    t_h = h_el.text.strip()
                                    m_h = re.search(r'(\d{1,2}:\d{2})', t_h)
                                    if m_h:
                                        hora_str = f" - {m_h.group(1)}"
                                        break
                            except: pass
                            return f"{int(dia_raw):02d}/{m:02d}/{y}{hora_str}"
                except: pass
        except: pass
        return None

    def _texto_valido(self, t):
        return bool(t) and t.strip().lower() not in ('', 'nenhum', 'none', 'n/a')

    def _ler_frete_linha(self, l):
        """Incoterms (EXW, CIF, FOB...) da linha do item; só existe com o item expandido. '' se não achar."""
        try: frete = l.find_element(By.CSS_SELECTOR, "div.s-shipping_term p.s-selectField").text
        except:
            try: frete = l.find_element(By.XPATH, ".//div[contains(@class, 's-fieldLabel') and contains(text(), 'Incoterms')]/following-sibling::p[contains(@class, 's-selectField')]").text
            except: return ""
        frete = (frete or "").strip().upper()
        return frete if frete.lower() not in ('nenhum', 'none', 'n/a') else ""

    def _frete_do_evento(self, itens, frete_pagina, padrao="EXW"):
        """Frete do evento: o primeiro Incoterms informado pelos itens; sem nenhum, o da página; por último, EXW."""
        for it in itens or []:
            frete = str(it.get("frete") or "").strip().upper()
            if frete and frete.lower() not in ('nenhum', 'none', 'n/a'):
                return frete
        frete = str(frete_pagina or "").strip().upper()
        return frete if frete and frete.lower() not in ('nenhum', 'none', 'n/a') else padrao

    def _texto_por_rotulo(self, l, *rotulos):
        """Texto de um campo da linha pelo rótulo; '' se não houver ou for 'Nenhum'.
        Layout de nov/2026: <div class="s-fieldLabel">Item Text</div><p class="s-textField">...</p>.
        Layout antigo: <dt>Item Text</dt><dd>...</dd>."""
        for rotulo in rotulos:
            try:
                p = l.find_element(By.XPATH, f".//div[contains(@class, 's-fieldLabel') and normalize-space()='{rotulo}']/following-sibling::p")
                txt = (p.text or "").strip()
                if self._texto_valido(txt): return txt
            except: pass
            try:
                dt = l.find_element(By.XPATH, f".//dt[contains(text(), '{rotulo}')]")
                txt = (dt.find_element(By.XPATH, "following-sibling::dd").text or "").strip()
                if self._texto_valido(txt): return txt
            except: pass
        return ""

    def _rotulo_nao_descritivo(self, p):
        """True se o p.s-textField é de um campo rotulado que não descreve o item (fabricante, NCM, data...)."""
        try:
            rotulos = p.find_elements(By.XPATH, "preceding-sibling::div[contains(@class, 's-fieldLabel')]")
            nome = (rotulos[-1].text or "").strip().lower() if rotulos else ""
        except Exception:
            return False
        return nome in ("manufacturer name", "fabricante", "ncm", "necessário até", "short text service", "descrição do item")

    def _ler_campos_linha(self, l):
        """Uma leitura (sem cliques) de título, descrição, quantidade, local de entrega e textos extras da linha."""
        tit = "Item"; desc = ""; qtd = "0"; loc = "N/A"
            
        # 1. Título e Descrição
        try: tit = l.find_element(By.CSS_SELECTOR, "div.s-description p.s-textField").text.strip()
        except: pass
        try:
            d = l.find_elements(By.CSS_SELECTOR, "p.s-textField")
            # Layout de nov/2026: o 2º p.s-textField passou a ser "Manufacturer Name" (quase sempre vazio);
            # p com rótulo que não é descrição não serve
            if len(d) > 1 and not self._rotulo_nao_descritivo(d[1]): desc = self._filter_desc(d[1].text)
        except: pass

        # Textos da Vale pelo rótulo: Texto de compra (PT), depois Item Text, depois Material Purchase Text
        for rotulos in (("Texto de compra",), ("Item Text", "Texto do item"), ("Material Purchase Text",)):
            if self._texto_valido(desc): break
            achado = self._texto_por_rotulo(l, *rotulos)
            if achado: desc = self._filter_desc(achado)
            
        # 2. Quantidade (BLINDADO CONTRA PREÇOS "BRL")
        try: 
            elementos_unidade = l.find_elements(By.CSS_SELECTOR, "p.valueWithUnit")
            for q in elementos_unidade:
                texto_q = q.text.strip()
                if "BRL" not in texto_q and "R$" not in texto_q and "USD" not in texto_q:
                    qtd = texto_q
                    break
        except: pass
            
        # 3. Local de Entrega (BUSCA ROBUSTA POR PALAVRA-CHAVE)
        try:
            elemento_local = l.find_element(By.XPATH, ".//*[contains(text(), 'Local de entrega:')]")
            loc = self.parse_local(elemento_local.text)
        except: 
            try: loc = self.parse_local(l.find_element(By.CSS_SELECTOR, "li.attachmentText").text)
            except: pass
            
        # Nova verificação: Endereço de entrega detalhado
        if loc == "N/A" or not loc.strip():
            try:
                address_label = l.find_element(By.XPATH, ".//div[contains(@class, 's-fieldLabel') and contains(text(), 'Endereço de entrega')]")
                address_box = address_label.find_element(By.XPATH, "following-sibling::div[contains(@class, 'addressLines')]")
                    
                linhas_end = address_box.text.split('\n')
                for linha in linhas_end:
                    if re.search(r'\d{5}-\d{3}', linha):
                        cidade_estado = re.sub(r'.*?\d{5}-\d{3}\s*', '', linha).strip()
                        if cidade_estado:
                            loc = re.sub(r'\s+', ' ', cidade_estado)
                            break
                    
                if loc == "N/A" or not loc.strip():
                    loc = re.sub(r'\s+', ' ', address_box.text)
            except: pass
            
        # Novas extrações solicitadas
        texto_item_extra = self._texto_por_rotulo(l, "Texto do item", "Item Text")

        texto_compra_extra = ""
        txt_tc = self._filter_desc(self._texto_por_rotulo(l, "Texto de compra", "Material Purchase Text"))
        if self._texto_valido(txt_tc):
            texto_compra_extra = txt_tc
        return {"tit": tit, "desc": desc, "qtd": qtd, "loc": loc, "texto_item": texto_item_extra, "texto_compra": texto_compra_extra, "frete": self._ler_frete_linha(l)}

    def _extrair_itens_linhas(self, linhas):
        itens = []
        tem_anexo_geral = False
        MAX_TENTATIVAS = 3
        for i, l in enumerate(linhas):
            # Insistência inteligente: o Local de Entrega só aparece com o item expandido (a quantidade aparece
            # mesmo fechado). Sem local = não expandiu, clica de novo; com local e sem textos = já está aberto,
            # só relê (um novo clique fecharia o item).
            campos = {"tit": "Item", "desc": "", "qtd": "0", "loc": "N/A", "texto_item": "", "texto_compra": "", "frete": ""}
            item_expandido = False
            tem_texto_descritivo = False
            for tentativa in range(MAX_TENTATIVAS):
                if tentativa == 0:
                    try:
                        btn_expandir = l.find_element(By.CSS_SELECTOR, "img.s-expandLines, div.s-expandSidebar")
                        ActionChains(self.driver).click(btn_expandir).perform()
                    except: pass
                    time.sleep(0.3)
                elif not item_expandido:
                    logger.warning(f"       ⚠️ Item {i+1} não expandiu (sem Local de Entrega). Re-expandindo (tentativa {tentativa+1}/{MAX_TENTATIVAS})...")
                    try: self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", l)
                    except: pass
                    try:
                        btn_expandir = l.find_element(By.CSS_SELECTOR, "img.s-expandLines, div.s-expandSidebar")
                        ActionChains(self.driver).click(btn_expandir).perform()
                    except: pass
                    time.sleep(0.6 + 0.2 * (tentativa - 1))
                else:
                    logger.info(f"       🔁 Item {i+1} já aberto, mas sem textos descritivos. Relendo sem clicar (tentativa {tentativa+1}/{MAX_TENTATIVAS})...")
                    time.sleep(0.6)

                try:
                    l = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")[i]
                except: pass

                try:
                    if l.find_elements(By.CSS_SELECTOR, "li.attachment.attachmentFile.s-attachmentFile"):
                        tem_anexo_geral = True
                except: pass

                try: leitura = self._ler_campos_linha(l)
                except Exception: leitura = {}

                # Uma releitura nunca apaga o que uma tentativa anterior já tinha achado
                if leitura.get("tit", "Item") != "Item": campos["tit"] = leitura["tit"]
                if leitura.get("qtd", "0") != "0": campos["qtd"] = leitura["qtd"]
                for chave in ("desc", "texto_item", "texto_compra"):
                    if self._texto_valido(leitura.get(chave, "")) or not campos[chave].strip():
                        campos[chave] = leitura.get(chave, "") or campos[chave]

                loc_lido = leitura.get("loc", "N/A") or ""
                item_expandido = bool(loc_lido.strip()) and loc_lido != "N/A"
                if item_expandido: campos["loc"] = loc_lido
                if leitura.get("frete"): campos["frete"] = leitura["frete"]

                tem_texto_descritivo = any(self._texto_valido(campos[c]) for c in ("desc", "texto_item", "texto_compra"))
                if item_expandido and tem_texto_descritivo:
                    break
            else:
                logger.warning(f"       ⚠️ Item {i+1}: extração incompleta após {MAX_TENTATIVAS} tentativas (expandido: {item_expandido} | textos: {tem_texto_descritivo}).")

            tit = campos["tit"]; desc = campos["desc"]; qtd = campos["qtd"]; loc = campos["loc"]
            texto_item_extra = campos["texto_item"]; texto_compra_extra = campos["texto_compra"]

            # DESCRIÇÃO nunca fica em branco quando o item tem algum texto
            if not self._texto_valido(desc) and (texto_compra_extra or texto_item_extra):
                desc = texto_compra_extra or texto_item_extra

            logger.info(f"       Item {i+1}: {tit[:20]}... | Qtd: {qtd} | Local: {loc[:30]}...")
            itens.append({"item_num": i+1, "titulo": tit, "descricao": desc, "quantidade": qtd, "local_entrega": loc, "texto_item": texto_item_extra, "texto_compra": texto_compra_extra, "frete": campos["frete"]})
        return itens, tem_anexo_geral

    def extrair_dados_do_evento(self, url: str, num: str, ja_respondido: bool = False, is_negotiation: bool = False, re_extrair: bool = False, venc_tabela: str = ""):
        if self.evento_ja_processado(num) and not re_extrair: 
            logger.info(f"⏭️ Evento {num} já existe e a data não mudou. Pulando.")
            return "SKIPPED"
            
        msg_extra = " (REABERTO POR MUDANÇA DE DATA)" if re_extrair else ""
        logger.info(f"🎯 Extraindo Evento: {num}{msg_extra}")
        try: self.sio.emit('relatar_progresso', {'mensagem': f"🎯 Iniciando extração do evento {num}{msg_extra}..."})
        except: pass

        try:
            # ==========================================================
            # 1. LOOP ANTI-OOPS E CARREGAMENTO
            # ==========================================================
            sucesso_carregamento = False
            for tentativa in range(3):
                if tentativa > 0: logger.info(f"    Recarregando evento {num} (Tentativa {tentativa+1}/3)...")
                self.driver.get(url) 
                time.sleep(4)
                try: 
                    self.driver.switch_to.alert.accept()
                    time.sleep(1)
                except NoAlertPresentException: pass
                
                if self._verificar_tela_erro():
                    logger.warning(f" Tela 'Oops!' detectada. Tentando de novo...")
                    if self._pausa_por_oops(): return "SKIPPED"
                    
                    self.fazer_login_hibrido() 
                    time.sleep(2) 
                    
                    novo_url = self._obter_novo_link_evento(num)
                    if novo_url:
                        logger.info(f"✅ Novo link encontrado para o evento {num}: {novo_url}")
                        url = novo_url
                    else:
                        logger.error(f"❌ Não foi possível encontrar um novo link para o evento {num}. Mantendo o antigo...")
                    
                    continue
                else:
                    self.oops_seguidos = 0
                    sucesso_carregamento = True
                    break
                    
            if not sucesso_carregamento:
                logger.error(f" Erro persistente (Oops!) no evento {num}. Ignorando.")
                return "SKIPPED"

            # ==========================================================
            # 2. IFRAME DA VALE E ESPERA INTELIGENTE
            # ==========================================================
            try: 
                iframe = self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe")))
                self.driver.switch_to.frame(iframe)
                logger.info("    Iframe detectado e acessado.")
            except: pass

            try:
                logger.info("    Aguardando a interface carregar...")
                WebDriverWait(self.driver, 15).until(lambda d: 
                    d.find_elements(By.ID, "participation") or
                    d.find_elements(By.XPATH, "//a[contains(., 'VENTURA COMERCIO VAREJISTA')]") or
                    d.find_elements(By.XPATH, "//a[.//span[text()='Minhas respostas']]") or
                    d.find_elements(By.XPATH, "//a[.//span[text()='Minha resposta']]") or
                    d.find_elements(By.ID, "quote_response_submit") or
                    d.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine") or
                    d.find_elements(By.XPATH, "//a[contains(@class, 'blue rollover button') and contains(@href, 'response_id')]")
                )
            except Exception: pass

            if self.driver.find_elements(By.XPATH, "//h1[contains(text(), 'já terminou')]"):
                logger.warning("   ⚠️ Evento encerrado."); self.driver.switch_to.default_content(); return "ENCERRADO"

            req = "N/A"
            titulo_h1 = ""
            try: 
                titulo_h1 = self.driver.find_element(By.CSS_SELECTOR, "h1.sourcing-wrapper-title").text
                if "NEGOCIAÇÃO" in titulo_h1.upper(): 
                    is_negotiation = True
                match_req = re.search(r'(?i)req[^\d]*(\d+)', titulo_h1)
                if match_req:
                    req = match_req.group(1)
                    self.req = req
            except: pass

            # Termos e condições: anota o evento para o e-mail de termos (mesmo que o robô consiga aceitar)
            aviso_termos.anotar(self, num, ler_termos(self.driver), titulo_h1, is_negotiation)

            vencimento = self.parsear_data_vencimento(venc_tabela) or "N/A"
            venc_inicial = self.capturar_end_time_elemento()
            if venc_inicial:
                vencimento = venc_inicial
                logger.info(f"   📅 Vencimento detectado na tela do evento: {vencimento}")
            frete = "EXW"

            # ==========================================================
            # 3. ACESSO DIRETO (PULANDO O 'PRETENDO' SE NÃO FOR NEGOCIAÇÃO)
            # ==========================================================
            entrou_nos_itens = False
            
            if not is_negotiation:
                xpath_botoes_acesso = [
                    "//a[contains(@class, 'blue rollover button') and contains(@href, 'response_id')]",
                    "//a[contains(., 'VENTURA COMERCIO VAREJISTA')]",
                    "//span[contains(text(), 'VENTURA COMERCIO VAREJISTA')]",
                    "//a[.//span[text()='Minhas respostas']]",
                    "//span[text()='Minhas respostas']",
                    "//a[.//span[text()='Minha resposta']]"
                ]
                
                for xpath in xpath_botoes_acesso:
                    botoes = self.driver.find_elements(By.XPATH, xpath)
                    for b in botoes:
                        if b.is_displayed() or self.driver.execute_script("return arguments[0].offsetWidth > 0;", b):
                            logger.info(f"   🎯 Resposta/Rascunho detectado! Pulando o 'Pretendo'.")
                            self.driver.execute_script("arguments[0].click();", b)
                            time.sleep(4)
                            entrou_nos_itens = True
                            break
                    if entrou_nos_itens: break
            else:
                logger.info("    Rodada de Negociação detectada! Forçando novo aceite...")

            # ==========================================================
            # 4. SE É NEGOCIAÇÃO OU NÃO TEM RASCUNHO, FAZ O ACEITE NORMAL
            # ==========================================================
            if not entrou_nos_itens:
                # "Aceita os Termos e condições?" (div.s-term): marca o Sim de cada termo antes do Pretendo
                aceitar_termos(self.driver)
                try:
                    dropdowns = self.driver.find_elements(By.ID, "participation")
                    if dropdowns:
                        logger.info("    Aceitando evento (Pretendo -> Enviar)...")
                        try:
                            Select(dropdowns[0]).select_by_value("true")
                        except Exception:
                            self.driver.execute_script("arguments[0].value='true'; arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", dropdowns[0])
                        btn_enviar = self.driver.find_elements(By.CSS_SELECTOR, "button.submitIntend")
                        if btn_enviar:
                            self.driver.execute_script("arguments[0].click();", btn_enviar[0])
                            time.sleep(3)
                            try: self.driver.switch_to.alert.accept()
                            except NoAlertPresentException: pass
                except Exception: pass
                    
                # Clicar Inserir resposta
                try:
                    btn_inserir = self.driver.find_elements(By.ID, "quote_response_submit")
                    if btn_inserir and btn_inserir[0].is_displayed():
                        logger.info("    Clicando em Inserir resposta...")
                        self.driver.execute_script("arguments[0].click();", btn_inserir[0])
                        time.sleep(4)
                except Exception: pass

            # ==========================================================
# ==========================================================
            # 5. EXTRAÇÃO E VALIDAÇÃO DOS ITENS (COM RETENTATIVAS OBRIGATÓRIAS)
            # REGRA: Sempre que exibir 'Expected Quantity x Unit Price' ou itens não identificados,
            # DEVE retentar extrair os itens antes de prosseguir para o próximo evento.
            # Cada campo é validado individualmente a cada tentativa.
            # Se persistir após múltiplas tentativas, envia e-mail de alerta e a cotação NÃO é processada com sucesso.
            # ==========================================================
            MAX_TENTATIVAS_ITENS = 3
            itens_validados = False
            itens = []
            tem_anexo_geral = False
            ultimo_motivo_invalido = ""
            teve_expected_quantity = False

            for tentativa in range(1, MAX_TENTATIVAS_ITENS + 1):
                logger.info(f"    🔍 Validação de Itens do evento {num} (Tentativa {tentativa}/{MAX_TENTATIVAS_ITENS})...")

                if tentativa > 1:
                    logger.warning(f"    ⏳ Recarregando página para nova tentativa de extração do evento {num}...")
                    try:
                        self.driver.refresh()
                        time.sleep(6)
                        try: self.driver.switch_to.alert.accept()
                        except: pass
                        self.garantir_logado(url)
                        self.driver.switch_to.default_content()
                        try: self.driver.switch_to.frame(self.driver.find_element(By.TAG_NAME, "iframe"))
                        except: pass
                    except Exception: pass

                linhas = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")

                if len(linhas) == 0:
                    xpath_fallback = [
                        "//a[contains(@class, 'blue rollover button') and contains(@href, 'response_id')]",
                        "//a[contains(., 'VENTURA COMERCIO VAREJISTA')]",
                        "//span[contains(text(), 'VENTURA COMERCIO VAREJISTA')]",
                        "//a[.//span[text()='Minhas respostas']]",
                        "//span[text()='Minhas respostas']",
                        "//a[.//span[text()='Minha resposta']]",
                        "//a[.//span[text()='Itens']]"
                    ]
                    for xpath in xpath_fallback:
                        try:
                            botoes = self.driver.find_elements(By.XPATH, xpath)
                            if botoes:
                                self.driver.execute_script("arguments[0].click();", botoes[0])
                                time.sleep(4)
                                break
                        except: pass

                    self.garantir_logado(url)
                    self.driver.switch_to.default_content()
                    try: self.driver.switch_to.frame(self.driver.find_element(By.TAG_NAME, "iframe"))
                    except: pass
                    linhas = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")

                if len(linhas) == 0:
                    try:
                        dropdowns = self.driver.find_elements(By.ID, "participation")
                        if dropdowns:
                            aceitar_termos(self.driver)   # "Aceita os Termos e condições?" -> Sim, antes do Pretendo
                            try: Select(dropdowns[0]).select_by_value("true")
                            except: self.driver.execute_script("arguments[0].value='true'; arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", dropdowns[0])
                            time.sleep(1)
                            btn_enviar = self.driver.find_elements(By.CSS_SELECTOR, "button.submitIntend")
                            if btn_enviar:
                                self.driver.execute_script("arguments[0].click();", btn_enviar[0])
                                time.sleep(3)
                                try: self.driver.switch_to.alert.accept()
                                except: pass
                    except: pass

                    try:
                        botoes_inserir = self.driver.find_elements(By.ID, "quote_response_submit")
                        if botoes_inserir and botoes_inserir[0].is_displayed():
                            self.driver.execute_script("arguments[0].click();", botoes_inserir[0])
                            time.sleep(4)
                    except: pass

                    self.garantir_logado(url)
                    self.driver.switch_to.default_content()
                    try: self.driver.switch_to.frame(self.driver.find_element(By.TAG_NAME, "iframe"))
                    except: pass
                    linhas = self.driver.find_elements(By.CSS_SELECTOR, "div.s-itemsAndServicesLine")

                if len(linhas) > 0:
                    itens, tem_anexo = self._extrair_itens_linhas(linhas)
                    if tem_anexo: tem_anexo_geral = True
                else:
                    itens = []

                texto_tela = ""
                try: texto_tela = self.driver.find_element(By.TAG_NAME, "body").text
                except: pass

                valido, motivo, tem_eq = validar_itens_evento(itens, texto_tela)
                if tem_eq:
                    teve_expected_quantity = True

                if valido:
                    logger.info(f"    ✅ Sucesso: {len(itens)} item(ns) identificados e validados no evento {num}!")
                    itens_validados = True
                    break
                else:
                    ultimo_motivo_invalido = motivo
                    logger.warning(f"    ⚠️ Falha na validação do evento {num} (Tentativa {tentativa}/{MAX_TENTATIVAS_ITENS}): {motivo}")

            if not itens_validados:
                pendentes_termo = termos_pendentes(self.driver)
                if pendentes_termo:
                    ultimo_motivo_invalido += f" | {pendentes_termo} termo(s) e condições sem o 'Sim' (o portal recusa o 'Pretendo')"
                logger.error(f"   ❌ FALHA DEFINITIVA: Evento {num} persistiu sem itens válidos após {MAX_TENTATIVAS_ITENS} tentativas! ({ultimo_motivo_invalido})")
                self.relatorio_email_stats.setdefault('eventos_sem_itens', []).append(num)
                if teve_expected_quantity:
                    self.relatorio_email_stats.setdefault('eventos_expected_quantity', []).append(num)

                try:
                    self.enviar_relatorio_email()
                except Exception as e_mail:
                    logger.warning(f"Erro ao disparar e-mail de alerta para evento {num}: {e_mail}")

                self.driver.switch_to.default_content()
                return "SKIPPED"

                        # REFORÇO OBRIGATÓRIO DA DATA DE VENCIMENTO (Aba Informações)
            # ==========================================================
            logger.info("       🔍 Confirmando a data de vencimento exata na aba de Informações...")
            try:
                xpath_info = "//a[contains(@class, 'button') and .//span[contains(text(), 'Informa') and contains(text(), 'evento')]]"
                btn_info = self.driver.find_elements(By.XPATH, xpath_info)
                
                if btn_info:
                    self.driver.execute_script("arguments[0].click();", btn_info[0])
                    time.sleep(2) 

                venc_info = self.capturar_end_time_elemento()
                if venc_info:
                    vencimento = venc_info
                        
                logger.info(f"       ✅ Data de vencimento confirmada pelo sistema: {vencimento}")
            except Exception as e:
                logger.warning(f"       ⚠️ Não foi possível forçar a aba de Informações: {e}. Mantendo data anterior: {vencimento}")

            # Se ainda nao tem data valida confirmada, usa a data da tabela do painel (se disponivel)
            if vencimento in ["N/A", ""] and venc_tabela:
                parsed_tabela = self.parsear_data_vencimento(venc_tabela)
                if parsed_tabela:
                    vencimento = parsed_tabela
                    logger.info(f"       ✅ Data de vencimento obtida da tabela do painel: {vencimento}")

            # O frete vem do Incoterms lido nos itens expandidos; a página (já na aba de informações) é só reserva
            frete_pagina = ""
            try: frete_pagina = self.driver.find_element(By.CSS_SELECTOR, "p.s-selectField").text
            except: pass
            frete = self._frete_do_evento(itens, frete_pagina)

            self.driver.switch_to.default_content()
            
            # Ajuste de Compatibilidade para retorno no Vale/Coupa
            if self.__class__.__name__ == "CoupaScraper":
                return {"numero_evento": num, "req_num": getattr(self, 'req', req), "data_vencimento": vencimento, "tipo_frete": frete, "itens": itens, "respondido_origem": ja_respondido, "tem_anexo": tem_anexo_geral}
            else:
                return {"numero_evento": num, "req_num": req, "data_vencimento": vencimento, "tipo_frete": frete, "itens": itens, "tem_anexo": tem_anexo_geral}
            
        except Exception as e:
            logger.error(f" Erro extração {num}: {e}")
            try: self.driver.switch_to.default_content()
            except: pass
            return None
