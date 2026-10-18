"""Fase 1 (docs/melhorias-bots): modo teste correto, trava antes de gravar na produção e recuperação de perdidas."""
import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ambiente
import instancia_unica
from resposta_cotacao import marcar_respondida
from scripts import recuperar_perdidas
from test_fase0 import LOGGER, carregar
from test_planilha_persistencia import Manager, namespace as ns_manager

RAIZ = Path(__file__).parent
PROD = ambiente.BASE_PRODUCAO + r'\database\Banco-de-dados\COTAÇÕES.json'


class AmbienteTest(unittest.TestCase):
    def test_modo_teste_reconhece_127_0_0_1_do_testar_local(self):
        casos = {'http://127.0.0.1:8000': True, 'http://localhost:8000': True, 'http://localhost:3000/x': True,
                 'https://maestro.ventura.inf.br': False, '': False}
        for url, esperado in casos.items():
            with self.subTest(url), patch.dict(os.environ, {'URL_SERVIDOR': url}, clear=False):
                os.environ.pop('MAESTRO_TESTE', None)
                self.assertEqual(ambiente.modo_teste(), esperado)
                self.assertEqual(ambiente.base_dir(), ambiente.BASE_TESTE if esperado else ambiente.BASE_PRODUCAO)
        with patch.dict(os.environ, {'URL_SERVIDOR': 'https://maestro.ventura.inf.br', 'MAESTRO_TESTE': '1'}):
            self.assertTrue(ambiente.modo_teste())

    def test_producao_so_grava_com_a_trava(self):
        self.assertTrue(ambiente.eh_producao(PROD))
        self.assertTrue(ambiente.eh_producao(PROD.replace('\\', '/').lower()))
        self.assertFalse(ambiente.eh_producao(r'C:\temp\COTAÇÕES.json'))
        with patch.object(instancia_unica, '_ativa', None):
            with self.assertRaises(PermissionError):
                ambiente.exigir_trava(PROD)
            ambiente.exigir_trava(r'C:\temp\COTAÇÕES.json')   # fora da produção: livre
        with tempfile.TemporaryFile() as arq, patch.object(instancia_unica, '_ativa', arq):
            ambiente.exigir_trava(PROD)

    def test_trava_ativa_depois_de_pegar_a_instancia(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(instancia_unica, '_ativa', None):
            arq = instancia_unica.instancia_unica(tmp)
            self.assertTrue(instancia_unica.trava_ativa())
            arq.close()
            self.assertFalse(instancia_unica.trava_ativa())


class GravacaoSemTravaTest(unittest.TestCase):
    def test_manager_e_marcar_respondida_recusam_producao_sem_trava(self):
        m = Manager.__new__(Manager)
        m.lock, m.iniciado, m._originais = __import__('threading').RLock(), True, {PROD: b''}
        m.caminho_cotacoes = PROD
        with patch.object(instancia_unica, '_ativa', None):
            with self.assertRaises(PermissionError):
                m._salvar_arquivo(PROD, {'COTAÇÃO': []})
            with self.assertRaises(PermissionError):
                marcar_respondida(m, dict.fromkeys(('COTAÇÃO', 'VENDEDOR', 'LOCALIDADE', 'ITEM', 'VENCIMENTO'), 'x'))

    def test_modo_teste_cria_base_vazia_fora_da_producao(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'MAESTRO_TESTE': '1'}), \
                patch.object(ambiente, 'BASE_TESTE', tmp):
            m = ns_manager['PlanilhaManager']()
            self.assertFalse(ambiente.eh_producao(m.caminho_cotacoes))
            self.assertEqual(json.loads(Path(m.caminho_cotacoes).read_text(encoding='utf-8')), {'COTAÇÃO': []})
            with patch.object(ns_manager['logger'], 'handle'):
                m.iniciar()
                self.assertTrue(m.adicionar_linhas('COTAÇÃO', [['1', '20/10/2026', 'Item']]))


class RecuperarPerdidasTest(unittest.TestCase):
    def test_abertas_saem_do_jsonl_e_vencidas_vao_para_o_csv(self):
        linhas_coupa = [
            {'titulo': '292832', 'data': '2026-10-13', 'respondido': True, 'vencimento_tabela': '26/10/26'},   # aberta, sumiu
            {'titulo': '292770', 'data': '2026-10-13', 'respondido': True, 'vencimento_tabela': '14/10/26'},   # vencida, sumiu
            {'titulo': '292800', 'data': '2026-10-13', 'respondido': True, 'vencimento_tabela': '26/10/26'},   # está no banco
            {'titulo': '290000', 'data': '2026-10-01', 'respondido': True, 'vencimento_tabela': '26/10/26'},   # fora do período
        ]
        linhas_vale = [{'titulo': '54328', 'data': '2026-10-13', 'respondido': True, 'vencimento_tabela': '10/26/26 02:30 pm -03'}]
        with tempfile.TemporaryDirectory() as tmp:
            for nome, linhas in (('eventos.jsonl', linhas_coupa), ('eventos_nimbi.jsonl', linhas_vale)):
                Path(tmp, nome).write_text(''.join(json.dumps(l) + '\n' for l in linhas), encoding='utf-8')
            antes = Path(tmp, 'eventos.jsonl').read_bytes()
            args = (tmp, {'292800'}, '2026-10-13', '2026-10-14')
            kw = dict(hoje=date(2026, 10, 18), pasta_relatorios=os.path.join(tmp, 'rel'))

            abertas, vencidas, relatorio = recuperar_perdidas.executar(*args, gravar=False, **kw)
            self.assertEqual(abertas, {'coupa': ['292832'], 'vale': ['54328']})
            self.assertEqual([v[:2] for v in vencidas], [('coupa', '292770')])
            self.assertEqual(Path(tmp, 'eventos.jsonl').read_bytes(), antes)   # sem --gravar não mexe

            recuperar_perdidas.executar(*args, gravar=True, **kw)
            ficaram = [json.loads(l)['titulo'] for l in Path(tmp, 'eventos.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(ficaram, ['292770', '292800', '290000'])
            self.assertEqual(Path(tmp, 'eventos_nimbi.jsonl').read_text(encoding='utf-8').strip(), '')
            self.assertEqual(len(list(Path(tmp).glob('eventos.jsonl.antes-recuperacao-*'))), 1)
            self.assertIn('292770', Path(relatorio).read_text(encoding='utf-8-sig'))


class RoboForaDoGerenciadorTest(unittest.TestCase):
    def test_robo_sozinho_na_producao_nao_inicia(self):
        for caminho, classe in ((RAIZ / 'bots/coupa/mixins/autenticacao.py', 'AutenticacaoMixin'),
                                (RAIZ / 'bots/vale_bot.py', 'ValeScraper')):
            with self.subTest(caminho.name):
                metodo = carregar(caminho, {'iniciar_e_logar'}, classe)['iniciar_e_logar']
                emitidos = []
                bot = SimpleNamespace(sio=SimpleNamespace(emit=lambda e, d: emitidos.append(d)),
                                      verificar_acesso_pastas=lambda: self.fail('não devia preparar pastas'))
                with patch.dict(os.environ, {'URL_SERVIDOR': 'https://maestro.ventura.inf.br'}), \
                        patch.object(instancia_unica, '_ativa', None), patch.object(LOGGER, 'handle'):
                    os.environ.pop('MAESTRO_TESTE', None)
                    metodo(bot)
                self.assertFalse(emitidos[0]['sucesso'])
                self.assertIn('modo teste', emitidos[0]['erro'])


class JsonlNaoRessuscitaTest(unittest.TestCase):
    def test_evento_removido_pelo_gerenciador_nao_volta(self):
        ns = carregar(RAIZ / 'bots/coupa/mixins/banco_dados.py', {'carregar_banco_dados', 'atualizar_status_respondido_jsonl'},
                      'Banco_dadosMixin', json=json, os=os, tempfile=tempfile, datetime=datetime)
        with tempfile.TemporaryDirectory() as tmp:
            arq = Path(tmp, 'eventos.jsonl')
            linha = lambda n: json.dumps({'titulo': n, 'respondido': False}) + '\n'
            arq.write_text(linha('1') + linha('2'), encoding='utf-8')
            bot = SimpleNamespace(ARQUIVO_JSONL=str(arq), db_eventos={})
            bot.carregar_banco_dados = lambda: ns['carregar_banco_dados'](bot)
            with patch.object(LOGGER, 'handle'):
                bot.carregar_banco_dados()
                arq.write_text(linha('1'), encoding='utf-8')          # gerenciador removeu o 2
                ns['atualizar_status_respondido_jsonl'](bot, '1')
            gravado = [json.loads(l) for l in arq.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(gravado, [{'titulo': '1', 'respondido': True}])


if __name__ == '__main__':
    unittest.main()
