# -*- coding: utf-8 -*-
"""Verificação diária das respostas no portal da Vale (docs/melhorias-bots, fase 3)."""
import logging
import csv
import os
import time
from datetime import datetime, timedelta
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from nucleo import ambiente

logger = logging.getLogger("ValeApp")


# Verificação (docs/melhorias-bots, fase 3)
VALE_URL_VERIFICACAO_PADRAO = "https://valebasemetals.coupahost.com/quotes/external_responses/824e8a0982de566c7bebeb7315a6bd89b533033a175c5acddf970e59ccc1e3b7954b1ce5107982e6/terms?quote_request_id=6213&source=event_invitation"   # CONFIG.json "vale_url_verificacao" muda
JANELA_NAO_RESPONDIDO_DIAS = 7   # NÃO RESPONDIDO recente continua sendo conferido


class VerificacaoMixin:
    # ========================================================
    # 8. VERIFICAÇÃO DE EVENTOS (COM GESTÃO DE NOVA ABA)
    # ========================================================
    def verificar_eventos(self):
        """Lê a coluna de respostas enviadas (precisa do OTP) e aplica planilha_manager.novo_status nas cotações da Vale.
        Sem OTP em OTP_TIMEOUT_S, pula e registra o motivo (não trava o ciclo)."""
        logger.info("--- VERIFICAÇÃO DE EVENTOS VALE ---")
        self.solicitacao_parada = False   # parada/login esgotado da extração anterior não cancela a verificação
        try:
            self.driver.execute_script("window.open('');")
            self.driver.switch_to.window(self.driver.window_handles[-1])
            self.driver.get(self.config.get('vale_url_verificacao', VALE_URL_VERIFICACAO_PADRAO))
            time.sleep(3)
            if not self._esperar_otp():
                return

            logger.info(" OTP recebido. Injetando no portal...")
            campo_otp = self.wait.until(EC.presence_of_element_located((By.ID, "supplier_otp")))
            campo_otp.clear()
            campo_otp.send_keys(self.otp_atual)
            campo_otp.send_keys(Keys.RETURN)
            time.sleep(4)
            self.wait.until(EC.element_to_be_clickable((By.ID, "home"))).click()
            time.sleep(4)

            pendentes = self._pendentes_vale()
            logger.info(f" Verificando {len(pendentes)} cotações pendentes da Vale...")
            lidos, ilegiveis, paginas, erro = self._ler_lista_vale(pendentes)

            # Só números deste robô: um número do Coupa igual ao de um evento da Vale não é tocado
            vistos = {n: q for n, q in lidos.items() if n in self.db_eventos}
            from nucleo.planilha_manager import planilha_manager
            mudancas = planilha_manager.atualizar_status_respostas(self.NOME_ABA_ALVO, vistos, 1, 9)
            if erro:
                raise erro   # o que foi lido já está gravado

            achadas = sum(1 for n in pendentes if n in vistos)
            relatorio = self._relatorio_nao_encontradas_vale(pendentes, vistos)
            resumo = (f"Verificação Vale: {len(vistos)} eventos lidos em {paginas} página(s), {achadas}/{len(pendentes)} "
                      f"pendentes encontradas, {len(mudancas)} status alterados, {ilegiveis} linhas ilegíveis")
            logger.info(f"✅ {resumo}" + (f". Não encontradas: {relatorio}" if relatorio else ""))
            from nucleo import eventos
            eventos.registrar('vale', 'verificacao_concluida', 'ok', resumo, detalhe={'encontradas': achadas, 'total': len(pendentes), 'mudancas': len(mudancas), 'paginas': paginas})
            self.sio.emit('relatar_progresso_vale', {'mensagem': f"✅ {resumo}"})
            self.sio.emit('tarefa_concluida', {'evento': 'Verificação Vale', 'sucesso': True})

        except Exception as e:
            logger.exception(f" Erro na verificação: {e}")
            self.sio.emit('tarefa_concluida', {'evento': 'Verificação Vale', 'sucesso': False, 'erro': str(e)})

        finally:
            try:
                if len(self.driver.window_handles) > 1:
                    logger.info(" Fechando aba extra e retornando ao painel principal...")
                    self.driver.close()
                    self.driver.switch_to.window(self.driver.window_handles[0])
            except Exception as e:
                logger.error(f"Erro ao tentar restaurar abas: {e}")

    def _pendentes_vale(self):
        """Cotações deste robô (as do eventos_nimbi.jsonl) sem resposta confirmada: vazias, RASCUNHO e
        NÃO RESPONDIDO que venceram há poucos dias."""
        from nucleo.planilha_manager import planilha_manager, status_atual, data_vencimento, RASCUNHO, NAO_RESPONDIDO
        planilha_manager.iniciar()
        limite = datetime.now().date() - timedelta(days=JANELA_NAO_RESPONDIDO_DIAS)

        def alvo(r):
            s = status_atual(r.get('RESPOSTA'))
            d = data_vencimento(r.get('VENCIMENTO'))
            return s in ('', RASCUNHO) or (s == NAO_RESPONDIDO and d is not None and d >= limite)
        return {str(r.get('COTAÇÃO')).strip(): r for r in planilha_manager.cotacoes.get('COTAÇÃO', [])
                if isinstance(r, dict) and str(r.get('COTAÇÃO') or '').strip() in self.db_eventos and alvo(r)}

    def _ler_lista_vale(self, pendentes):
        """{número: respostas} (None = célula ilegível). Se o portal falhar no meio, devolve o que já leu e o erro."""
        vistos, ilegiveis, paginas = {}, 0, 0
        ordem = lambda q: -1 if q is None else q   # número repetido: vale a maior quantidade lida
        from nucleo.ambiente import paginas_verificacao
        max_paginas = paginas_verificacao('vale')   # CONFIG.json, editável no painel (padrão 60; 15 linhas por página)
        try:
            while paginas < max_paginas:
                if getattr(self, 'solicitacao_parada', False):
                    raise Exception("Processo cancelado pelo utilizador.")
                time.sleep(2)
                # coupa_datatable_row é a tela antiga; a extração já usa coupaTable__row
                linhas = self.driver.find_elements(By.XPATH, "//tr[contains(@class, 'coupa_datatable_row') or contains(@class, 'coupaTable__row')]")
                for _ in range(3 if paginas == 0 else 1):   # página lenta não é "fim da lista": espera de novo antes de desistir
                    if linhas:
                        break
                    time.sleep(5)
                    linhas = self.driver.find_elements(By.XPATH, "//tr[contains(@class, 'coupa_datatable_row') or contains(@class, 'coupaTable__row')]")
                if not linhas:
                    if paginas == 0:
                        raise Exception("A lista de eventos da Vale não carregou.")
                    break
                paginas += 1
                for linha in linhas:
                    try:
                        num = linha.find_element(By.XPATH, ".//span[contains(@class, 'dt_open_link')]").text.strip()
                    except Exception:
                        ilegiveis += 1
                        continue
                    try:
                        texto = linha.find_element(By.XPATH, ".//td[contains(@class, 's-datatable-cell-num_responses')]").text.strip()
                        qtd = int(texto) if texto.isdigit() else None
                    except Exception:
                        qtd = None
                    if num and (num not in vistos or ordem(qtd) > ordem(vistos[num])):
                        vistos[num] = qtd
                faltam = sum(1 for n in pendentes if n not in vistos)
                logger.info(f"    Página {paginas}: {len(linhas)} linhas, faltam {faltam} pendentes.")
                if not faltam:
                    break
                try:
                    botao = self.driver.find_element(By.XPATH, "//a[contains(@class, 'next_page') and contains(text(), 'Avançar')]")
                except Exception:
                    logger.info("✅ Fim das páginas atingido.")
                    break
                self.driver.execute_script("arguments[0].click();", botao)
                time.sleep(3)
        except Exception as e:
            logger.exception(f"Leitura interrompida na página {paginas}; gravando o que já foi lido.")
            return vistos, ilegiveis, paginas, e
        return vistos, ilegiveis, paginas, None

    def _relatorio_nao_encontradas_vale(self, pendentes, vistos):
        faltando = [r for n, r in pendentes.items() if n not in vistos]
        pasta = os.path.join(ambiente.RAIZ, 'relatorios')
        caminho = os.path.join(pasta, f"verificacao_vale_{datetime.now():%Y-%m-%d}.csv")
        if not faltando:
            if os.path.exists(caminho):
                os.remove(caminho)   # relatório de uma rodada anterior do dia não vale mais
            return None
        os.makedirs(pasta, exist_ok=True)
        with open(caminho, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(['COTAÇÃO', 'VENCIMENTO', 'RESPOSTA', 'VENDEDOR'])
            w.writerows([r.get('COTAÇÃO'), r.get('VENCIMENTO'), r.get('RESPOSTA') or '', r.get('VENDEDOR')] for r in faltando)
        return caminho
