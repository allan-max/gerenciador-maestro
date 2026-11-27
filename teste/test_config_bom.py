"""CONFIG.json e banco_usuarios.json salvos com BOM (Bloco de Notas do Windows) não podem derrubar o gerenciador."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from test_fase0 import carregar
from fonte_vale_base_metais import FONTE_VALE_BASE_METAIS

RAIZ = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == 'teste' else Path(__file__).resolve().parent


class ConfigComBomTest(unittest.TestCase):
    def test_gerenciador_le_config_e_usuarios_com_bom(self):
        ns = carregar(RAIZ / 'gerenciador.py', {'carregar_config', 'carregar_usuarios', 'com_padroes', 'PADROES_CONFIG'}, os=os, json=json)
        anterior = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            os.mkdir('dados')
            try:
                for nome, dados in (('CONFIG.json', {'verificacao_max_paginas': 200}), ('banco_usuarios.json', {'a@b.c': {}})):
                    Path('dados', nome).write_text(json.dumps(dados), encoding='utf-8-sig')   # grava com BOM
                self.assertEqual(ns['carregar_config']()['verificacao_max_paginas'], 200)
                self.assertIn('a@b.c', ns['carregar_usuarios']())
            finally:
                os.chdir(anterior)

    def test_robos_leem_config_com_bom(self):
        # os robôs engolem o erro: com BOM o CONFIG inteiro era ignorado em silêncio
        for arquivo in ('bots/vale/principal.py', str(FONTE_VALE_BASE_METAIS)):
            self.assertIn("open(os.path.join('dados', 'CONFIG.json'), 'r', encoding='utf-8-sig')", (RAIZ / arquivo).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
