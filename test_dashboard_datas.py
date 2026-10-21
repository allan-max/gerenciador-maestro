"""Dashboard de cotações: VENCIMENTO vem dos robôs como DD/MM/AAAA (Coupa e Vale)."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import planilha_manager as pm
from test_fase0 import carregar
from test_seguranca import Manager, SioFalso

GERENCIADOR = Path(__file__).with_name('gerenciador.py')


class DatasDoDashboardTest(unittest.TestCase):
    def totais(self, linhas, filtros):
        sio = SioFalso()
        ns = carregar(GERENCIADOR, {'comando_ler_excel_dashboard', 'classificar_resposta', 'sem_acento'},
                      sio=sio, unicodedata=__import__('unicodedata'))
        modulo = SimpleNamespace(planilha_manager=Manager(linhas), data_vencimento=pm.data_vencimento)
        with patch.dict(sys.modules, {'planilha_manager': modulo}):
            ns['comando_ler_excel_dashboard']({'clientId': 'c', 'filtros': filtros})
        return sio.ultimo('retorno_dados_dashboard')['totais']

    def test_dia_ate_12_e_lido_como_dia_e_nao_como_mes(self):
        linhas = [{'COTAÇÃO': '1', 'VENCIMENTO': '03/04/2026', 'VENDEDOR': 'A'}]
        self.assertEqual(self.totais(linhas, {'dataInicio': '2026-04-01', 'dataFim': '2026-04-30'})['total'], 1)
        self.assertEqual(self.totais(linhas, {'dataInicio': '2026-03-01', 'dataFim': '2026-03-31'})['total'], 0)

    def test_mm_dd_antigo_ainda_e_entendido_quando_nao_cabe_como_dia(self):
        linhas = [{'COTAÇÃO': '1', 'VENCIMENTO': '04/25/2026', 'VENDEDOR': 'A'}]
        self.assertEqual(self.totais(linhas, {'dataInicio': '2026-04-01', 'dataFim': '2026-04-30'})['total'], 1)


if __name__ == '__main__':
    unittest.main()
