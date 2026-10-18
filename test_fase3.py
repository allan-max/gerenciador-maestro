"""Fase 3 (docs/melhorias-bots): verificação diária da Vale com OTP, mesmas regras do RESPOSTA e responder."""
import base64
import csv
import json
import os
import re
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from selenium.webdriver.common.by import By

from test_fase0 import LOGGER, Relogio, carregar
from test_fase2 import FUT_REAL, PAS_REAL, BaseTemporaria
from test_planilha_persistencia import namespace as ns_manager

RAIZ = Path(__file__).parent
ARQ_VALE = RAIZ / 'bots/vale_bot.py'
METODOS = {'verificar_eventos', '_esperar_otp', '_pendentes_vale', '_ler_lista_vale', '_relatorio_nao_encontradas_vale'}
CONSTANTES = {'VALE_URL_VERIFICACAO_PADRAO', 'OTP_TIMEOUT_S', 'MAX_PAGINAS_VERIFICACAO', 'JANELA_NAO_RESPONDIDO_DIAS'}


class PilotoTest(unittest.TestCase):
    def test_vale_verifica_uma_vez_por_dia_a_partir_das_9h(self):
        deve = carregar(RAIZ / 'gerenciador.py', {'deve_verificar_vale'})['deve_verificar_vale']
        hoje = date(2026, 10, 19)
        self.assertFalse(deve(datetime(2026, 10, 19, 8, 59), None))
        self.assertTrue(deve(datetime(2026, 10, 19, 9, 0), None))
        self.assertFalse(deve(datetime(2026, 10, 19, 15, 0), hoje))           # já tentou hoje
        self.assertTrue(deve(datetime(2026, 10, 20, 9, 30), hoje))

    def test_ciclo_da_vale_chama_verificar(self):
        fonte = (RAIZ / 'gerenciador.py').read_text(encoding='utf-8')
        trecho = fonte[fonte.index('if deve_verificar_vale(datetime.now(), ultimo_dia_verificacao_vale):'):][:600]
        self.assertIn("sio.emit('comando_direto', {'modo': 'verificar', 'portal': 'vale'})", trecho)


class Celula(SimpleNamespace):
    def clear(self): pass
    def send_keys(self, *a): pass
    def click(self): pass


class LinhaVale:
    def __init__(self, num, qtd):
        self.num, self.qtd = num, qtd

    def find_element(self, por, valor):
        if 'dt_open_link' in valor:
            return Celula(text=self.num)
        if self.qtd is None:
            raise RuntimeError('sem célula')
        return Celula(text=self.qtd)


class PortalVale:
    def __init__(self, paginas):
        self.paginas, self.atual, self.abas = paginas, 0, ['principal']
        self.switch_to = SimpleNamespace(window=lambda w: None)

    @property
    def window_handles(self):
        return self.abas

    def get(self, url):
        self.url = url

    def execute_script(self, script, *args):
        if "window.open" in script:
            self.abas.append('verificacao')
        elif 'click' in script:
            self.atual += 1

    def close(self):
        self.abas.pop()

    def find_elements(self, por, valor):
        return self.paginas[self.atual]

    def find_element(self, por, valor):
        if self.atual >= len(self.paginas) - 1:
            raise RuntimeError('sem botão Avançar')
        return Celula()


class VerificacaoValeTest(BaseTemporaria):
    LINHAS = [{'COTAÇÃO': '54328', 'VENCIMENTO': FUT_REAL, 'RESPOSTA': ''},
              {'COTAÇÃO': '54291', 'VENCIMENTO': PAS_REAL, 'RESPOSTA': ''},
              {'COTAÇÃO': '54268', 'VENCIMENTO': PAS_REAL, 'RESPOSTA': 'RASCUNHO', 'VENDEDOR': 'BIA'},
              {'COTAÇÃO': '290000', 'VENCIMENTO': PAS_REAL, 'RESPOSTA': ''}]          # do Coupa: não é alvo da Vale

    def rodar(self, paginas, otp='123456', parada=False):
        relogio, emitidos = Relogio(), []
        ns = carregar(ARQ_VALE, CONSTANTES)
        ns = carregar(ARQ_VALE, METODOS, 'ValeScraper', time=relogio, By=By, csv=csv, os=os, datetime=datetime,
                      timedelta=timedelta, ambiente=SimpleNamespace(RAIZ=str(self.dir)),
                      Keys=SimpleNamespace(RETURN='\n'),
                      EC=SimpleNamespace(presence_of_element_located=lambda x: x, element_to_be_clickable=lambda x: x),
                      **{k: v for k, v in ns.items() if k in CONSTANTES})
        bot = type('Bot', (), {k: ns[k] for k in METODOS})()

        def emit(evento, dados=None):
            emitidos.append((evento, dados))
            if evento == 'pedir_otp_usuario' and otp:
                bot.otp_atual = otp          # alguém digitou o código no painel
        bot.__dict__.update(driver=PortalVale(paginas), config={}, NOME_ABA_ALVO='COTAÇÃO', solicitacao_parada=parada,
                            db_eventos={n: {} for n in ('54328', '54291', '54268')}, sio=SimpleNamespace(emit=emit),
                            wait=SimpleNamespace(until=lambda cond: Celula()))
        modulo = SimpleNamespace(planilha_manager=self.manager,
                                 **{k: ns_manager[k] for k in ('status_atual', 'data_vencimento', 'RASCUNHO', 'NAO_RESPONDIDO')})
        with patch.dict(sys.modules, planilha_manager=modulo), patch.object(LOGGER, 'handle'), \
                patch('eventos.registrar') as registrar:
            bot.verificar_eventos()
        return bot, emitidos, registrar, relogio

    def test_aplica_as_regras_e_relata_as_nao_encontradas(self):
        paginas = [[LinhaVale('54328', '1'), LinhaVale('290000', '2')], [LinhaVale('54291', None)]]
        bot, emitidos, _, _ = self.rodar(paginas)
        self.assertEqual(emitidos[-1], ('tarefa_concluida', {'evento': 'Verificação Vale', 'sucesso': True}))
        self.assertEqual(self.respostas(), {'54328': 'RESPONDIDO', '54291#1': '', '54268#2': 'RASCUNHO', '290000#3': ''})
        self.assertEqual(bot.driver.abas, ['principal'])      # fechou a aba da verificação
        with open(self.dir / 'relatorios' / f'verificacao_vale_{datetime.now():%Y-%m-%d}.csv', encoding='utf-8-sig') as f:
            self.assertEqual(list(csv.reader(f, delimiter=';'))[1], ['54268', PAS_REAL, 'RASCUNHO', 'BIA'])

    def test_lista_que_nao_carrega_e_erro_e_nao_lista_vazia(self):
        antes = self.cota.read_bytes()
        bot, emitidos, _, _ = self.rodar([[]])
        self.assertFalse(emitidos[-1][1]['sucesso'])
        self.assertIn('não carregou', emitidos[-1][1]['erro'])
        self.assertEqual(self.cota.read_bytes(), antes)
        self.assertFalse((self.dir / 'relatorios').exists())   # não acusa todas como "não encontradas"

    def test_parada_da_extracao_anterior_nao_cancela_a_verificacao(self):
        paginas = [[LinhaVale('54328', '1')]]
        _, emitidos, _, _ = self.rodar(paginas, parada=True)
        self.assertTrue(emitidos[-1][1]['sucesso'])

    def test_sem_otp_pula_registra_e_nao_grava(self):
        antes = self.cota.read_bytes()
        _, emitidos, registrar, relogio = self.rodar([[LinhaVale('54328', '1')]], otp=None)
        self.assertFalse(emitidos[-1][1]['sucesso'])
        self.assertIn('OTP não informado', emitidos[-1][1]['erro'])
        registrar.assert_called_once()
        self.assertEqual(self.cota.read_bytes(), antes)
        self.assertLess(relogio.agora - 1000, 200)            # 3 min, não trava


class FonteValeTest(unittest.TestCase):
    fonte = ARQ_VALE.read_text(encoding='utf-8')

    def test_sem_laco_infinito_do_excel_e_otp_fora_do_log(self):
        self.assertNotIn('_colorir_excel_verificacao', self.fonte)
        self.assertNotIn('{self.otp_atual}', self.fonte)

    def test_extracao_nao_marca_respondido(self):
        self.assertIn('"respondido": False, "extraido": True,', self.fonte)

    def test_anexo_com_nome_malicioso_fica_na_pasta(self):
        import tempfile
        gravar = carregar(ARQ_VALE, {'_gravar_anexo'}, 'ValeScraper', os=os, re=re, base64=base64)['_gravar_anexo']
        with tempfile.TemporaryDirectory() as tmp:
            for nome in ('..\\..\\x.pdf', '../x.pdf', '..', 'a:b?.pdf'):
                caminho = Path(gravar(tmp, {'name': nome, 'data': base64.b64encode(b'1').decode()}))
                self.assertEqual(caminho.parent, Path(tmp))
            # datasheet e DAV com o mesmo nome: os dois ficam
            a = gravar(tmp, {'name': 'igual.pdf', 'data': base64.b64encode(b'ds').decode()})
            b = gravar(tmp, {'name': 'igual.pdf', 'data': base64.b64encode(b'dav').decode()})
            self.assertNotEqual(a, b)
            self.assertEqual((Path(a).read_bytes(), Path(b).read_bytes()), (b'ds', b'dav'))


if __name__ == '__main__':
    unittest.main()
