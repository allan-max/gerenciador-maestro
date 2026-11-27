""".env e JSON do servidor em dados/: o gerenciador move os antigos da raiz ao iniciar sem perder nada."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from nucleo import ambiente
from test_fase0 import carregar

RAIZ = Path(__file__).resolve().parent.parent


class PastaDadosTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)

    def test_move_os_arquivos_da_raiz(self):
        (self.raiz / '.env').write_text('ROBO_SECRET=x', encoding='utf-8')
        (self.raiz / 'CONFIG.json').write_text('{"a": 1}', encoding='utf-8')
        (self.raiz / 'banco_usuarios.json').write_text('{}', encoding='utf-8')
        (self.raiz / 'gerenciador.estado').write_text('{}', encoding='utf-8')   # não é dado: fica na raiz
        movidos, conflito = ambiente.migrar_dados(str(self.raiz))
        self.assertEqual(sorted(movidos), ['.env', 'CONFIG.json', 'banco_usuarios.json'])
        self.assertEqual(conflito, [])
        self.assertEqual((self.raiz / 'dados' / '.env').read_text(encoding='utf-8'), 'ROBO_SECRET=x')
        self.assertFalse((self.raiz / 'CONFIG.json').exists())
        self.assertTrue((self.raiz / 'gerenciador.estado').exists())

    def test_ja_migrado_nao_faz_nada(self):
        (self.raiz / 'dados').mkdir()
        (self.raiz / 'dados' / 'CONFIG.json').write_text('{"a": 1}', encoding='utf-8')
        self.assertEqual(ambiente.migrar_dados(str(self.raiz)), ([], []))
        self.assertEqual((self.raiz / 'dados' / 'CONFIG.json').read_text(encoding='utf-8'), '{"a": 1}')

    def test_copia_igual_na_raiz_e_apagada(self):
        (self.raiz / 'dados').mkdir()
        for pasta in (self.raiz, self.raiz / 'dados'):
            (pasta / 'banco_ips_bloqueados.json').write_text('[]', encoding='utf-8')
        self.assertEqual(ambiente.migrar_dados(str(self.raiz)), (['banco_ips_bloqueados.json'], []))
        self.assertFalse((self.raiz / 'banco_ips_bloqueados.json').exists())

    def test_copia_diferente_fica_nos_dois_lugares(self):
        (self.raiz / 'dados').mkdir()
        (self.raiz / 'banco_usuarios.json').write_text('{"raiz": 1}', encoding='utf-8')
        (self.raiz / 'dados' / 'banco_usuarios.json').write_text('{"dados": 1}', encoding='utf-8')
        self.assertEqual(ambiente.migrar_dados(str(self.raiz)), ([], ['banco_usuarios.json']))
        self.assertEqual((self.raiz / 'banco_usuarios.json').read_text(encoding='utf-8'), '{"raiz": 1}')
        self.assertEqual((self.raiz / 'dados' / 'banco_usuarios.json').read_text(encoding='utf-8'), '{"dados": 1}')

    def test_gerenciador_antes_de_migrar_le_o_config_da_raiz_sem_criar_padrao(self):
        # o atualizador roda os testes (que importam o gerenciador) antes do reinício que migra:
        # um CONFIG padrão criado em dados/ venceria o real na migração
        ns = carregar(RAIZ / 'gerenciador.py', {'carregar_config', 'com_padroes', 'PADROES_CONFIG'}, os=os, json=json)
        anterior = os.getcwd()
        os.chdir(self.raiz)
        try:
            Path('CONFIG.json').write_text('{"SMTP_USER": "producao"}', encoding='utf-8')
            self.assertEqual(ns['carregar_config']()['SMTP_USER'], 'producao')
            self.assertFalse(Path('dados').exists())
        finally:
            os.chdir(anterior)

    def test_arquivo_dado_usa_a_raiz_so_antes_de_migrar(self):
        # o atualizador roda com um gerenciador antigo ainda aberto: lê da raiz sem mover nada
        self.assertEqual(ambiente.arquivo_dado('CONFIG.json', str(self.raiz)), str(self.raiz / 'dados' / 'CONFIG.json'))
        (self.raiz / 'CONFIG.json').write_text('{}', encoding='utf-8')
        self.assertEqual(ambiente.arquivo_dado('CONFIG.json', str(self.raiz)), str(self.raiz / 'CONFIG.json'))
        (self.raiz / 'dados').mkdir()
        (self.raiz / 'dados' / 'CONFIG.json').write_text('{}', encoding='utf-8')
        self.assertEqual(ambiente.arquivo_dado('CONFIG.json', str(self.raiz)), str(self.raiz / 'dados' / 'CONFIG.json'))


if __name__ == '__main__':
    unittest.main()
