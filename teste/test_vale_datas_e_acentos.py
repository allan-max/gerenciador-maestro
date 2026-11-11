# -*- coding: utf-8 -*-
"""vale_bot: datas de vencimento sem ambiguidade de dia/mês e textos do Word sem acentuação quebrada."""
import os
import re
import sys
import unittest

os.environ.setdefault('MAESTRO_TESTE', '1')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bots.vale_bot import ValeScraper

parse = ValeScraper.parsear_data_vencimento
ARQUIVO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bots', 'vale_bot.py'))


class TestDataVencimento(unittest.TestCase):
    def test_com_e_sem_zero_dao_a_mesma_data(self):
        self.assertEqual(parse("04/05/2026"), "04/05/2026")
        self.assertEqual(parse("4/5/2026"), "04/05/2026")
        self.assertEqual(parse("4/5/2026 14:30"), "04/05/2026 - 14:30")

    def test_formato_americano_com_am_pm(self):
        self.assertEqual(parse("12/10/2026 2:30 PM"), "10/12/2026 - 14:30")
        self.assertEqual(parse("4/5/2026 9:00 am"), "05/04/2026 - 09:00")

    def test_dia_maior_que_12_desfaz_ambiguidade(self):
        self.assertEqual(parse("25/11/2026 09:00"), "25/11/2026 - 09:00")
        self.assertEqual(parse("11/25/2026 9:00 PM"), "25/11/2026 - 21:00")
        self.assertEqual(parse("5/25/2026"), "25/05/2026")

    def test_iso_e_vazio(self):
        self.assertEqual(parse("2026-12-10 14:30:00 -0300"), "10/12/2026 - 14:30")
        self.assertIsNone(parse("N/A"))
        self.assertIsNone(parse(""))


class TestAcentuacao(unittest.TestCase):
    def test_codigo_sem_texto_quebrado(self):
        texto = open(ARQUIVO, encoding='utf-8').read()
        quebrados = re.findall(r'Ã[\u0080-\u00bf\u0152-\u2122]|â[\u0080-\u00bf\u0152-\u2122]{2}|ðŸ', texto)
        self.assertEqual(quebrados, [])
        self.assertIn('add_run("DESCRIÇÃO:\\n")', texto)
        self.assertIn('f"⚠️ BLOQUEADO:', texto)


if __name__ == '__main__':
    unittest.main()
