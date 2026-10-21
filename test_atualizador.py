"""Atualizador automático (scripts/atualizador.py) com git de verdade: GitHub, servidor e dev em pastas temporárias."""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).parent
_spec = importlib.util.spec_from_file_location('atualizador', RAIZ / 'scripts' / 'atualizador.py')
atualizador = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(atualizador)

GIT_ENV = {'GIT_AUTHOR_NAME': 'teste', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 'teste', 'GIT_COMMITTER_EMAIL': 't@t'}


def git(pasta, *args):
    r = subprocess.run(['git', *args], cwd=pasta, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


class Repos(unittest.TestCase):
    """origem (o GitHub), servidor (clone de produção) e dev (de onde saem os commits novos)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        env = patch.dict(os.environ, GIT_ENV)
        env.start()
        self.addCleanup(env.stop)
        self.eventos = []
        logs = self.tmp / 'logs'
        logs.mkdir()
        for alvo in (patch.object(atualizador.eventos, 'registrar', lambda *a, **k: self.eventos.append(a)),
                     patch.object(atualizador, 'pasta', lambda: str(logs)),   # 21/10: os testes escreviam no atualizador.log de produção
                     patch.object(atualizador, 'enviar_email', lambda msg: None)):
            alvo.start()
            self.addCleanup(alvo.stop)

    def criar(self, nome, arquivos):
        origem, dev = self.tmp / f'{nome}.git', self.tmp / f'{nome}-dev'
        git(self.tmp, 'init', '-q', '--bare', str(origem))
        git(origem, 'symbolic-ref', 'HEAD', 'refs/heads/main')   # 'init -b' não existe no git do servidor (< 2.28)
        git(self.tmp, 'clone', '-q', str(origem), str(dev))
        git(dev, 'checkout', '-q', '-b', 'main')
        self.commit(dev, arquivos, 'inicial')
        servidor = self.tmp / f'{nome}-servidor'
        git(self.tmp, 'clone', '-q', str(origem), str(servidor))
        return dev, servidor

    def commit(self, dev, arquivos, msg):
        for nome, conteudo in arquivos.items():
            if conteudo is None:
                git(dev, 'rm', '-q', '--cached', nome)
                (dev / nome).unlink()
            else:
                (dev / nome).write_text(conteudo, encoding='utf-8')
                git(dev, 'add', nome)
        git(dev, 'commit', '-q', '-m', msg)
        git(dev, 'push', '-q', 'origin', 'main')
        return git(dev, 'rev-parse', 'HEAD')


class GerenciadorTest(Repos):
    def setUp(self):
        super().setUp()
        self.dev, self.servidor = self.criar('ger', {'app.py': 'v1', 'CONFIG.json': '{"SMTP_USER": "repo"}'})
        self.antigo = git(self.servidor, 'rev-parse', 'HEAD')
        (self.servidor / 'CONFIG.json').write_text('{"SMTP_USER": "producao"}', encoding='utf-8')   # o painel gravou
        alvo = patch.object(atualizador, 'RAIZ', str(self.servidor))
        alvo.start()
        self.addCleanup(alvo.stop)
        self.reiniciados, self.passa = [], True

    def estado(self, ocupados=(), versao=None):
        (self.servidor / 'gerenciador.estado').write_text(json.dumps(
            {'pid': 4321, 'em': time.time(), 'versao': versao or self.antigo, 'ocupados': list(ocupados)}), encoding='utf-8')

    def rodar(self):
        atualizador.atualizar_gerenciador(testar=lambda repo: (self.passa, 'Ran 9 tests OK'),
                                          reiniciar=lambda pid: self.reiniciados.append(pid))

    def test_commit_que_tira_o_config_do_repo_nao_apaga_o_de_producao(self):
        # 21/10: o Allan tirou CONFIG.json do repositório; o git pull do servidor travou (ou apagaria o arquivo)
        novo = self.commit(self.dev, {'CONFIG.json': None, 'app.py': 'v2'}, 'tira o CONFIG do repo')
        self.estado()
        self.rodar()
        self.assertEqual(git(self.servidor, 'rev-parse', 'HEAD'), novo)
        self.assertEqual((self.servidor / 'app.py').read_text(), 'v2')
        self.assertEqual((self.servidor / 'CONFIG.json').read_text(), '{"SMTP_USER": "producao"}')
        self.assertEqual(self.reiniciados, [4321])
        self.assertTrue(any('atualizado' in e[3] for e in self.eventos))

    def test_robo_trabalhando_espera_e_atualiza_depois(self):
        novo = self.commit(self.dev, {'app.py': 'v2'}, 'v2')
        self.estado(ocupados=['coupa'])
        self.rodar()
        self.assertEqual(git(self.servidor, 'rev-parse', 'HEAD'), self.antigo)      # nem baixou
        self.assertEqual(self.reiniciados, [])
        self.estado()                                                                # robô terminou
        self.rodar()
        self.assertEqual(git(self.servidor, 'rev-parse', 'HEAD'), novo)
        self.assertEqual(self.reiniciados, [4321])

    def test_teste_que_falha_volta_a_versao_e_avisa(self):
        self.commit(self.dev, {'app.py': 'quebrado'}, 'quebra')
        self.estado()
        self.passa = False
        self.rodar()
        self.assertEqual(git(self.servidor, 'rev-parse', 'HEAD'), self.antigo)
        self.assertEqual((self.servidor / 'app.py').read_text(), 'v1')
        self.assertEqual((self.servidor / 'CONFIG.json').read_text(), '{"SMTP_USER": "producao"}')
        self.assertEqual(self.reiniciados, [])
        self.assertEqual(self.eventos[-1][2], 'erro')

    def test_rodando_versao_velha_reinicia_mesmo_sem_commit_novo(self):
        self.estado(versao='0' * 40)            # baixado numa rodada em que um robô começou a trabalhar
        self.rodar()
        self.assertEqual(self.reiniciados, [4321])

    def test_em_dia_nao_faz_nada_e_gerenciador_parado_nao_e_iniciado(self):
        self.estado()
        self.rodar()
        self.assertEqual((self.reiniciados, self.eventos), ([], []))
        novo = self.commit(self.dev, {'app.py': 'v2'}, 'v2')
        (self.servidor / 'gerenciador.estado').unlink()                            # gerenciador desligado
        self.rodar()
        self.assertEqual(git(self.servidor, 'rev-parse', 'HEAD'), novo)
        self.assertEqual(self.reiniciados, [])


class PortalTest(Repos):
    def setUp(self):
        super().setUp()
        self.dev, self.portal = self.criar('portal', {'server.js': 'v1', 'package.json': '{}'})
        self.antigo = git(self.portal, 'rev-parse', 'HEAD')
        self.comandos, self.reinicios_pm2, self.cai_ao_subir = [], 0, False
        alvo = patch.object(atualizador, 'ler_estado', lambda: None)
        alvo.start()
        self.addCleanup(alvo.stop)

    def rodar_falso(self, cmd, cwd, timeout=900):
        if cmd[0] == 'git':
            return atualizador.rodar(cmd, cwd, timeout)
        self.comandos.append(cmd[:2])
        if cmd[1] == 'jlist':
            if self.cai_ao_subir:
                self.reinicios_pm2 += 1                                              # pm2 reiniciando em laço
            env = {'pm_cwd': str(self.portal), 'status': 'online', 'restart_time': self.reinicios_pm2}
            return 0, 'aviso do pm2\n' + json.dumps([{'name': 'Site-Maestro', 'pm2_env': env}])
        return 0, ''

    def atualizar(self):
        atualizador.atualizar_portal(rodar=self.rodar_falso, espera=lambda s: None)

    def test_atualiza_e_reinicia_e_so_instala_quando_dependencias_mudam(self):
        novo = self.commit(self.dev, {'server.js': 'v2'}, 'v2')
        self.atualizar()
        self.assertEqual(git(self.portal, 'rev-parse', 'HEAD'), novo)
        self.assertIn([atualizador.PM2, 'restart'], self.comandos)
        self.assertNotIn([atualizador.NPM, 'ci'], self.comandos)
        self.commit(self.dev, {'package.json': '{"dependencies": {}}'}, 'deps')
        self.atualizar()
        self.assertIn([atualizador.NPM, 'ci'], self.comandos)

    def test_site_que_nao_sobe_volta_a_versao(self):
        self.commit(self.dev, {'server.js': 'quebrado'}, 'quebra')
        self.cai_ao_subir = True
        self.atualizar()
        self.assertEqual(git(self.portal, 'rev-parse', 'HEAD'), self.antigo)
        self.assertEqual(self.comandos.count([atualizador.PM2, 'restart']), 2)       # o novo e a volta
        self.assertEqual(self.eventos[-1][2], 'erro')


class ChaveTest(unittest.TestCase):
    def test_liga_desliga_pelo_painel(self):
        self.assertTrue(atualizador.ligado({}))
        self.assertTrue(atualizador.ligado({'atualizacao_automatica': 'sim'}))
        for valor in ('não', 'NAO', 'false', '0', 'desligado'):
            self.assertFalse(atualizador.ligado({'atualizacao_automatica': valor}), valor)

    def test_desligado_nao_toca_em_nada(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(atualizador, 'RAIZ', tmp), \
                patch.object(atualizador.ambiente, 'ler_config', return_value={'atualizacao_automatica': 'não'}), \
                patch.object(atualizador, 'atualizar_gerenciador', side_effect=AssertionError('não devia rodar')), \
                patch.object(atualizador.eventos, 'ativar'):
            atualizador.main()


if __name__ == '__main__':
    unittest.main()
