# -*- coding: utf-8 -*-
import unittest
from unittest.mock import MagicMock
from bots.recomendacao_vendedor import (
    marca_e_modelo_na_descricao,
    selecionar_alvos_comparacao_item
)
from bots.coupa import CoupaScraper
from bots.vale_bot import ValeScraper


class TestRecomendacaoVendedor(unittest.TestCase):

    def test_prioridade_principal_texto_item(self):
        """1. Prioridade Principal: Havendo Texto do Item, usa exclusivamente ele quando marca/modelo não estão na descrição."""
        item = {
            "texto_item": "PARAFUSO SEXTAVADO M12 AÇO INOX 316",
            "descricao": "PARAFUSO DIVERSOS"
        }
        dados_ia = {"marca": "", "modelo": ""}
        alvos = selecionar_alvos_comparacao_item(item, dados_ia)

        self.assertEqual(len(alvos), 1)
        self.assertEqual(alvos[0][0], "parafuso sextavado m12 aço inox 316")
        self.assertIn("Exclusivo", alvos[0][1])

    def test_excecao_ausencia_texto_item(self):
        """2. Exceção Descrição: Quando NÃO há Texto do Item disponível, usa a Descrição."""
        casos_sem_texto_item = ["", None, "N/A", "nenhum", "None"]
        for val in casos_sem_texto_item:
            with self.subTest(val=val):
                item = {
                    "texto_item": val,
                    "descricao": "VALVULA DE RETENCAO PILOTADA 1/2"
                }
                dados_ia = {"marca": "", "modelo": ""}
                alvos = selecionar_alvos_comparacao_item(item, dados_ia)

                self.assertEqual(len(alvos), 1)
                self.assertEqual(alvos[0][0], "valvula de retencao pilotada 1/2")
                self.assertIn("Ausencia de Texto do Item", alvos[0][1])

    def test_excecao_marca_e_modelo_na_descricao(self):
        """3. Exceção Marca e Modelo: Se marca e modelo forem encontrados na DESCRIÇÃO, ela tem prioridade."""
        item = {
            "texto_item": "MOTOR ELETRICO INDUSTRIAL",
            "descricao": "MOTOR DE INDUCAO TRIFASICO WEG W22 10CV 4P"
        }
        dados_ia = {
            "marca": "WEG",
            "modelo": "W22"
        }
        alvos = selecionar_alvos_comparacao_item(item, dados_ia)

        self.assertEqual(len(alvos), 2)
        # O primeiro elemento deve ser obrigatoriamente a descrição
        self.assertEqual(alvos[0][0], "motor de inducao trifasico weg w22 10cv 4p")
        self.assertIn("Prioridade: Marca e Modelo na Descricao", alvos[0][1])
        # O segundo elemento é o fallback para o texto do item
        self.assertEqual(alvos[1][0], "motor eletrico industrial")

    def test_marca_e_modelo_no_texto_item_mantem_texto_item_exclusivo(self):
        """Regra Crítica: Se marca e modelo estão no Texto do Item e NÃO na Descrição, a comparação é EXCLUSIVAMENTE pelo Texto do Item."""
        item = {
            "texto_item": "ROLAMENTO RIGIDO DE ESFERAS SKF 6205-2RSH",
            "descricao": "ROLAMENTO RADIAL COMUM"
        }
        dados_ia = {
            "marca": "SKF",
            "modelo": "6205-2RSH"
        }
        alvos = selecionar_alvos_comparacao_item(item, dados_ia)

        # Não deve conter a descrição nem como fallback
        self.assertEqual(len(alvos), 1)
        self.assertEqual(alvos[0][0], "rolamento rigido de esferas skf 6205-2rsh")
        self.assertIn("Exclusivo", alvos[0][1])

    def test_marca_e_modelo_na_descricao_funcao_auxiliar(self):
        """Verifica a lógica booleana da função marca_e_modelo_na_descricao."""
        # Ambos presentes na descrição
        self.assertTrue(marca_e_modelo_na_descricao("BOMBA KSB MEGANORM 50-200", "KSB", "MEGANORM"))
        # Apenas marca identificada e presente
        self.assertTrue(marca_e_modelo_na_descricao("BOMBA KSB HIDRAULICA", "KSB", ""))
        # Apenas modelo identificado e presente
        self.assertTrue(marca_e_modelo_na_descricao("BOMBA HIDRAULICA MEGANORM", "", "MEGANORM"))
        # Marca e modelo identificados, mas apenas um deles está na descrição
        self.assertFalse(marca_e_modelo_na_descricao("BOMBA KSB DIVERSA", "KSB", "MEGANORM"))
        # Nenhum presente
        self.assertFalse(marca_e_modelo_na_descricao("VALVULA DE ESFERA", "SCHNEIDER", "ATV320"))
        # Sem dados de marca/modelo
        self.assertFalse(marca_e_modelo_na_descricao("VALVULA DE ESFERA", "", ""))

    def test_encontrar_vendedor_similar_coupa_com_exclusividade(self):
        """Garante que CoupaScraper respeita a exclusividade do Texto do Item contra falso match na descrição."""
        scraper = CoupaScraper.__new__(CoupaScraper)
        # Histórico tem descrição que casaria com a descrição do item, mas não com o texto_item
        scraper.carregar_historico = MagicMock(return_value=[
            {
                "data": "01/01/2026",
                "descricao": "PARAFUSO DIVERSOS DE FIXACAO",
                "vendedor": "CARLOS_VENDEDOR",
                "modelo": "",
                "marca": ""
            }
        ])

        item = {
            "texto_item": "SENSOR INDUTIVO PEPPERL FUCHS NBB2-V3-E2",
            "descricao": "PARAFUSO DIVERSOS DE FIXACAO"
        }
        dados_ia = {"marca": "PEPPERL+FUCHS", "modelo": "NBB2-V3-E2"}

        # Como marca/modelo NÃO estão na descrição, a busca deve ser EXCLUSIVA no texto_item.
        # Portanto, o histórico que coincide com a 'descricao' NÃO deve ser retornado!
        res = scraper.encontrar_vendedor_similar(item, dados_ia)
        self.assertIsNone(res)

    def test_encontrar_vendedor_similar_vale_com_prioridade_descricao(self):
        """Garante que ValeScraper prioriza a descrição quando marca e modelo estão nela."""
        scraper = ValeScraper.__new__(ValeScraper)
        scraper.carregar_historico = MagicMock(return_value=[
            {
                "data": "01/01/2026",
                "descricao": "MOTOR DE INDUCAO TRIFASICO WEG W22 10CV 4P",
                "vendedor": "MARIANA_VENDEDORA",
                "modelo": "",
                "marca": ""
            }
        ])

        item = {
            "texto_item": "ACIONAMENTO GERAL",
            "descricao": "MOTOR DE INDUCAO TRIFASICO WEG W22 10CV 4P"
        }
        dados_ia = {"marca": "WEG", "modelo": "W22"}

        res = scraper.encontrar_vendedor_similar(item, dados_ia)
        self.assertIsNotNone(res)
        self.assertIn("MARIANA_VENDEDORA", res)
        self.assertIn("Marca e Modelo na Descricao", res)


if __name__ == '__main__':
    unittest.main()
