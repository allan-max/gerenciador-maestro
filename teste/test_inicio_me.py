# -*- coding: utf-8 -*-
"""Mercado Eletrônico abre pelo 'dotnet run' na pasta do projeto, sem depender do nome do .bat
([WinError 2] em 06/11: o .bat do servidor foi renomeado para Mercado-Eletronico.bat)."""
import ast
import os
import unittest

from test_fase0 import RAIZ, carregar

FONTE = (RAIZ / 'gerenciador.py').read_text(encoding='utf-8')


def obter_pasta_me(config):
    ns = carregar(RAIZ / 'gerenciador.py', {'obter_pasta_me', 'PASTA_ME_PADRAO'}, CONFIG_GLOBAL=config)
    return ns['obter_pasta_me']()


class TestInicioMe(unittest.TestCase):
    def test_pasta_padrao(self):
        self.assertEqual(obter_pasta_me({}), r"C:\Users\Administrator\Desktop\Mercado-Eletronico")

    def test_pasta_do_config(self):
        self.assertEqual(obter_pasta_me({'caminho_me': r'D:\ME'}), r'D:\ME')

    def test_inicia_com_dotnet_run_na_pasta_e_sem_o_bat(self):
        trecho = FONTE[FONTE.index("    if portal == 'me':"):FONTE.index("    elif portal == 'ariba':")]
        self.assertIn('dotnet run', trecho)
        self.assertIn('cwd=pasta_me', trecho)
        self.assertIn('NODE_SKIP_PLATFORM_CHECK', trecho)
        self.assertNotIn('caminho_bat_me', trecho)


if __name__ == '__main__':
    unittest.main()
