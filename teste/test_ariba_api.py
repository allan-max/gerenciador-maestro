"""ariba_api contra um servidor HTTP falso (faz o papel do gateway e do AribaSourcing)."""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from nucleo import ariba_api


class Falso(BaseHTTPRequestHandler):
    estado = 'AGUARDANDO_COMANDO'
    recebidos = []
    resposta_iniciar = (200, {'sucesso': True, 'mensagem': 'Processamento iniciado com prioridade UNIÃO > ALIANÇA'})

    def log_message(self, *a):
        pass

    def _responder(self, status, corpo):
        dados = json.dumps(corpo, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        if self.path == '/health':
            self._responder(200, {'status': 'ok', 'estado': Falso.estado})
        else:
            self._responder(404, {})

    def do_POST(self):
        tamanho = int(self.headers.get('Content-Length') or 0)
        corpo = json.loads(self.rfile.read(tamanho).decode('utf-8')) if tamanho else None
        Falso.recebidos.append((self.path, corpo, self.headers.get('X-Api-Key')))
        if self.path == '/api/ariba/iniciar':
            self._responder(*Falso.resposta_iniciar)
        elif self.path == '/api/ariba/parar':
            Falso.estado = 'AGUARDANDO_COMANDO'
            self._responder(200, {'sucesso': True, 'mensagem': 'Processamento interrompido'})
        else:
            self._responder(404, {})


class AribaApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servidor = HTTPServer(('127.0.0.1', 0), Falso)
        threading.Thread(target=cls.servidor.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{cls.servidor.server_port}'
        cls.config = {'ariba_gateway_url': base + '/', 'ariba_servico_url': base}
        livre = HTTPServer(('127.0.0.1', 0), Falso)
        cls.fora_do_ar = {'ariba_gateway_url': f'http://127.0.0.1:{livre.server_port}',
                          'ariba_servico_url': f'http://127.0.0.1:{livre.server_port}'}
        livre.server_close()

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.servidor.server_close()

    def setUp(self):
        Falso.estado = 'AGUARDANDO_COMANDO'
        Falso.recebidos = []
        Falso.resposta_iniciar = (200, {'sucesso': True, 'mensagem': 'ok do robô'})
        os.environ.pop('ARIBA_API_KEY', None)

    def test_iniciar_envia_as_prioridades_no_formato_do_gateway(self):
        self.assertEqual(ariba_api.iniciar(self.config, '3, 1'), 'ok do robô')
        caminho, corpo, chave = Falso.recebidos[0]
        self.assertEqual(caminho, '/api/ariba/iniciar')
        self.assertEqual(corpo['dados'], {'prioridade': ['3', '1']})
        self.assertTrue(corpo['codigoTarefa'].startswith('GM-'))
        self.assertIsNone(chave)

    def test_iniciar_recusado_levanta_com_a_mensagem_do_robo(self):
        Falso.resposta_iniciar = (400, {'sucesso': False, 'mensagem': "Empresa desconhecida na prioridade: '9'"})
        with self.assertRaises(ariba_api.ErroAriba) as erro:
            ariba_api.iniciar(self.config, '9')
        self.assertIn('Empresa desconhecida', str(erro.exception))

    def test_chave_da_api_vai_no_cabecalho_quando_configurada(self):
        os.environ['ARIBA_API_KEY'] = 'segredo-de-teste'
        ariba_api.parar(self.config)
        self.assertEqual(Falso.recebidos[0], ('/api/ariba/parar', None, 'segredo-de-teste'))

    def test_processando_segue_o_estado_do_health(self):
        self.assertFalse(ariba_api.processando(self.config))
        Falso.estado = 'PROCESSANDO'
        self.assertTrue(ariba_api.processando(self.config))
        Falso.estado = 'AGUARDANDO_PROXIMO_CICLO'
        self.assertTrue(ariba_api.processando(self.config))

    def test_robo_fora_do_ar(self):
        self.assertIsNone(ariba_api.saude(self.fora_do_ar))
        self.assertFalse(ariba_api.processando(self.fora_do_ar))
        with self.assertRaises(ariba_api.ErroAriba):
            ariba_api.iniciar(self.fora_do_ar, '1')

    def test_garantir_no_ar_nao_mexe_na_tarefa_se_ja_responde(self):
        original = ariba_api.subprocess.run
        ariba_api.subprocess.run = lambda *a, **k: self.fail('não devia iniciar a tarefa')
        try:
            self.assertTrue(ariba_api.garantir_no_ar(self.config))
        finally:
            ariba_api.subprocess.run = original


if __name__ == '__main__':
    unittest.main()
