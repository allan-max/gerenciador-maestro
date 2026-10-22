"""Trava do PEDIDOS.json combinada com o cotafrete: outro processo segurando a trava impede a troca do arquivo."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from trava_arquivo import TravaOcupada, trava_arquivo
from test_planilha_persistencia import Manager, namespace as ns_manager

RAIZ = Path(__file__).parent


class OutroProcesso:
    """Segura a trava num processo à parte (como o serviço de NF do cotafrete) até ser encerrado."""
    def __init__(self, caminho):
        codigo = ('import sys, time; sys.path.insert(0, sys.argv[1]); from trava_arquivo import trava_arquivo\n'
                  'with trava_arquivo(sys.argv[2]):\n    print("ok", flush=True); time.sleep(60)')
        self.proc = subprocess.Popen([sys.executable, '-c', codigo, str(RAIZ), str(caminho)],
                                     stdout=subprocess.PIPE, text=True)
        assert self.proc.stdout.readline().strip() == 'ok'

    def encerrar(self):
        self.proc.kill()
        self.proc.wait(10)
        self.proc.stdout.close()


class TravaArquivoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.arq = Path(self.tmp.name) / 'PEDIDOS.json'

    def test_formato_combinado_com_o_cotafrete(self):
        with trava_arquivo(self.arq):
            self.assertTrue(Path(str(self.arq) + '.lock').exists())
        self.assertTrue(Path(str(self.arq) + '.lock').exists())   # nunca é apagado

    def test_espera_e_desiste_se_outro_processo_nao_solta(self):
        outro = OutroProcesso(self.arq)
        try:
            inicio = time.time()
            with self.assertRaises(TravaOcupada):
                with trava_arquivo(self.arq, espera_s=0.6):
                    pass
            self.assertGreaterEqual(time.time() - inicio, 0.5)
        finally:
            outro.encerrar()
        with trava_arquivo(self.arq, espera_s=5):   # o sistema solta a trava de quem morreu
            pass

    def test_planilha_manager_nao_troca_o_json_com_a_trava_ocupada(self):
        cota = Path(self.tmp.name) / 'COTAÇÕES.json'
        cota.write_text(json.dumps({'COTAÇÃO': []}), encoding='utf-8')
        self.arq.write_text(json.dumps({'PEDIDOS': [{'PEDIDO': '1'}]}), encoding='utf-8')
        m = Manager()
        m.configurar_caminhos(self.tmp.name)
        with patch.object(ns_manager['logger'], 'handle'):
            m.iniciar()
            m.pedidos['PEDIDOS'].append({'PEDIDO': '2'})
            outro = OutroProcesso(self.arq)
            try:
                with patch('trava_arquivo.ESPERA_S', 0.4):
                    self.assertFalse(m.salvar())
            finally:
                outro.encerrar()
        self.assertEqual(json.loads(self.arq.read_text(encoding='utf-8')), {'PEDIDOS': [{'PEDIDO': '1'}]})
        self.assertEqual([p.name for p in Path(self.tmp.name).glob('*.tmp')], [])


if __name__ == '__main__':
    unittest.main()
