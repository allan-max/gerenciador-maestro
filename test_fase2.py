"""Fase 2 (docs/melhorias-bots): regras do RESPOSTA, RASCUNHO e verificação do Coupa a partir das pendências."""
import base64
import csv
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

from test_fase0 import LOGGER, Relogio, carregar
from test_planilha_persistencia import Manager, namespace as ns_manager

RAIZ = Path(__file__).parent
novo_status = ns_manager['novo_status']
AGORA = datetime(2026, 10, 17, 10, 0)
FUTURO, PASSADO, HOJE = '20/10/2026', '10/10/2026', '17/10/2026'   # em relação a AGORA
# a verificação usa a data real (datetime.now): datas relativas a hoje
FUT_REAL, PAS_REAL = [(datetime.now() + timedelta(days=d)).strftime('%d/%m/%Y') for d in (5, -5)]


class NovoStatusTest(unittest.TestCase):
    def test_regras_d3(self):
        casos = [
            # (atual, respostas, vencimento) -> esperado
            (('', 1, FUTURO), 'RESPONDIDO'),                 # resposta enviada: não espera o vencimento
            ((None, 2, PASSADO), 'RESPONDIDO'),
            (('', 0, FUTURO), ''),                           # sem resposta, ainda no prazo: nada muda
            (('', 0, PASSADO), 'NÃO RESPONDIDO'),
            (('RESPONDIDO', 0, PASSADO), 'RESPONDIDO'),      # nunca volta (marcado à mão ou pelo robô)
            (('NÃO RESPONDIDO', 1, PASSADO), 'RESPONDIDO'),  # resposta enviada depois
            (('RASCUNHO', 0, FUTURO), 'RASCUNHO'),
            (('RASCUNHO', 0, PASSADO), 'NÃO RESPONDIDO'),    # rascunho que ninguém enviou
            (('RASCUNHO', 1, FUTURO), 'RESPONDIDO'),
            (('NÃƒO RESPONDIDO', 0, PASSADO), 'NÃO RESPONDIDO'),   # corrige o acento quebrado
            (('', None, PASSADO), ''),                       # célula ilegível: não afirma nada
            ((None, None, PASSADO), None),
            (('', 0, 'N/A'), ''),                            # sem data: não dá para dizer que venceu
            (('', 0, '10/25/2026'), ''),                     # MM/DD (25 não é mês) = 25/10, no prazo
        ]
        for (atual, respostas, venc), esperado in casos:
            with self.subTest(atual=atual, respostas=respostas, venc=venc):
                self.assertEqual(novo_status(atual, respostas, venc, AGORA), esperado)

    def test_no_dia_do_vencimento_vale_a_partir_das_14h30(self):
        self.assertEqual(novo_status('', 0, HOJE, datetime(2026, 10, 17, 14, 29)), '')
        self.assertEqual(novo_status('', 0, HOJE, datetime(2026, 10, 17, 14, 30)), 'NÃO RESPONDIDO')


class DataVencimentoTest(unittest.TestCase):
    def test_ano_com_2_digitos(self):
        # a base de produção tem vencimentos como '19/10/26' (4 linhas em 19/10/2026)
        data_vencimento = ns_manager['data_vencimento']
        self.assertEqual(data_vencimento('19/10/26'), data_vencimento('19/10/2026'))
        self.assertEqual(data_vencimento('12/23/2025').isoformat(), '2025-12-23')
        self.assertEqual(novo_status('', 0, '10/10/26', AGORA), 'NÃO RESPONDIDO')


class BaseTemporaria(unittest.TestCase):
    LINHAS = []

    def setUp(self):
        self.logs = patch.object(ns_manager['logger'], 'handle')
        self.logs.start()
        self.addCleanup(self.logs.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.cota = self.dir / 'COTAÇÕES.json'
        (self.dir / 'PEDIDOS.json').write_text(json.dumps({'PEDIDOS': []}), encoding='utf-8')
        self.cota.write_text(json.dumps({'COTAÇÃO': self.LINHAS}, ensure_ascii=False), encoding='utf-8')
        eventos = patch('eventos.registrar')   # o evento da tela Atividade não é o assunto aqui
        eventos.start()
        self.addCleanup(eventos.stop)
        self.manager = self.novo_manager()

    def novo_manager(self):
        m = Manager.__new__(Manager)
        m.lock, m.iniciado = threading.RLock(), False
        m.cotacoes, m.pedidos = {'COTAÇÃO': []}, {'PEDIDOS': []}
        m.caminho_cotacoes, m.caminho_pedidos = str(self.cota), str(self.dir / 'PEDIDOS.json')
        m.map_cota = {1: 'COTAÇÃO', 2: 'VENCIMENTO', 9: 'RESPOSTA'}
        return m

    def respostas(self):
        return {r['COTAÇÃO'] + ('' if i == 0 else f'#{i}'): r.get('RESPOSTA')
                for i, r in enumerate(json.loads(self.cota.read_text(encoding='utf-8'))['COTAÇÃO'])}


class GravacaoEmLoteTest(BaseTemporaria):
    LINHAS = [{'COTAÇÃO': '1', 'VENCIMENTO': PASSADO, 'RESPOSTA': ''},
              {'COTAÇÃO': '1', 'VENCIMENTO': PASSADO, 'RESPOSTA': 'NÃO RESPONDIDO'},   # duplicada
              {'COTAÇÃO': '2', 'VENCIMENTO': PASSADO, 'RESPOSTA': ''},
              {'COTAÇÃO': '3', 'VENCIMENTO': FUTURO, 'RESPOSTA': 'RESPONDIDO'}]

    def ler(self):
        return [r.get('RESPOSTA') for r in json.loads(self.cota.read_text(encoding='utf-8'))['COTAÇÃO']]

    def test_aplica_em_todas_as_linhas_do_numero_e_grava_uma_vez(self):
        with patch.object(self.manager, '_salvar_arquivo', wraps=self.manager._salvar_arquivo) as salvar:
            mudancas = self.manager.atualizar_status_respostas('COTAÇÃO', {'1': 1, '2': None, '3': 0}, 1, 9, agora=AGORA)
        self.assertEqual(salvar.call_count, 1)
        self.assertEqual(mudancas, ['1: RESPONDIDO', '1: RESPONDIDO'])
        self.assertEqual(self.ler(), ['RESPONDIDO', 'RESPONDIDO', '', 'RESPONDIDO'])
        self.assertEqual(self.manager.atualizar_status_respostas('COTAÇÃO', {'1': 1}, 1, 9, agora=AGORA), [])

    def test_bool_antigo_da_vale_continua_funcionando(self):
        self.manager.atualizar_status_respostas('COTAÇÃO', {'2': False}, 1, 9, agora=AGORA)
        self.assertEqual(self.ler()[2], 'NÃO RESPONDIDO')

    def test_muitas_mudancas_fazem_backup_antes(self):
        with patch.dict(ns_manager, BACKUP_A_PARTIR_DE=2):
            self.manager.atualizar_status_respostas('COTAÇÃO', {'1': 1}, 1, 9, agora=AGORA)
        (backup,) = self.dir.glob('COTAÇÕES antes-verificacao *.json')
        self.assertEqual([r['RESPOSTA'] for r in json.loads(backup.read_text(encoding='utf-8'))['COTAÇÃO']][:2], ['', 'NÃO RESPONDIDO'])


class RascunhoTest(BaseTemporaria):
    LINHAS = [{'COTAÇÃO': '7', 'VENCIMENTO': FUTURO, 'RESPOSTA': ''},
              {'COTAÇÃO': '8', 'VENCIMENTO': FUTURO, 'RESPOSTA': 'RESPONDIDO'}]

    def test_rascunho_nao_rebaixa_respondida_e_nao_regrava(self):
        self.assertEqual(self.manager.marcar_rascunho('7'), 1)
        self.assertEqual(self.manager.marcar_rascunho('7'), 0)
        self.assertEqual(self.manager.marcar_rascunho('8'), 0)
        self.assertEqual([r['RESPOSTA'] for r in json.loads(self.cota.read_text(encoding='utf-8'))['COTAÇÃO']], ['RASCUNHO', 'RESPONDIDO'])

    def test_anexo_com_nome_malicioso_fica_na_pasta_temporaria(self):
        gravar = carregar(RAIZ / 'bots/coupa/mixins/resposta.py', {'_gravar_anexo'}, 'RespostaMixin', os=os, re=re, base64=base64)['_gravar_anexo']
        dado = base64.b64encode(b'pdf').decode()
        for nome in ('..\\..\\evil.pdf', '../evil.pdf', '..', '', 'pedido:12?.pdf'):
            with self.subTest(nome):
                caminho = Path(gravar(str(self.dir), {'name': nome, 'data': dado}))
                self.assertEqual(caminho.parent, self.dir)
                self.assertEqual(caminho.read_bytes(), b'pdf')


# ---------- verificação com um portal falso ----------
class Celula(SimpleNamespace):
    pass


class Linha:
    def __init__(self, num, qtd='0'):
        self.num, self.qtd = num, qtd

    def find_element(self, por, valor):
        if self.num is None:
            raise RuntimeError('stale element')
        if valor == 'a span.dt_open_link':
            return Celula(text=self.num)
        if self.qtd is None:
            raise NoSuchElementException('sem célula')
        return Celula(text=self.qtd)


class Portal:
    def __init__(self, paginas):
        self.paginas, self.atual, self.cliques = paginas, 0, 0
        proximo = self

        class Botao:
            def is_displayed(self):
                return True

            def get_attribute(self, nome):
                return 'disabled' if proximo.atual == len(proximo.paginas) - 1 else ''

            def find_element(self, por, valor):
                return Celula(get_attribute=lambda n: '')
        self.botao = Botao()

    def get(self, url):
        self.atual = 0

    def find_elements(self, por, valor):
        return self.paginas[self.atual]

    def find_element(self, por, valor):
        return self.botao

    def execute_script(self, script, *args):
        if 'click' in script:
            self.atual += 1
            self.cliques += 1


class VerificacaoTest(BaseTemporaria):
    LINHAS = [{'COTAÇÃO': '100', 'VENCIMENTO': FUT_REAL, 'RESPOSTA': ''},
              {'COTAÇÃO': '101', 'VENCIMENTO': PAS_REAL, 'RESPOSTA': 'RASCUNHO'},
              {'COTAÇÃO': '102', 'VENCIMENTO': PAS_REAL, 'RESPOSTA': ''},
              {'COTAÇÃO': '103', 'VENCIMENTO': PAS_REAL, 'RESPOSTA': 'RESPONDIDO'},
              {'COTAÇÃO': '104', 'VENCIMENTO': PAS_REAL, 'RESPOSTA': '', 'VENDEDOR': 'ANA'},
              {'COTAÇÃO': '55000', 'VENCIMENTO': PAS_REAL, 'RESPOSTA': ''},          # da Vale: não é alvo do Coupa
              {'COTAÇÃO': '105', 'VENCIMENTO': PAS_REAL, 'RESPOSTA': 'NÃO RESPONDIDO'}]   # venceu há 5 dias: ainda conferida

    def rodar(self, paginas, db=('100', '101', '102', '103', '104', '105'), portal=None):
        relatorios = self.dir / 'relatorios'
        ns = carregar(RAIZ / 'bots/coupa/mixins/verificacao.py', {'MAX_PAGINAS_PADRAO', 'JANELA_NAO_RESPONDIDO_DIAS', 'VerificacaoMixin'},
                      time=Relogio(), By=By, NoSuchElementException=NoSuchElementException, csv=csv, datetime=datetime, timedelta=timedelta,
                      WebDriverWait=lambda d, t: SimpleNamespace(until=lambda c: True),
                      EC=SimpleNamespace(presence_of_element_located=lambda x: x), PASTA_RELATORIOS=relatorios)
        emitidos, jsonl = [], []
        bot = type('Bot', (ns['VerificacaoMixin'],), {})()
        bot.__dict__.update(driver=(portal or Portal)(paginas), EVENTS_URL='x', NOME_ABA_ALVO='COTAÇÃO', config={}, solicitacao_parada=False,
                            db_eventos={n: {'respondido': False} for n in db}, garantir_logado=lambda url: None,
                            atualizar_status_respondido_jsonl=jsonl.append,
                            sio=SimpleNamespace(emit=lambda e, d: emitidos.append((e, d))))
        modulo = SimpleNamespace(planilha_manager=self.manager,
                                 **{k: ns_manager[k] for k in ('status_atual', 'data_vencimento', 'RASCUNHO', 'NAO_RESPONDIDO')})
        with patch.dict(sys.modules, planilha_manager=modulo), patch.object(LOGGER, 'handle'):
            bot.fase_verificacao()
        return bot, emitidos, jsonl, relatorios

    def test_aplica_regras_e_relata_as_nao_encontradas(self):
        paginas = [[Linha('100', '1'), Linha('102', None), Linha('103', '0'), Linha(None)],
                   [Linha('101', '0'), Linha('999', '3'), Linha('55000', '1'), Linha('100', '0')],   # 100 repetido com 0
                   [Linha('998', '0'), Linha('105', '1')]]
        bot, emitidos, jsonl, relatorios = self.rodar(paginas)
        self.assertEqual(emitidos[-1], ('tarefa_concluida', {'evento': 'Verificação de Status', 'sucesso': True}))
        self.assertEqual(bot.driver.cliques, 2)                                   # 104 nunca aparece: lê até a última
        self.assertEqual(self.respostas(), {'100': 'RESPONDIDO', '101#1': 'NÃO RESPONDIDO', '102#2': '',
                                            '103#3': 'RESPONDIDO', '104#4': '', '55000#5': '', '105#6': 'RESPONDIDO'})
        self.assertEqual(jsonl, ['100', '105'])          # só eventos deste robô
        self.assertIn('em 3 página(s)', emitidos[-2][1]['mensagem'])
        self.assertIn('1 linhas ilegíveis', emitidos[-2][1]['mensagem'])
        with open(relatorios / f'verificacao_coupa_{datetime.now():%Y-%m-%d}.csv', encoding='utf-8-sig') as f:
            self.assertEqual(list(csv.reader(f, delimiter=';')), [['COTAÇÃO', 'VENCIMENTO', 'RESPOSTA', 'VENDEDOR'], ['104', PAS_REAL, '', 'ANA']])

    def test_para_quando_acha_todas_as_pendentes(self):
        paginas = [[Linha('100', '0'), Linha('102', '1')], [Linha('101', '0')], [Linha('104', '0')]]
        bot, *_ = self.rodar(paginas, db=('100', '102'))
        self.assertEqual(bot.driver.cliques, 0)
        self.assertEqual(self.respostas()['102#2'], 'RESPONDIDO')

    def test_portal_que_cai_no_meio_grava_o_que_ja_leu(self):
        class PortalInstavel(Portal):
            def find_elements(self, por, valor):
                if self.atual == 1:
                    raise RuntimeError('invalid session id')
                return super().find_elements(por, valor)
        paginas = [[Linha('100', '1')], [Linha('101', '0')], [Linha('104', '0')]]
        _, emitidos, *_ = self.rodar(paginas, portal=PortalInstavel)
        self.assertFalse(emitidos[-1][1]['sucesso'])
        self.assertEqual(self.respostas()['100'], 'RESPONDIDO')


if __name__ == '__main__':
    unittest.main()
