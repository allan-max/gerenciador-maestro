"""Fase 5 (docs/melhorias-bots): log dos robôs com data e máquina, rotação e monitor que acompanha a rotação."""
import logging
import os
import re
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ambiente
import log_robos

RAIZ = Path(__file__).parent


class LogBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for alvo in (patch.object(ambiente, 'BASE_TESTE', self.tmp.name), patch.object(ambiente, '_modo', True)):
            alvo.start()
            self.addCleanup(alvo.stop)
        self.logger = logging.getLogger(f'teste-log-{self.id()}')
        self.logger.propagate = False
        self.logger.setLevel(logging.INFO)
        self.addCleanup(self.logger.handlers.clear)        # cleanups rodam do último para o primeiro: fecha antes
        self.addCleanup(lambda: [h.close() for h in self.logger.handlers])


class ConfigurarTest(LogBase):
    def test_linha_tem_data_maquina_e_nivel_e_fica_no_base_teste(self):
        with patch('sys.stdout'):
            caminho = log_robos.configurar(self.logger, 'log-coupa.txt')
            self.logger.info('evento 287782 extraído')
        self.assertEqual(Path(caminho), Path(self.tmp.name) / 'Logs' / 'log-coupa.txt')
        linha = Path(caminho).read_text(encoding='utf-8').strip()
        self.assertRegex(linha, r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[' + re.escape(socket.gethostname()) + r'\] \[INFO\] evento 287782')

    def test_chamar_de_novo_nao_duplica_handlers(self):
        with patch('sys.stdout'):
            log_robos.configurar(self.logger, 'log-vale.txt')
            log_robos.configurar(self.logger, 'log-vale.txt')
        tipos = sorted(type(h).__name__ for h in self.logger.handlers)
        self.assertEqual(tipos, ['RotatingFileHandler', 'StreamHandler'])

    def test_servidor_fora_do_ar_segue_so_no_console(self):
        with patch('sys.stdout'), patch.object(log_robos.os, 'makedirs', side_effect=OSError('rede')):
            self.assertIsNone(log_robos.configurar(self.logger, 'log-vale.txt'))
        self.assertEqual([type(h).__name__ for h in self.logger.handlers], ['StreamHandler'])


class MonitorTest(LogBase):
    def test_comeca_do_fim_ignora_linha_pela_metade_e_le_o_resto(self):
        arq = Path(self.tmp.name, 'log.txt')
        arq.write_bytes(b'antiga\n')
        linhas, pos = log_robos.novas_linhas(str(arq), None)
        self.assertEqual(linhas, [])                                  # começa do fim, como antes
        with open(arq, 'ab') as f:
            f.write('nova 1\nmeia linh'.encode('utf-8'))
        linhas, pos = log_robos.novas_linhas(str(arq), pos)
        self.assertEqual(linhas, ['nova 1'])
        with open(arq, 'ab') as f:
            f.write('a ção\n'.encode('utf-8'))
        self.assertEqual(log_robos.novas_linhas(str(arq), pos)[0], ['meia linha ção'])

    def test_rotacao_com_o_monitor_lendo_no_windows(self):
        with patch('sys.stdout'), patch.object(log_robos, 'TAMANHO_MAX', 400), patch.object(log_robos, 'ARQUIVOS_GUARDADOS', 2):
            caminho = log_robos.configurar(self.logger, 'log-coupa.txt')
            self.logger.info('primeira')
            _, pos = log_robos.novas_linhas(caminho, None)
            lidas = []
            with patch.object(logging.Handler, 'handleError', side_effect=AssertionError('rotação falhou')):
                for i in range(40):
                    self.logger.info(f'linha {i:02d} ' + 'x' * 40)
                    novas, pos = log_robos.novas_linhas(caminho, pos)
                    lidas += novas
        self.assertTrue(Path(caminho + '.1').exists())                # rodou mesmo com o monitor lendo
        self.assertTrue(any('linha 39' in l for l in lidas))         # e o monitor continuou depois da rotação

    def test_arquivo_que_aparece_depois_e_lido_do_comeco_em_partes(self):
        arq = Path(self.tmp.name, 'novo.txt')
        linhas, pos = log_robos.novas_linhas(str(arq), None)
        self.assertEqual((linhas, pos), ([], 0))                      # não existia: quando aparecer, do começo
        arq.write_text(''.join(f'linha {i}\n' for i in range(30)), encoding='utf-8')
        with patch.object(log_robos, 'LEITURA_MAX', 50):
            lidas = []
            for _ in range(20):
                novas, pos = log_robos.novas_linhas(str(arq), pos)
                lidas += novas
        self.assertEqual(lidas, [f'linha {i}' for i in range(30)])

    def test_acompanhar_repassa_as_linhas(self):
        arq = Path(self.tmp.name, 'log.txt')
        arq.write_text('', encoding='utf-8')
        enviadas, voltas = [], iter(range(4))

        def continuar():
            volta = next(voltas, None)
            if volta == 1:
                with open(arq, 'a', encoding='utf-8') as f:
                    f.write('olá painel\n')
            return volta is not None
        log_robos.acompanhar(str(arq), enviadas.append, continuar, intervalo=0)
        self.assertEqual(enviadas, ['olá painel'])


class LigacaoTest(unittest.TestCase):
    def test_robos_e_monitor_usam_o_log_robos(self):
        self.assertIn("log_robos.configurar(logger, 'log-vale.txt')", (RAIZ / 'bots/vale_bot.py').read_text(encoding='utf-8'))
        self.assertIn("log_robos.configurar(logger, 'log-coupa.txt')", (RAIZ / 'bots/coupa/mixins/utilitarios.py').read_text(encoding='utf-8'))
        ger = (RAIZ / 'gerenciador.py').read_text(encoding='utf-8')
        self.assertEqual(ger.count('log_robos.acompanhar('), 2)
        for nome in ('log-coupa.txt"', 'log-vale.txt"'):
            self.assertNotIn(nome, ger)     # o caminho fixo antigo saiu; vem do log_robos.caminho_log
        for arquivo in ('bots/vale_bot.py', 'bots/coupa/mixins/verificacao.py'):
            self.assertIn("'verificacao_concluida'", (RAIZ / arquivo).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
