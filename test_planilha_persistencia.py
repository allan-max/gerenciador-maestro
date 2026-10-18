import ast
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

# Carrega a classe sem executar o singleton que acessa o compartilhamento real.
tree = ast.parse(Path(__file__).with_name('planilha_manager.py').read_text(encoding='utf-8'))
namespace = dict(json=json, logging=logging, os=os, tempfile=tempfile,
                 threading=threading, datetime=datetime, logger=logging.getLogger('teste-persistencia'))
definicoes = [n for n in tree.body if not (isinstance(n, ast.Assign) and any(
    getattr(t, 'id', None) in ('logger', 'planilha_manager') for t in n.targets))]
exec(compile(ast.Module(body=definicoes, type_ignores=[]), '<PlanilhaManager>', 'exec'), namespace)
Manager = namespace['PlanilhaManager']


class PersistenciaTest(unittest.TestCase):
    def setUp(self):
        self.logs = patch.object(namespace['logger'], 'handle')
        self.logs.start()
        self.addCleanup(self.logs.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cota = Path(self.tmp.name) / 'COTAÇÕES.json'
        self.pedido = Path(self.tmp.name) / 'PEDIDOS.json'
        self.cota.write_text(json.dumps({'COTAÇÃO': [
            {'COTAÇÃO': '123', 'VENCIMENTO': '01/01/2020', 'ITEM': 'Item original'}]}), encoding='utf-8')
        self.pedido.write_text(json.dumps({'PEDIDOS': [{'PEDIDO': '456'}]}), encoding='utf-8')
        self.manager = self.novo_manager()
        self.manager.iniciar()

    def novo_manager(self):
        m = Manager.__new__(Manager)
        m.lock = threading.RLock()
        m.iniciado = False
        m.cotacoes = {'COTAÇÃO': []}
        m.pedidos = {'PEDIDOS': []}
        m.caminho_cotacoes = str(self.cota)
        m.caminho_pedidos = str(self.pedido)
        m.map_cota = {1: 'COTAÇÃO', 2: 'VENCIMENTO', 3: 'ITEM', 9: 'RESPOSTA'}
        return m

    def test_salva_e_reabre_preservando_cotacoes_e_pedidos(self):
        self.assertTrue(self.manager.adicionar_linhas('COTAÇÃO', [['789', '20/10/2026', 'Novo item']]))
        novo = self.novo_manager()
        novo.iniciar()
        self.assertEqual(len(novo.cotacoes['COTAÇÃO']), 2)
        self.assertEqual(novo.pedidos, {'PEDIDOS': [{'PEDIDO': '456'}]})

    def test_resposta_da_tela_e_inclusao_do_bot_preservam_ambas_alteracoes(self):
        from resposta_cotacao import marcar_respondida
        identidade = {'COTAÇÃO': '123', 'VENCIMENTO': '01/01/2020', 'ITEM': 'Item original',
                      'VENDEDOR': 'GUSTAVO', 'LOCALIDADE': 'Vitória'}
        self.manager.cotacoes['COTAÇÃO'][0].update(identidade)
        self.assertTrue(self.manager.salvar())
        marcar_respondida(self.manager, identidade)
        self.assertTrue(self.manager.adicionar_linhas('COTAÇÃO', [['789', '20/10/2026', 'Novo item']]))
        banco = json.loads(self.cota.read_text(encoding='utf-8'))['COTAÇÃO']
        self.assertEqual(banco[0]['RESPOSTA'], 'RESPONDIDO')
        self.assertEqual(banco[1]['COTAÇÃO'], '789')

    def test_falha_antes_substituicao_preserva_arquivo_e_recupera_memoria(self):
        anterior = self.cota.read_bytes()
        self.manager.cotacoes['COTAÇÃO'].append({'COTAÇÃO': 'novo'})
        with patch('os.replace', side_effect=PermissionError('bloqueado')):
            self.assertFalse(self.manager.salvar())
        self.assertEqual(self.cota.read_bytes(), anterior)
        self.assertEqual(len(list(Path(self.tmp.name).iterdir())), 2)
        self.manager.iniciar()
        self.assertEqual(len(self.manager.cotacoes['COTAÇÃO']), 1)

    def test_json_corrompido_no_reinicio_bloqueia_inclusao_e_gravacao(self):
        self.cota.write_text('{"COTAÇÃO": [', encoding='utf-8')
        antes = self.cota.read_bytes()
        novo = self.novo_manager()
        with self.assertRaises(ValueError):
            novo.adicionar_linhas('COTAÇÃO', [['novo']])
        self.assertFalse(novo.salvar())
        self.assertEqual(self.cota.read_bytes(), antes)

    def test_alteracao_por_outro_servico_nao_e_sobrescrita(self):
        externo = json.dumps({'COTAÇÃO': [{'COTAÇÃO': 'externa'}]}).encode('utf-8')
        self.cota.write_bytes(externo)
        self.manager.cotacoes['COTAÇÃO'].append({'COTAÇÃO': 'local'})
        self.assertFalse(self.manager.salvar())
        self.assertEqual(self.cota.read_bytes(), externo)
        self.manager.iniciar()
        self.assertEqual(self.manager.cotacoes['COTAÇÃO'], [{'COTAÇÃO': 'externa'}])

    def test_leitura_falha_nao_usa_cache_antigo_para_gravar(self):
        self.cota.write_text('{quebrado', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.manager.iniciar()
        self.assertFalse(self.manager.salvar())
        self.assertEqual(self.cota.read_text(encoding='utf-8'), '{quebrado')

    def test_arquivo_ausente_ou_estrutura_errada_bloqueia_inicio(self):
        self.cota.unlink()
        with self.assertRaises(FileNotFoundError):
            self.novo_manager().iniciar()
        self.cota.write_text('{"COTAÇÃO": {}}', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.novo_manager().iniciar()

    def test_respostas_usam_gravacao_segura_e_preservam_pedidos(self):
        pedidos = self.pedido.read_bytes()
        with patch('os.replace', side_effect=PermissionError('bloqueado')):
            with self.assertRaises(PermissionError):
                self.manager.atualizar_status_respostas('COTAÇÃO', {'123': True}, 1, 9)
        self.assertNotIn('RESPOSTA', json.loads(self.cota.read_text(encoding='utf-8'))['COTAÇÃO'][0])
        self.assertEqual(self.pedido.read_bytes(), pedidos)
        self.assertTrue(self.manager.atualizar_status_respostas('COTAÇÃO', {'123': True}, 1, 9))
        self.assertEqual(json.loads(self.cota.read_text(encoding='utf-8'))['COTAÇÃO'][0]['RESPOSTA'], 'RESPONDIDO')

    def test_pedidos_legados_e_reducao_de_arquivo_continuam_carregando(self):
        self.pedido.write_text('"PEDIDOS": [{"PEDIDO": "legado",},]', encoding='utf-8')
        self.manager.iniciar()
        self.assertEqual(self.manager.pedidos['PEDIDOS'][0]['PEDIDO'], 'legado')
        self.cota.write_text('{"COTAÇÃO": []}', encoding='utf-8')
        os.utime(self.cota, (1, 1))
        self.manager.iniciar()
        self.assertEqual(self.manager.cotacoes['COTAÇÃO'], [])

    def test_encerrar_processo_antes_troca_preserva_json_no_reinicio(self):
        anterior = self.cota.read_bytes()
        script = '''
from test_planilha_persistencia import Manager
import os, sys, threading, time
from pathlib import Path
p = Path(sys.argv[1])
m = Manager.__new__(Manager)
m.lock = threading.RLock()
m.iniciado = False
m.cotacoes = {}; m.pedidos = {}
m.caminho_cotacoes = str(p / 'COTA\\u00c7\\u00d5ES.json')
m.caminho_pedidos = str(p / 'PEDIDOS.json')
m.iniciar()
m.cotacoes['COTA\\u00c7\\u00c3O'].append({'COTA\\u00c7\\u00c3O': 'novo'})
def antes_troca(*args):
    (p / 'pronto').write_text('aguardando troca')
    time.sleep(60)
os.replace = antes_troca
m.salvar()
'''
        proc = subprocess.Popen([sys.executable, '-c', script, self.tmp.name],
                                cwd=Path(__file__).parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            deadline = time.monotonic() + 10
            while not (Path(self.tmp.name) / 'pronto').exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue((Path(self.tmp.name) / 'pronto').exists(), 'Processo não chegou à troca')
            proc.terminate()
            proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        self.assertEqual(self.cota.read_bytes(), anterior)
        novo = self.novo_manager()
        novo.iniciar()
        self.assertEqual(len(novo.cotacoes['COTAÇÃO']), 1)

    def test_status_que_ja_esta_igual_nao_regrava_a_base(self):
        """A verificação do Coupa chama isto por cotação: regravar 5,6 MB a cada
        2 s sem mudança fazia o portal ler o arquivo no meio de uma troca."""
        self.assertTrue(self.manager.atualizar_status_respostas('COTAÇÃO', {'123': True}, 1, 9))
        antes = os.stat(self.cota).st_mtime_ns
        os.utime(self.cota, ns=(1, 1))
        self.manager.iniciar()
        with patch('os.replace', side_effect=AssertionError('não devia gravar')):
            self.assertFalse(self.manager.atualizar_status_respostas('COTAÇÃO', {'123': True}, 1, 9))
            self.assertFalse(self.manager.atualizar_coluna_por_chave('COTAÇÃO', {'123': 'Item original'}, 1, 3))
        self.assertNotEqual(antes, 0)

    def test_salvar_cotacao_nao_regrava_pedidos_alterados_por_outro_servico(self):
        externo = json.dumps({'PEDIDOS': [{'PEDIDO': '456'}, {'PEDIDO': 'do cadastro'}]}).encode('utf-8')
        self.pedido.write_bytes(externo)
        self.manager.cotacoes['COTAÇÃO'][0]['RESPOSTA'] = 'RESPONDIDO'
        self.assertTrue(self.manager.salvar())
        self.assertEqual(self.pedido.read_bytes(), externo)
        self.assertEqual(json.loads(self.cota.read_text(encoding='utf-8'))['COTAÇÃO'][0]['RESPOSTA'], 'RESPONDIDO')

    def test_leitura_tenta_de_novo_se_outro_processo_troca_o_arquivo(self):
        real = open
        trocou = []

        def abrir(caminho, *a, **k):
            f = real(caminho, *a, **k)
            if str(caminho) == str(self.cota) and not trocou:
                trocou.append(1)
                self.cota.write_text(json.dumps({'COTAÇÃO': [{'COTAÇÃO': 'novo'}]}), encoding='utf-8')
                os.utime(self.cota, ns=(5, 5))
            return f
        os.utime(self.cota, ns=(3, 3))
        namespace['open'] = abrir
        namespace['time'] = type('T', (), {'sleep': staticmethod(lambda s: None)})
        self.addCleanup(namespace.pop, 'open')
        self.manager.iniciar()
        self.assertEqual(self.manager.cotacoes['COTAÇÃO'], [{'COTAÇÃO': 'novo'}])


if __name__ == '__main__':
    unittest.main()
