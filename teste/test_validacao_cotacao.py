# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch, MagicMock
from bots.validacao_cotacao import (
    contem_expected_quantity,
    validar_item_individual,
    validar_itens_evento
)


class TestValidacaoCotacao(unittest.TestCase):

    def test_contem_expected_quantity_padroes(self):
        """Verifica detecção de 'Expected Quantity x Unit Price' em várias formas."""
        casos_positivos = [
            "Expected Quantity x Unit Price",
            "Expected Quantity X Unit Price",
            "Expected Quantity * Unit Price",
            "Expected Quantity × Unit Price",
            "Expected  Quantity   x   Unit   Price",
            "Expected Quantity\nx\nUnit Price",
            "Tabela: Expected Quantity x Unit Price (Total)",
            "Quantidade esperada x Preço unitário",
            "Quantidade esperada × Preço unitário",
        ]
        for caso in casos_positivos:
            with self.subTest(caso=caso):
                self.assertTrue(contem_expected_quantity(caso), f"Falha ao detectar: {caso}")

        casos_negativos = [
            "Parafuso Sextavado M12",
            "Qtd: 10 PC | Preco: 50.00 BRL",
            "Item com descrição detalhada",
            "",
            None
        ]
        for caso in casos_negativos:
            with self.subTest(caso=caso):
                self.assertFalse(contem_expected_quantity(caso), f"Falso positivo em: {caso}")

    def test_validar_item_individual_valido(self):
        """Item com título/descrição e quantidade reais deve ser válido."""
        item = {
            "item_num": 1,
            "titulo": "ROLAMENTO DE ESFERAS 6205",
            "descricao": "Rolamento radial de uma carreira de esferas",
            "quantidade": "10 PC",
            "local_entrega": "USINA TUBARAO",
            "texto_item": "",
            "texto_compra": ""
        }
        valido, motivo = validar_item_individual(item)
        self.assertTrue(valido)
        self.assertEqual(motivo, "Item válido")

    def test_validar_item_individual_rejeita_expected_quantity(self):
        """Item contendo 'Expected Quantity x Unit Price' em qualquer campo deve ser rejeitado."""
        campos = ["titulo", "descricao", "quantidade", "local_entrega", "texto_item", "texto_compra"]
        for campo in campos:
            with self.subTest(campo=campo):
                item = {
                    "item_num": 1,
                    "titulo": "Produto Real",
                    "descricao": "Desc Real",
                    "quantidade": "5 UN",
                    "local_entrega": "Local Real"
                }
                item[campo] = "Expected Quantity x Unit Price"
                valido, motivo = validar_item_individual(item)
                self.assertFalse(valido)
                self.assertIn("Expected Quantity x Unit Price", motivo)

    def test_validar_item_individual_sem_identificacao(self):
        """Item sem título e sem descrição deve ser rejeitado."""
        item = {
            "item_num": 1,
            "titulo": "Item",
            "descricao": "",
            "quantidade": "0",
            "local_entrega": "N/A"
        }
        valido, motivo = validar_item_individual(item)
        self.assertFalse(valido)
        self.assertIn("Item sem identificação real", motivo)

    def test_validar_itens_evento_com_expected_quantity_em_contexto(self):
        """Quando a página exibe 'Expected Quantity x Unit Price' sem itens reais, deve invalidar."""
        texto_pagina = "Visualização da Cotação: Expected Quantity x Unit Price\nSem dados no momento."
        valido, motivo, tem_eq = validar_itens_evento([], texto_pagina)
        self.assertFalse(valido)
        self.assertTrue(tem_eq)
        self.assertIn("Expected Quantity x Unit Price", motivo)

    def test_validar_itens_evento_com_item_invalido(self):
        """Se algum item falhar na validação individual, o lote todo do evento é invalidado."""
        itens = [
            {"item_num": 1, "titulo": "Cabo de Aço", "descricao": "10mm", "quantidade": "100 M"},
            {"item_num": 2, "titulo": "Expected Quantity x Unit Price", "descricao": "", "quantidade": "0"}
        ]
        valido, motivo, tem_eq = validar_itens_evento(itens, "")
        self.assertFalse(valido)
        self.assertTrue(tem_eq)
        self.assertIn("Expected Quantity x Unit Price", motivo)

    def test_validar_itens_evento_sucesso(self):
        """Itens bem formados sem o marcador devem passar na validação."""
        itens = [
            {"item_num": 1, "titulo": "Válvula de Esfera", "descricao": "Inox 316 2 polegadas", "quantidade": "2 PC"},
            {"item_num": 2, "titulo": "Filtro Y", "descricao": "Bronze 1 polegada", "quantidade": "4 PC"}
        ]
        texto_pagina = "Página do Evento 301025. Itens e Serviços."
        valido, motivo, tem_eq = validar_itens_evento(itens, texto_pagina)
        self.assertTrue(valido)
        self.assertFalse(tem_eq)
        self.assertEqual(motivo, "Todos os itens foram corretamente identificados e validados")

    @patch('time.sleep')
    def test_coupa_scraper_retenta_e_alerta_em_expected_quantity(self, mock_sleep):
        """CoupaScraper deve retentar e emitir e-mail de alerta caso Expected Quantity persista."""
        from unittest.mock import MagicMock, patch
        from bots.coupa import CoupaScraper

        scraper = CoupaScraper.__new__(CoupaScraper)
        scraper.db_eventos = {}
        scraper.driver = MagicMock()
        scraper.wait = MagicMock()
        scraper._verificar_tela_erro = MagicMock(return_value=False)
        scraper.relatorio_email_stats = {
            'total_cotacoes': 0,
            'eventos_sem_itens': [],
            'eventos_expected_quantity': []
        }
        scraper.sio = MagicMock()
        scraper.enviar_relatorio_email = MagicMock()
        scraper.garantir_logado = MagicMock()

        # Simula tela retornando "Expected Quantity x Unit Price" e 0 linhas
        scraper.driver.find_elements.return_value = []
        body_mock = MagicMock()
        body_mock.text = "Visualização: Expected Quantity x Unit Price"
        scraper.driver.find_element.return_value = body_mock

        with patch('bots.coupa.mixins.extracao.WebDriverWait'):
            res = scraper.extrair_dados_do_evento("http://coupa.test/events/301025", "301025")

        # Deve ser considerado não processado com sucesso ("SKIPPED")
        self.assertEqual(res, "SKIPPED")
        # Deve ter registrado em eventos_sem_itens e eventos_expected_quantity
        self.assertIn("301025", scraper.relatorio_email_stats['eventos_sem_itens'])
        self.assertIn("301025", scraper.relatorio_email_stats['eventos_expected_quantity'])
        # Deve ter chamado o envio de e-mail de alerta
        scraper.enviar_relatorio_email.assert_called_once()
        # Deve ter tentado 3 vezes (refresh chamado pelo menos 2 vezes nas retentativas)
        self.assertGreaterEqual(scraper.driver.refresh.call_count, 2)

    @patch('time.sleep')
    def test_vale_scraper_retenta_e_alerta_em_expected_quantity(self, mock_sleep):
        """ValeScraper deve retentar e emitir e-mail de alerta caso Expected Quantity persista."""
        from unittest.mock import MagicMock, patch
        from bots.vale_bot import ValeScraper

        scraper = ValeScraper.__new__(ValeScraper)
        scraper.db_eventos = {}
        scraper.driver = MagicMock()
        scraper.wait = MagicMock()
        scraper._verificar_tela_erro = MagicMock(return_value=False)
        scraper.relatorio_email_stats = {
            'total_cotacoes': 0,
            'eventos_sem_itens': [],
            'eventos_expected_quantity': []
        }
        scraper.sio = MagicMock()
        scraper.enviar_relatorio_email = MagicMock()
        scraper.garantir_logado = MagicMock()

        scraper.driver.find_elements.return_value = []
        body_mock = MagicMock()
        body_mock.text = "Visualização: Expected Quantity x Unit Price"
        scraper.driver.find_element.return_value = body_mock

        with patch('bots.vale_bot.WebDriverWait'):
            res = scraper.extrair_dados_do_evento("http://vale.test/events/991000", "991000")

        self.assertEqual(res, "SKIPPED")
        self.assertIn("991000", scraper.relatorio_email_stats['eventos_sem_itens'])
        self.assertIn("991000", scraper.relatorio_email_stats['eventos_expected_quantity'])
        scraper.enviar_relatorio_email.assert_called_once()
        self.assertGreaterEqual(scraper.driver.refresh.call_count, 2)


if __name__ == '__main__':
    unittest.main()
