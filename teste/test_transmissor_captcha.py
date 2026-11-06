"""Transmissor do captcha: descarta quadro repetido, respeita ack, para sozinho e não emite depois de parar."""
import sys
import threading
import time
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bots import transmissor_captcha as tc  # noqa: E402


class DriverFalso:
    """Sem debuggerAddress: o transmissor cai no execute_cdp_cmd."""
    capabilities = {}
    current_window_handle = 'aba'

    def __init__(self, quadros):
        self.quadros = quadros
        self.capturas = 0
        self.params = []

    def execute_cdp_cmd(self, metodo, params):
        assert metodo == 'Page.captureScreenshot'
        self.params.append(params)
        self.capturas += 1
        return {'data': self.quadros(self.capturas)}


class SioFalso:
    def __init__(self, confirma=True):
        self.confirma = confirma
        self.emitidos = []
        self.trava = threading.Lock()

    def emit(self, evento, dados, callback=None):
        with self.trava:
            self.emitidos.append((time.monotonic(), dados['imagem']))
        if self.confirma and callback:
            callback()


class Dono:
    pass


RECT = {'x': 10, 'y': 20, 'width': 400, 'height': 580}


def rodar(segundos, driver, sio, dono=None):
    dono = dono or Dono()
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        tc.manter_transmissao(dono, driver, sio, RECT)
        time.sleep(0.05)
    tc.parar_transmissao(dono)
    return dono


class TransmissorTest(unittest.TestCase):
    def test_quadros_que_mudam_saem_em_alta_taxa(self):
        driver, sio = DriverFalso(lambda n: f'q{n}'), SioFalso()
        rodar(1.0, driver, sio)
        self.assertGreater(len(sio.emitidos), 30)       # ~60 por segundo, folga para máquina lenta
        self.assertLessEqual(len(sio.emitidos), 70)     # nunca acima do teto de 60 FPS (+folga)
        self.assertTrue(sio.emitidos[0][1].startswith('data:image/jpeg;base64,'))
        self.assertEqual(driver.params[0]['clip']['x'], 10.0)

    def test_quadro_repetido_nao_e_reenviado_mas_mantem_sinal_de_vida(self):
        driver, sio = DriverFalso(lambda n: 'igual'), SioFalso()
        rodar(2.3, driver, sio)
        self.assertGreater(driver.capturas, 30)
        self.assertIn(len(sio.emitidos), (2, 3))        # 1º quadro + keepalive a cada 1 s

    def test_servidor_sem_ack_nao_trava_a_transmissao(self):
        driver, sio = DriverFalso(lambda n: f'q{n}'), SioFalso(confirma=False)
        rodar(2.0, driver, sio)
        self.assertGreater(len(sio.emitidos), 20)       # 1 s de espera no 1º quadro, depois segue sem ack

    def test_nada_e_emitido_depois_de_parar(self):
        driver, sio = DriverFalso(lambda n: f'q{n}'), SioFalso()
        rodar(0.3, driver, sio)
        total = len(sio.emitidos)
        time.sleep(0.2)
        self.assertEqual(len(sio.emitidos), total)

    def test_watchdog_encerra_se_o_laco_do_robo_sumir(self):
        driver, sio, dono = DriverFalso(lambda n: f'q{n}'), SioFalso(), Dono()
        t = tc.manter_transmissao(dono, driver, sio, RECT)
        time.sleep(tc.WATCHDOG_S + 0.5)
        self.assertFalse(t.ativo)

    def test_sem_rect_nao_transmite(self):
        self.assertIsNone(tc.manter_transmissao(Dono(), DriverFalso(lambda n: 'x'), SioFalso(), None))


if __name__ == '__main__':
    unittest.main()
