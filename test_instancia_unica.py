import os
import tempfile
import unittest

from instancia_unica import JaRodando, instancia_unica


class InstanciaUnicaTest(unittest.TestCase):
    def test_segundo_gerenciador_e_recusado_e_mostra_quem_esta_rodando(self):
        with tempfile.TemporaryDirectory() as pasta:
            primeiro = instancia_unica(pasta)
            try:
                with self.assertRaises(JaRodando) as erro:
                    instancia_unica(pasta)
                self.assertIn(f"PID {os.getpid()}", str(erro.exception))
            finally:
                primeiro.close()

    def test_trava_e_solta_quando_o_primeiro_sai(self):
        with tempfile.TemporaryDirectory() as pasta:
            instancia_unica(pasta).close()
            segundo = instancia_unica(pasta)
            segundo.close()


if __name__ == "__main__":
    unittest.main()
