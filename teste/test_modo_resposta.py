"""Modo resposta: responder cotação pelo Minhas Cotações tem prioridade total sobre o Coupa/Vale.
O robô que estiver extraindo/verificando para com segurança (termina o evento atual e salva), desliga, e só então
o robô de resposta liga. Quando o modo acaba (10 min depois da última resposta) o piloto refaz o ciclo cortado."""
import threading
import time as time_real
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from test_fase0 import LOGGER, carregar
from test_sincronizacao import gerenciador, retornos

RAIZ = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == 'teste' else Path(__file__).resolve().parent
GER = RAIZ / 'gerenciador.py'
MODO = {'entrar_modo_resposta', 'sair_modo_resposta', '_parar_e_desligar_para_responder', 'desligar_coupa_vale',
        '_robo_trabalhando', 'modo_resposta_ativo', 'modo_resposta_pronto', '_trava_modo_resposta', 'ESPERA_SALVAR_S'}


class RoboFalso:
    def __init__(self, ns, portal, trabalho_s=0.0):
        self.solicitacao_parada = False
        self.driver = MagicMock()
        self.thread_login = None
        self.salvou = False

        def extrair():   # como o varrer_painel_eventos: só sai do laço quando vê a parada, e grava antes de sair
            while not self.solicitacao_parada:
                time_real.sleep(0.01)
            time_real.sleep(trabalho_s)
            self.salvou = True
        self.thread_atual = threading.Thread(target=extrair, daemon=True)
        self.thread_atual.start()


class EntrarModoRespostaTest(unittest.TestCase):
    def ns(self):
        return carregar(GER, MODO, sio=MagicMock(), threading=threading, time=SimpleNamespace(sleep=lambda s: None),
                        bot_coupa=None, bot_vale=None, rodando_monitor_coupa=True, rodando_monitor_vale=True,
                        _trava_ciclo=threading.Lock())

    def test_para_salva_e_desliga_os_dois_antes_de_liberar_a_resposta(self):
        ns = self.ns()
        coupa, vale = RoboFalso(ns, 'coupa', 0.05), RoboFalso(ns, 'vale')
        ns['bot_coupa'], ns['bot_vale'] = coupa, vale
        with patch.object(LOGGER, 'handle'):
            ns['entrar_modo_resposta']()
        self.assertTrue(coupa.salvou and vale.salvou)      # terminou o evento atual e gravou antes de fechar
        coupa.driver.quit.assert_called_once()
        vale.driver.quit.assert_called_once()
        self.assertIsNone(ns['bot_coupa'])
        self.assertIsNone(ns['bot_vale'])
        self.assertTrue(ns['modo_resposta_ativo'])
        self.assertTrue(ns['modo_resposta_pronto'])

    def test_espera_o_ciclo_que_estava_rodando_sair(self):
        ns = self.ns()
        ns['_trava_ciclo'].acquire()   # ciclo do piloto ainda no finally
        threading.Timer(0.2, ns['_trava_ciclo'].release).start()
        inicio = time_real.monotonic()
        with patch.object(LOGGER, 'handle'):
            ns['entrar_modo_resposta']()
        self.assertGreaterEqual(time_real.monotonic() - inicio, 0.15)
        self.assertTrue(ns['modo_resposta_pronto'])

    def test_segunda_resposta_nao_para_nada_de_novo(self):
        ns = self.ns()
        with patch.object(LOGGER, 'handle'):
            ns['entrar_modo_resposta']()
            resposta = MagicMock()        # robô de resposta já ligado
            ns['bot_coupa'] = resposta
            ns['entrar_modo_resposta']()
        resposta.driver.quit.assert_not_called()
        self.assertIs(ns['bot_coupa'], resposta)

    def test_sair_libera_o_piloto(self):
        ns = self.ns()
        with patch.object(LOGGER, 'handle'):
            ns['entrar_modo_resposta']()
            ns['sair_modo_resposta']()
        self.assertFalse(ns['modo_resposta_ativo'])
        self.assertFalse(ns['modo_resposta_pronto'])


class CicloInterrompidoTest(unittest.TestCase):
    def test_extracao_cortada_nao_e_erro_nao_desliga_o_robo_de_resposta_e_o_piloto_refaz(self):
        def tarefa(portal, modo):
            ns['modo_resposta_ativo'] = True   # resposta chegou durante a extração
        ns = gerenciador(executar_tarefa=MagicMock(side_effect=tarefa))
        with patch.object(LOGGER, 'handle'):
            ok, msg = ns['rodar_ciclo']('coupa')
        self.assertFalse(ok)
        self.assertIn('interrompido', msg)
        self.assertEqual([c.args for c in ns['executar_tarefa'].call_args_list], [('coupa', 'extrair')])
        ns['enviar_email_erro'].assert_not_called()
        ns['extracao_ok'].assert_not_called()
        self.assertNotIn('desligar_robo', [c.args[0] for c in ns['comandar'].call_args_list])
        self.assertEqual(ns['ciclo_interrompido'], 'coupa')
        self.assertIsNone(ns['ciclo_em_andamento'])
        self.assertEqual(ns['eventos'].registrar.call_args.args[2], 'aviso')

    def test_verificacao_da_vale_cortada_roda_de_novo_no_mesmo_dia(self):
        def tarefa(portal, modo):
            if modo == 'verificar':
                ns['modo_resposta_ativo'] = True
                raise Exception('robô desligado')
        ns = gerenciador(hora=10, executar_tarefa=MagicMock(side_effect=tarefa))
        with patch.object(LOGGER, 'handle'):
            ok, _ = ns['rodar_ciclo']('vale')
        self.assertFalse(ok)
        self.assertIsNone(ns['ultimo_dia_verificacao_vale'])

    def test_manual_cortado_nao_marca_para_o_piloto(self):
        def tarefa(portal, modo):
            ns['modo_resposta_ativo'] = True
        ns = gerenciador(executar_tarefa=MagicMock(side_effect=tarefa))
        with patch.object(LOGGER, 'handle'):
            ok, _ = ns['rodar_ciclo']('coupa', manual=True)
        self.assertFalse(ok)
        self.assertIsNone(ns['ciclo_interrompido'])

    def test_nao_comeca_ciclo_durante_o_modo_resposta(self):
        ns = gerenciador()
        ns['modo_resposta_ativo'] = True
        ok, _ = ns['rodar_ciclo']('vale')
        self.assertFalse(ok)
        ns['comandar'].assert_not_called()

    def test_ciclo_manual_recusado_durante_o_modo_resposta(self):
        ns = gerenciador()
        ns['modo_resposta_ativo'] = True
        ns['comando_sincronizacao']({'servico': 'ciclo_coupa'})
        r = retornos(ns)[0]
        self.assertFalse(r['sucesso'])
        self.assertIn('Minhas Cotações', r['erro'])
        self.assertFalse(ns['_trava_ciclo'].locked())


class PilotoRefazCicloTest(unittest.TestCase):
    def test_piloto_espera_o_modo_resposta_e_refaz_o_ciclo_cortado(self):
        pausou, nunca = threading.Event(), threading.Event()
        dormidas = []

        def dormir(s):
            dormidas.append(s)
            if s == 5 and dormidas.count(5) == 2:
                ns['modo_resposta_ativo'] = False   # 10 min depois da última resposta
            if s == 90 * 60:
                pausou.set()
                nunca.wait()

        ns = gerenciador(time=SimpleNamespace(sleep=dormir))
        chamadas = []

        def ciclo(portal):
            chamadas.append(portal)
            if len(chamadas) == 1:   # primeira tentativa cortada por uma resposta
                ns['modo_resposta_ativo'] = True
                ns['ciclo_interrompido'] = portal
            return True, 'ok'
        ns['rodar_ciclo'] = ciclo
        with patch.object(LOGGER, 'handle'):
            threading.Thread(target=ns['loop_automacao'], daemon=True).start()
            self.assertTrue(pausou.wait(5))
        self.assertEqual(chamadas, ['coupa', 'coupa'])
        self.assertIsNone(ns['ciclo_interrompido'])
        self.assertEqual(dormidas.count(5), 2)   # esperou o modo resposta acabar antes de refazer


if __name__ == '__main__':
    unittest.main()
