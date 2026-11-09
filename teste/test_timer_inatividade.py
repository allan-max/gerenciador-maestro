# -*- coding: utf-8 -*-
import threading
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
from test_fase0 import LOGGER, carregar

RAIZ = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == 'teste' else Path(__file__).resolve().parent
GER = RAIZ / 'gerenciador.py'
FUNCOES = {
    'cancelar_timer_inatividade_resposta',
    'agendar_timer_inatividade_resposta',
    '_timer_inatividade_resposta',
    '_trava_timer_inatividade',
    'TEMPO_ESPERA_INATIVIDADE_RESPOSTA',
    '_robo_trabalhando',
    'sair_modo_resposta',
    'modo_resposta_ativo',
    'modo_resposta_pronto',
    '_trava_modo_resposta',
}


class TestTimerInatividade(unittest.TestCase):

    def setUp(self):
        self.mock_sio = MagicMock()
        self.ns = carregar(
            GER,
            FUNCOES,
            sio=self.mock_sio,
            bot_coupa=None,
            bot_vale=None,
            portal_atual='coupa',
            threading=threading
        )

    def tearDown(self):
        self.ns['cancelar_timer_inatividade_resposta']()

    def test_agendar_inicia_timer_600s(self):
        with patch('threading.Timer') as mock_timer_cls:
            mock_inst = MagicMock()
            mock_timer_cls.return_value = mock_inst

            self.ns['agendar_timer_inatividade_resposta']('coupa')

            mock_timer_cls.assert_called_once()
            args, _ = mock_timer_cls.call_args
            self.assertEqual(args[0], 600)  # 10 minutos = 600 segundos
            mock_inst.start.assert_called_once()

    def test_novo_comando_cancela_timer(self):
        with patch('threading.Timer') as mock_timer_cls:
            mock_inst = MagicMock()
            mock_timer_cls.return_value = mock_inst

            self.ns['agendar_timer_inatividade_resposta']('coupa')
            self.assertIsNotNone(self.ns['_timer_inatividade_resposta'])

            # Novo comando chega
            self.ns['cancelar_timer_inatividade_resposta']()
            mock_inst.cancel.assert_called_once()
            self.assertIsNone(self.ns['_timer_inatividade_resposta'])

    def test_callback_fecha_navegador_quando_disparado(self):
        # Simula o disparo do callback de fechar navegador
        mock_driver = MagicMock()
        mock_bot = MagicMock()
        mock_bot.driver = mock_driver
        mock_bot.thread_atual = None
        mock_bot.thread_login = None
        self.ns['bot_coupa'] = mock_bot
        self.ns['modo_resposta_ativo'] = self.ns['modo_resposta_pronto'] = True

        with patch('threading.Timer') as mock_timer_cls:
            callback_fechar = None
            def capturar_timer(tempo, fn):
                nonlocal callback_fechar
                callback_fechar = fn
                return MagicMock()

            mock_timer_cls.side_effect = capturar_timer

            self.ns['agendar_timer_inatividade_resposta']('coupa')
            self.assertIsNotNone(callback_fechar)

            # Executa o callback como se tivessem passado os 10 minutos
            callback_fechar()

            mock_driver.quit.assert_called_once()
        self.assertIsNone(self.ns['bot_coupa'])
        # Modo resposta acaba: o piloto do Coupa/Vale volta
        self.assertFalse(self.ns['modo_resposta_ativo'])
        self.assertFalse(self.ns['modo_resposta_pronto'])

    def test_callback_adia_se_o_robo_ainda_esta_respondendo(self):
        tarefa = MagicMock()
        tarefa.is_alive.return_value = True
        mock_bot = MagicMock(thread_atual=tarefa, thread_login=None)
        self.ns['bot_vale'] = mock_bot
        self.ns['modo_resposta_ativo'] = self.ns['modo_resposta_pronto'] = True
        callbacks = []
        with patch('threading.Timer') as mock_timer_cls:
            mock_timer_cls.side_effect = lambda tempo, fn: callbacks.append(fn) or MagicMock()
            self.ns['agendar_timer_inatividade_resposta']('vale')
            callbacks[0]()
        mock_bot.driver.quit.assert_not_called()
        self.assertEqual(len(callbacks), 2)              # reagendou mais 10 minutos
        self.assertTrue(self.ns['modo_resposta_ativo'])


if __name__ == '__main__':
    unittest.main()
