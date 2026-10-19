"""CONFIG.json e banco_usuarios.json salvos com BOM (Bloco de Notas do Windows) não podem derrubar o gerenciador."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from test_fase0 import carregar

RAIZ = Path(__file__).parent


class ConfigComBomTest(unittest.TestCase):
    def test_gerenciador_le_config_e_usuarios_com_bom(self):
        ns = carregar(RAIZ / 'gerenciador.py', {'carregar_config', 'carregar_usuarios'}, os=os, json=json)
        anterior = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                for nome, dados in (('CONFIG.json', {'verificacao_max_paginas': 200}), ('banco_usuarios.json', {'a@b.c': {}})):
                    Path(nome).write_text(json.dumps(dados), encoding='utf-8-sig')   # grava com BOM
                self.assertEqual(ns['carregar_config']()['verificacao_max_paginas'], 200)
                self.assertIn('a@b.c', ns['carregar_usuarios']())
            finally:
                os.chdir(anterior)

    def test_robos_leem_config_com_bom(self):
        # os robôs engolem o erro: com BOM o CONFIG inteiro era ignorado em silêncio
        for arquivo in ('bots/coupa/principal.py', 'bots/vale_bot.py'):
            self.assertIn("open('CONFIG.json', 'r', encoding='utf-8-sig')", (RAIZ / arquivo).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
