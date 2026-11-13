"""Reinício suave (reinicio.py): o atualizador só pede; o gerenciador troca a si mesmo quando nada está em andamento.
Robô externo ligado 24/7 (ME, Ariba, Findes) não segura o reinício nem é fechado por ele."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import reinicio
from test_fase0 import LOGGER, carregar

RAIZ = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == 'teste' else Path(__file__).resolve().parent
GER = RAIZ / 'gerenciador.py'


class Pasta(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.raiz = tmp.name
        self.passos = []

    def tentar(self, versao='v1', motivos=()):
        return reinicio.tentar(self.raiz, versao, lambda: list(motivos),
                               lambda: self.passos.append('fechar'), lambda: self.passos.append('relancar'))


class PedidoTest(Pasta):
    def test_sem_pedido_nao_reinicia(self):
        self.assertFalse(self.tentar())
        self.assertEqual(self.passos, [])

    def test_pedido_com_algo_em_andamento_espera_e_continua_valendo(self):
        reinicio.pedir(self.raiz, 'v2')
        self.assertFalse(self.tentar(motivos=['coupa']))
        self.assertEqual(self.passos, [])
        self.assertEqual(reinicio.ler_pedido(self.raiz)['versao'], 'v2')

    def test_livre_fecha_antes_de_abrir_o_novo_e_consome_o_pedido(self):
        reinicio.pedir(self.raiz, 'v2')
        self.assertTrue(self.tentar())
        self.assertEqual(self.passos, ['fechar', 'relancar'])
        self.assertIsNone(reinicio.ler_pedido(self.raiz))

    def test_algo_que_comeca_na_hora_de_fechar_adia_a_troca(self):
        reinicio.pedir(self.raiz, 'v2')
        trocou = reinicio.tentar(self.raiz, 'v1', lambda: [], lambda: False, lambda: self.passos.append('relancar'))
        self.assertFalse(trocou)
        self.assertEqual(self.passos, [])
        self.assertEqual(reinicio.ler_pedido(self.raiz)['versao'], 'v2')

    def test_pedido_para_a_versao_que_ja_esta_rodando_e_descartado(self):
        reinicio.pedir(self.raiz, 'v1')
        self.assertFalse(self.tentar(versao='v1'))
        self.assertEqual(self.passos, [])
        self.assertIsNone(reinicio.ler_pedido(self.raiz))

    def test_pedido_ilegivel_nao_reinicia(self):
        Path(self.raiz, reinicio.ARQ_PEDIDO).write_text('{pela metade', encoding='utf-8')
        self.assertFalse(self.tentar())


class ContadorTest(unittest.TestCase):
    def test_comando_conta_so_enquanto_roda_mesmo_quando_da_erro(self):
        contador = reinicio.Contador()
        visto = []

        @contador.contar('salvar')
        def salvar(falhar):
            visto.append(contador.ativos())
            if falhar:
                raise ValueError('x')
            return 'ok'

        self.assertEqual(salvar(False), 'ok')
        with self.assertRaises(ValueError):
            salvar(True)
        self.assertEqual(visto, [['salvar'], ['salvar']])
        self.assertEqual(contador.ativos(), [])


class TrocaTest(Pasta):
    def test_o_novo_processo_le_uma_vez_o_que_o_antigo_deixou(self):
        reinicio.salvar_troca(self.raiz, {'ultimo_ciclo': {'portal': 'coupa', 'fim': 100}})
        self.assertEqual(reinicio.ler_troca(self.raiz)['ultimo_ciclo']['portal'], 'coupa')
        self.assertEqual(reinicio.ler_troca(self.raiz), {})

    def test_troca_velha_de_outro_dia_e_ignorada(self):
        reinicio.salvar_troca(self.raiz, {'ultimo_ciclo': {'portal': 'coupa', 'fim': 100}})
        self.assertEqual(reinicio.ler_troca(self.raiz, agora=time.time() + reinicio.VALIDADE_TROCA_S + 1), {})

    def test_piloto_retoma_a_pausa_de_onde_parou(self):
        troca = {'ultimo_ciclo': {'portal': 'coupa', 'fim': 1000}}
        self.assertEqual(reinicio.retomada_piloto(troca, agora=1000 + 30 * 60, pausa_s=90 * 60),
                         {'portal': 'coupa', 'falta_s': 60 * 60})
        self.assertEqual(reinicio.retomada_piloto(troca, agora=1000 + 5 * 3600, pausa_s=90 * 60)['falta_s'], 0)
        self.assertIsNone(reinicio.retomada_piloto({}, agora=1000, pausa_s=90 * 60))

    def test_log_do_robo_continua_de_onde_o_antigo_parou(self):
        self.assertEqual(reinicio.posicao_log(tamanho=500, salvo=420), 420)
        self.assertEqual(reinicio.posicao_log(tamanho=500, salvo=None), 500)    # sem troca: só as linhas novas
        self.assertEqual(reinicio.posicao_log(tamanho=80, salvo=420), 80)       # log trocado/rotacionado no meio


class RelancarTest(unittest.TestCase):
    def test_abre_o_mesmo_python_com_os_mesmos_argumentos_na_mesma_pasta(self):
        abertos = []
        reinicio.relancar(popen=lambda cmd, **k: abertos.append((cmd, k)))
        cmd, opcoes = abertos[0]
        self.assertEqual(cmd, [sys.executable, *sys.argv])
        self.assertEqual(opcoes['cwd'], os.getcwd())
        self.assertNotIn('creationflags', opcoes)   # herda a janela (ou a falta dela) de quem abriu o gerenciador


class OcupadosTest(unittest.TestCase):
    def ocupados(self, **globais):
        return carregar(GER, {'robos_ocupados'}, time=time, **globais)['robos_ocupados']()

    def test_robo_externo_ligado_nao_segura_o_reinicio(self):
        aberto = SimpleNamespace(poll=lambda: None)
        self.assertEqual(self.ocupados(processo_me=aberto, bot_ariba=aberto, bot_findes=aberto, bot_comprasgov=aberto), [])

    def test_tarefa_do_coupa_ciclo_respostas_e_backup_seguram(self):
        parar = threading.Event()
        th = threading.Thread(target=parar.wait, daemon=True)
        th.start()
        self.addCleanup(parar.set)
        trava, livre = threading.Lock(), threading.Lock()
        trava.acquire()
        self.assertEqual(self.ocupados(bot_coupa=SimpleNamespace(thread_atual=th), bot_vale=SimpleNamespace(thread_atual=None),
                                       _trava_ciclo=trava, _trava_backup=livre, modo_resposta_ativo=True),
                         ['coupa', 'ciclo', 'respostas'])
        self.assertEqual(self.ocupados(_trava_ciclo=livre, _trava_backup=trava), ['backup'])

    def test_login_do_robo_em_andamento_segura(self):
        parar = threading.Event()
        th = threading.Thread(target=parar.wait, daemon=True)
        th.start()
        self.addCleanup(parar.set)
        self.assertEqual(self.ocupados(bot_vale=SimpleNamespace(thread_atual=None, thread_login=th)), ['vale'])

    def test_robo_recem_ligado_segura_por_um_minuto(self):
        inicio = {'ariba': time.time() - 5, 'me': time.time() - 600}
        self.assertEqual(self.ocupados(_ultimo_inicio=inicio, _GRACA_INICIO_S=60), ['ariba iniciando'])

    def test_comando_de_usuario_em_andamento_e_findes_recebendo_eventos_seguram(self):
        contador = reinicio.Contador()
        visto = []
        contador.contar('comando_imprimir')(lambda: visto.extend(
            self.ocupados(reinicio=SimpleNamespace(comandos=contador), findes_recebendo_eventos=True)))()
        self.assertEqual(visto, ['findes recebendo eventos', 'comando_imprimir'])

    def test_estado_avisa_o_atualizador_que_este_gerenciador_reinicia_sozinho(self):
        with tempfile.TemporaryDirectory() as tmp:
            arq = os.path.join(tmp, 'gerenciador.estado')
            ns = carregar(GER, {'robos_ocupados', 'gravar_estado'}, os=os, json=json, time=time, ARQ_ESTADO=arq, VERSAO='abc')
            ns['gravar_estado']()
            estado = json.loads(Path(arq).read_text(encoding='utf-8'))
        self.assertEqual((estado['suave'], estado['ocupados'], estado['pid']), (True, [], os.getpid()))


class PilotoTest(unittest.TestCase):
    def test_reinicio_no_meio_da_pausa_nao_dispara_ciclo_extra(self):
        dormidas, ciclos, pronto, nunca = [], [], threading.Event(), threading.Event()

        def dormir(s):
            dormidas.append(s)
            if s == 90 * 60:
                pronto.set()
                nunca.wait()

        ns = carregar(GER, {'loop_automacao', '_trava_ciclo', 'ciclo_interrompido', 'modo_resposta_ativo',
                            'ultimo_ciclo_piloto', 'PAUSA_PILOTO_S'},
                      time=SimpleNamespace(sleep=dormir, time=time.time), threading=threading,
                      sio=SimpleNamespace(emit=lambda *a, **k: None))
        ns['rodar_ciclo'] = lambda portal: ciclos.append(portal)
        self.addCleanup(nunca.set)
        from unittest.mock import patch
        with patch.object(LOGGER, 'handle'):
            threading.Thread(target=ns['loop_automacao'], args=({'portal': 'coupa', 'falta_s': 600},), daemon=True).start()
            self.assertTrue(pronto.wait(5))
        self.assertEqual(ciclos, ['vale'])            # o Coupa já tinha rodado antes do reinício
        self.assertEqual(dormidas[:2], [10, 600])     # só o que faltava da pausa de 1h30


if __name__ == '__main__':
    unittest.main()
