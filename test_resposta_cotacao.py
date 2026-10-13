import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from resposta_cotacao import CAMPOS, marcar_respondida


class RespostaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'COTAÇÕES.json'
        self.row = dict(zip(CAMPOS, ('123', 'GUSTAVO', 'Vitória', 'Câmera', '20/10/2026')))
        self.manager = SimpleNamespace(caminho_cotacoes=str(self.path), lock=threading.RLock())
        self.save([self.row, {**self.row, 'VENDEDOR': 'MICHELI'}])

    def save(self, rows):
        self.path.write_text(json.dumps({'COTAÇÃO': rows}), encoding='utf-8')

    def test_atualiza_apenas_linha_identificada_e_preserva_outros_campos(self):
        marcar_respondida(self.manager, self.row)
        rows = json.loads(self.path.read_text(encoding='utf-8'))['COTAÇÃO']
        self.assertEqual(rows[0], {**self.row, 'RESPOSTA': 'RESPONDIDO'})
        self.assertNotIn('RESPOSTA', rows[1])
        self.assertEqual(self.manager.cotacoes['COTAÇÃO'], rows)
        marcar_respondida(self.manager, self.row)  # Repetir não cria outra linha.
        self.assertEqual(len(self.manager.cotacoes['COTAÇÃO']), 2)

    def test_duplicada_ou_ausente_nao_grava(self):
        for rows in ([self.row, self.row], []):
            self.save(rows)
            original = self.path.read_bytes()
            with self.assertRaises(ValueError):
                marcar_respondida(self.manager, self.row)
            self.assertEqual(self.path.read_bytes(), original)

    def test_erro_de_gravacao_preserva_json_e_remove_temporario(self):
        original = self.path.read_bytes()
        with patch('resposta_cotacao.os.replace', side_effect=PermissionError('bloqueado')):
            with self.assertRaises(PermissionError):
                marcar_respondida(self.manager, self.row)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(len(list(self.path.parent.iterdir())), 1)

    def test_dados_invalidos_ou_json_corrompido_nao_sao_substituidos(self):
        with self.assertRaises(ValueError):
            marcar_respondida(self.manager, {})
        self.path.write_text('{incompleto', encoding='utf-8')
        with self.assertRaises(ValueError):
            marcar_respondida(self.manager, self.row)
        self.assertEqual(self.path.read_text(encoding='utf-8'), '{incompleto')


if __name__ == '__main__':
    unittest.main()
