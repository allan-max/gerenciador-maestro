import ast
import unittest
import tempfile
from types import SimpleNamespace
from dotenv import dotenv_values
from pathlib import Path


class AutenticacaoRoboTest(unittest.TestCase):
    def test_ler_segredo_do_env_nao_aplica_outras_opcoes_antecipadamente(self):
        tree = ast.parse(Path(__file__).with_name('gerenciador.py').read_text(encoding='utf-8'))
        assignment = next(n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'ROBO_SECRET' for t in n.targets))
        fallback = next(n for n in tree.body if isinstance(n, ast.If) and ast.unparse(n.test) == 'ROBO_SECRET is None')
        code = compile(ast.Module(body=[assignment, fallback], type_ignores=[]), '<credencial-robo>', 'exec')
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / '.env'
            file.write_text('\ufeffROBO_SECRET=' + 'a' * 64 + '\nURL_SERVIDOR=http://localhost:8000\n', encoding='utf-8')
            environment = {}
            namespace = {'os': SimpleNamespace(getenv=environment.get),
                         'dotenv_values': lambda **kwargs: dotenv_values(file, **kwargs)}
            exec(code, namespace)
            self.assertEqual(namespace['ROBO_SECRET'], 'a' * 64)
            self.assertEqual(environment, {})
            environment['ROBO_SECRET'] = 'b' * 64
            exec(code, namespace)
            self.assertEqual(namespace['ROBO_SECRET'], 'b' * 64)

    def test_segredo_obrigatorio_antes_de_configurar_base_ou_iniciar_servicos(self):
        tree = ast.parse(Path(__file__).with_name('gerenciador.py').read_text(encoding='utf-8'))
        guard = next(n for n in tree.body if isinstance(n, ast.If) and 'len(ROBO_SECRET)' in ast.unparse(n.test))
        database = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == 'planilha_manager.iniciar')
        config = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == 'carregar_config')
        self.assertLess(guard.lineno, database.lineno)
        self.assertLess(guard.lineno, config.lineno)
        load = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == 'load_dotenv')
        self.assertGreater(load.lineno, config.lineno, 'Preservar a ordem legada das outras opções, especialmente o modo local.')
        code = compile(ast.Module(body=[guard], type_ignores=[]), '<segredo-robo>', 'exec')
        for secret in ['', 'VEMKAUAN']:
            with self.assertRaises(SystemExit):
                exec(code, {'__name__': '__main__', 'ROBO_SECRET': secret})
        exec(code, {'__name__': '__main__', 'ROBO_SECRET': 'x' * 64})
        connect = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == 'sio.connect')
        auth = next(k.value for k in connect.keywords if k.arg == 'auth')
        self.assertEqual(ast.literal_eval(auth.keys[0]), 'robo_secret')
        self.assertEqual(ast.unparse(auth.values[0]), 'ROBO_SECRET')


if __name__ == '__main__':
    unittest.main()
