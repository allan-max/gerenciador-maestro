import ast
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import eventos
from resposta_cotacao import CAMPOS, marcar_respondida
from test_planilha_persistencia import Manager, namespace

CONTRATO = {'em', 'servico', 'tipo', 'nivel', 'resumo', 'ref', 'detalhe'}


class EventosTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.arq = Path(self.tmp.name) / 'logs' / 'eventos.jsonl'
        anterior = eventos.ARQUIVO
        eventos.ativar(str(self.arq))
        self.addCleanup(eventos.ativar, anterior)

    def linhas(self):
        return [json.loads(l) for l in self.arq.read_text(encoding='utf-8').splitlines()]

    def escrever(self, *dias_atras):
        agora = datetime.now().astimezone()
        self.arq.parent.mkdir(parents=True, exist_ok=True)
        with open(self.arq, 'w', encoding='utf-8') as f:
            for d in dias_atras:
                f.write(json.dumps({'em': (agora - timedelta(days=d)).isoformat(timespec='seconds'),
                                    'servico': 'coupa', 'tipo': 't', 'nivel': 'ok', 'resumo': str(d),
                                    'ref': None, 'detalhe': {}}) + '\n')

    def test_grava_uma_linha_no_contrato(self):
        eventos.registrar('vale', 'cotacao_nova', 'ok', 'x' * 300, ref=123, detalhe={'cotacoes': ['1']})
        eventos.registrar('outro', 'tipo', 'urgente', 'normaliza')
        a, b = self.linhas()
        self.assertEqual(set(a), CONTRATO)
        self.assertEqual((a['servico'], a['nivel'], a['ref']), ('vale', 'ok', '123'))
        self.assertEqual(len(a['resumo']), 140)
        self.assertTrue(a['resumo'].endswith('…'))
        self.assertIsNotNone(datetime.fromisoformat(a['em']).tzinfo)
        self.assertEqual((b['servico'], b['nivel']), ('gerenciador', 'info'))

    def test_desligado_sem_ativar_nao_grava_nada(self):
        eventos.ativar(None)
        eventos.registrar('vale', 'x', 'ok', 'não deve gravar')
        eventos.limpar()
        self.assertFalse(self.arq.exists())
        self.assertEqual(eventos.ultimos(), {'eventos': [], 'total': 0, 'erros': {}})

    def test_falha_ao_gravar_nao_derruba_e_vira_aviso(self):
        self.arq.mkdir(parents=True)          # um diretório no lugar do arquivo: open() falha
        with self.assertLogs('eventos', level='WARNING') as log:
            eventos.registrar('coupa', 'ciclo', 'ok', 'não grava')
        self.assertIn('Evento não gravado', log.output[0])

    def test_limpeza_tira_mais_de_7_dias_e_linha_cortada(self):
        self.escrever(0, 3, 8, 30)
        with open(self.arq, 'a', encoding='utf-8') as f:
            f.write('{"em": "2026-10-1')        # desligamento no meio da gravação
        eventos.limpar()
        self.assertEqual([e['resumo'] for e in self.linhas()], ['0', '3'])
        self.assertEqual([p.name for p in self.arq.parent.iterdir()], ['eventos.jsonl'])   # sem temporário

    def test_ultimos_mais_novos_primeiro_com_limite_e_desde(self):
        self.escrever(5, 1, 3, 9)
        r = eventos.ultimos()
        self.assertEqual([e['resumo'] for e in r['eventos']], ['1', '3', '5'])
        self.assertEqual(r['total'], 3)
        self.assertEqual(len(eventos.ultimos(limite=1)['eventos']), 1)
        self.assertEqual(len(eventos.ultimos(limite='lixo')['eventos']), 3)
        desde = (datetime.now().astimezone() - timedelta(days=2)).isoformat()
        self.assertEqual([e['resumo'] for e in eventos.ultimos(desde=desde)['eventos']], ['1'])
        self.escrever(*([0] * 600))
        self.assertEqual(len(eventos.ultimos(limite=9999)['eventos']), eventos.MAX_EVENTOS)

    def test_ler_todos_sem_corte_na_ordem_gravada(self):
        self.escrever(*([0] * 600), 1)
        todos = eventos.ler_todos()
        self.assertEqual(len(todos), 601)
        self.assertEqual(todos[-1]['resumo'], '1')
        eventos.ativar(None)
        self.assertEqual(eventos.ler_todos(), [])

    def test_status_de_resposta_so_gera_evento_quando_muda(self):
        cota = Path(self.tmp.name) / 'COTAÇÕES.json'
        pedido = Path(self.tmp.name) / 'PEDIDOS.json'
        cota.write_text(json.dumps({'COTAÇÃO': [{'COTAÇÃO': '123', 'VENCIMENTO': '01/01/2020'}]}), encoding='utf-8')
        pedido.write_text(json.dumps({'PEDIDOS': []}), encoding='utf-8')
        m = Manager.__new__(Manager)
        m.lock, m.iniciado = threading.RLock(), False
        m.cotacoes, m.pedidos = {'COTAÇÃO': []}, {'PEDIDOS': []}
        m.caminho_cotacoes, m.caminho_pedidos = str(cota), str(pedido)
        m.map_cota = {1: 'COTAÇÃO', 2: 'VENCIMENTO', 9: 'RESPOSTA'}
        with patch.object(namespace['logger'], 'handle'):
            self.assertTrue(m.atualizar_status_respostas('COTAÇÃO', {'123': True}, 1, 9))
            self.assertFalse(m.atualizar_status_respostas('COTAÇÃO', {'123': True}, 1, 9))   # já igual
        (e,) = self.linhas()
        self.assertEqual((e['tipo'], e['ref'], e['detalhe']), ('resposta', '123', {'mudancas': ['123: RESPONDIDO']}))

    def test_marcar_respondida_gera_evento(self):
        caminho = Path(self.tmp.name) / 'COTAÇÕES.json'
        linha = dict(zip(CAMPOS, ('456', 'GUSTAVO', 'Vitória', 'Câmera', '20/10/2026')))
        caminho.write_text(json.dumps({'COTAÇÃO': [linha]}), encoding='utf-8')
        marcar_respondida(SimpleNamespace(caminho_cotacoes=str(caminho), lock=threading.RLock()), linha)
        (e,) = self.linhas()
        self.assertEqual((e['servico'], e['nivel'], e['ref']), ('gerenciador', 'ok', '456'))


class LigacaoNoGerenciadorTest(unittest.TestCase):
    """Confere o gerenciador.py pela árvore, sem executá-lo (ele conecta na rede ao importar)."""

    @classmethod
    def setUpClass(cls):
        cls.fonte = Path(__file__).with_name('gerenciador.py').read_text(encoding='utf-8')
        cls.arvore = ast.parse(cls.fonte)

    def test_comando_eventos_responde_com_client_id(self):
        func = next(n for n in self.arvore.body if isinstance(n, ast.FunctionDef) and n.name == 'comando_eventos')
        self.assertIn("sio.on('comando_eventos')", ast.unparse(func.decorator_list[0]))
        emitidos = [ast.unparse(n) for n in ast.walk(func) if isinstance(n, ast.Call) and ast.unparse(n.func) == 'sio.emit']
        self.assertEqual(len(emitidos), 2)
        self.assertTrue(all("'retorno_eventos'" in e and "'clientId'" in e for e in emitidos))
        sio = SimpleNamespace(emitidos=[])
        ns = {'eventos': SimpleNamespace(ultimos=lambda *a: 1 / 0),
              'sio': SimpleNamespace(emit=lambda *a: sio.emitidos.append(a), on=lambda *_: (lambda f: f))}
        exec(compile(ast.Module(body=[func], type_ignores=[]), '<comando_eventos>', 'exec'), ns)
        ns['comando_eventos']({'clientId': 'c1'})
        self.assertEqual(sio.emitidos, [('retorno_eventos', {'sucesso': False, 'erro': 'division by zero', 'clientId': 'c1'})])
        ns['eventos'] = SimpleNamespace(ultimos=lambda limite, desde: {'eventos': [], 'limite': limite, 'desde': desde})
        ns['comando_eventos'](None)
        self.assertEqual(sio.emitidos[-1][1]['resultado'], {'eventos': [], 'limite': 200, 'desde': None})

    def test_ativa_antes_da_trava_e_limpa_na_partida_e_todo_dia(self):
        ativar = self.fonte.index('eventos.ativar()')
        trava = self.fonte.index('instancia_unica(CONFIG_GLOBAL')
        recusa = self.fonte.index("'instancia', 'aviso'")
        limpar = self.fonte.index('eventos.limpar()')
        self.assertLess(ativar, trava)
        self.assertLess(trava, recusa)
        self.assertLess(recusa, limpar)
        self.assertIn('schedule.every().day.at("03:00").do(eventos.limpar)', self.fonte)

    def test_ciclos_do_auto_pilot_e_robos_registram(self):
        # um rodar_ciclo(portal) para os dois; o comportamento está em test_sincronizacao.py
        for nivel in ('info', 'ok', 'erro'):
            self.assertIn(f"eventos.registrar(portal, 'ciclo', '{nivel}'", self.fonte)
        self.assertIn("for portal in ('coupa', 'vale'):", self.fonte)
        raiz = Path(__file__).parent
        for arq, servico in (('bots/vale_bot.py', 'vale'), ('bots/coupa/mixins/planilhas.py', 'coupa')):
            texto = (raiz / arq).read_text(encoding='utf-8')
            self.assertLess(texto.index('planilha_manager.adicionar_linhas(self.NOME_ABA_ALVO'),
                            texto.index(f"registrar('{servico}', 'cotacao_nova'"))


if __name__ == '__main__':
    unittest.main()
