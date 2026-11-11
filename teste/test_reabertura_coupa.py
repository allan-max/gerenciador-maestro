# -*- coding: utf-8 -*-
"""Reabertura de evento do Coupa por mudança de vencimento (varrer_painel_eventos).
Desde 07/11/2026 a lista mostra '06/11/2026' e o banco tinha '06/11/26': o texto diferente reabria centenas
de eventos sem mudança (1466 só no dia 07/11), inclusive vencidos, e cada um virava e-mail de erro."""
import unittest
from datetime import date

from bots.coupa.mixins.extracao import data_da_lista, vencimento_reabriu

HOJE = date(2026, 11, 11)


class Reabertura(unittest.TestCase):
    def test_ano_com_2_ou_4_digitos_e_o_mesmo_dia(self):
        self.assertEqual(data_da_lista('06/11/26'), date(2026, 11, 6))
        self.assertEqual(data_da_lista('06/11/2026'), date(2026, 11, 6))
        self.assertIsNone(data_da_lista('sem data'))
        self.assertIsNone(data_da_lista('31/02/2026'))

    def test_so_o_formato_mudou_nao_reabre(self):
        self.assertFalse(vencimento_reabriu('04/11/2026', '04/11/26', HOJE))
        self.assertFalse(vencimento_reabriu('18/11/2026', '18/11/26', HOJE))
        self.assertFalse(vencimento_reabriu('18/11/2026', '18/11/2026', HOJE))

    def test_prazo_prorrogado_reabre(self):
        self.assertTrue(vencimento_reabriu('20/11/2026', '18/11/26', HOJE))
        self.assertTrue(vencimento_reabriu('11/11/2026', '05/11/26', HOJE))   # vence hoje: ainda dá para responder

    def test_data_mudou_mas_ja_venceu_nao_reabre(self):
        self.assertFalse(vencimento_reabriu('10/11/2026', '05/11/26', HOJE))
        self.assertFalse(vencimento_reabriu('03/11/2026', '10/11/26', HOJE))

    def test_sem_data_de_um_dos_lados_nao_reabre(self):
        self.assertFalse(vencimento_reabriu('', '05/11/26', HOJE))
        self.assertFalse(vencimento_reabriu('20/11/2026', '', HOJE))

    def test_texto_que_nao_e_data_compara_como_antes(self):
        self.assertTrue(vencimento_reabriu('A definir', 'Pendente', HOJE))
        self.assertFalse(vencimento_reabriu('A definir', 'A definir', HOJE))


if __name__ == '__main__':
    unittest.main()
