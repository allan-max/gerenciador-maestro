"""Tetos de páginas editáveis no painel (CONFIG.json, padrão 60) e Chrome do robô que fecha o anterior (20/10)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import ambiente
import navegador
from test_fase0 import carregar

RAIZ = Path(__file__).parent


class PaginasVerificacaoTest(unittest.TestCase):
    def test_valor_do_painel_padrao_e_limites(self):
        p = ambiente.paginas_verificacao
        self.assertEqual(p('vale', {'vale_verificacao_max_paginas': '200'}), 200)     # o painel salva texto
        self.assertEqual(p('vale', {}), 60)
        self.assertEqual(p('vale', {'vale_verificacao_max_paginas': 'abc'}), 60)
        self.assertEqual(p('vale', {'vale_verificacao_max_paginas': '0'}), 1)
        self.assertEqual(p('vale', {'vale_verificacao_max_paginas': 99999}), 500)
        self.assertEqual(p('coupa', {'verificacao_max_paginas': 200}), 200)            # nome antigo ainda vale no Coupa
        self.assertEqual(p('vale', {'verificacao_max_paginas': 200}), 60)              # e não vale na Vale

    def test_le_o_arquivo_na_hora(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ambiente, 'RAIZ', tmp):
            self.assertEqual(ambiente.paginas_verificacao('coupa'), 60)                 # sem arquivo
            Path(tmp, 'CONFIG.json').write_text('{"coupa_verificacao_max_paginas": "80"}', encoding='utf-8-sig')
            self.assertEqual(ambiente.paginas_verificacao('coupa'), 80)
            Path(tmp, 'CONFIG.json').write_text('{quebrado', encoding='utf-8')
            self.assertEqual(ambiente.paginas_verificacao('coupa'), 60)


class ConfigDoGerenciadorTest(unittest.TestCase):
    NOMES = {'carregar_config', 'com_padroes', 'PADROES_CONFIG', 'salvar_config', 'salvar_config_admin'}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        anterior = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, anterior)
        self.sio = SimpleNamespace(emit=MagicMock(), on=lambda e: (lambda f: f))
        self.ns = carregar(RAIZ / 'gerenciador.py', self.NOMES, os=os, json=json, sio=self.sio,
                           CONFIG_GLOBAL={'SMTP_USER': 'x'}, SMTP_USER='', SMTP_PASSWORD='')

    def ler(self):
        return json.loads(Path('CONFIG.json').read_text(encoding='utf-8'))

    def test_chaves_novas_aparecem_com_60_sem_perder_o_resto(self):
        Path('CONFIG.json').write_text(json.dumps({'SMTP_USER': 'x', 'coupa_verificacao_max_paginas': '200'}), encoding='utf-8')
        cfg = self.ns['carregar_config']()
        self.assertEqual(cfg['coupa_verificacao_max_paginas'], '200')                  # valor salvo no painel fica
        self.assertEqual(cfg['vale_verificacao_max_paginas'], 60)
        self.assertEqual(self.ler(), cfg)                                              # gravado para o painel mostrar

    def test_salvar_pelo_painel_mantem_as_chaves_e_recusa_config_vazia(self):
        Path('CONFIG.json').write_text(json.dumps({'SMTP_USER': 'x'}), encoding='utf-8')
        self.ns['salvar_config_admin']({'clientId': 'c', 'config': {'SMTP_USER': 'y', 'vale_verificacao_max_paginas': '200'}})
        self.assertEqual(self.ler(), {'SMTP_USER': 'y', 'vale_verificacao_max_paginas': '200', 'coupa_verificacao_max_paginas': 60})
        self.ns['salvar_config_admin']({'clientId': 'c', 'config': {}})
        self.assertEqual(self.ler()['SMTP_USER'], 'y')                                  # não apagou o arquivo
        self.assertFalse(self.sio.emit.call_args[0][1]['sucesso'])


class NavegadorTest(unittest.TestCase):
    def test_fecha_so_o_chrome_deste_robo_e_limpa_o_perfil(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ambiente, 'RAIZ', tmp), patch.object(navegador.time, 'sleep'):
            antigo = Path(tmp, 'perfis_chrome', 'coupa')
            antigo.mkdir(parents=True)
            (antigo / 'Cookies').write_text('sessão velha')
            chamadas = []
            caminho = navegador.preparar('coupa', rodar=lambda cmd, **k: chamadas.append(cmd))
            self.assertEqual(Path(caminho), antigo)
            self.assertEqual(list(antigo.iterdir()), [])                                # login começa do zero
            if os.name == 'nt':
                script = chamadas[0][-1]
                self.assertIn("$_.Name -eq 'chrome.exe'", script)
                self.assertNotIn('"', script)                                          # aspas duplas quebravam a consulta
                self.assertIn(str(antigo), script)                                     # filtra pelo perfil deste robô
                self.assertNotIn('vale', script)

    def test_sem_powershell_abre_o_chrome_assim_mesmo(self):
        def falha(*a, **k):
            raise FileNotFoundError('powershell')
        with tempfile.TemporaryDirectory() as tmp, patch.object(ambiente, 'RAIZ', tmp):
            self.assertTrue(Path(navegador.preparar('vale', rodar=falha)).is_dir())

    def test_robos_usam_o_perfil_proprio(self):
        for arquivo, nome in (('bots/coupa/mixins/autenticacao.py', 'coupa'), ('bots/vale_bot.py', 'vale')):
            fonte = (RAIZ / arquivo).read_text(encoding='utf-8')
            self.assertIn(f"--user-data-dir={{navegador.preparar('{nome}')}}", fonte)
            self.assertIn('anterior.quit()', fonte)


if __name__ == '__main__':
    unittest.main()
