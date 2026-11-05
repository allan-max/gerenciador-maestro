# -*- coding: utf-8 -*-
import os
import unittest
from unittest.mock import patch, MagicMock
from docx import Document

from bots.classificador_tecnologia import (
    FORNECEDORES_TECNOLOGIA,
    pre_identificar_suspeito_ti,
    analisar_item_tecnologia,
    adicionar_paragrafo_onde_encontrar,
    _CACHE_CLASSIFICACAO_TI,
)


class TestClassificadorTecnologia(unittest.TestCase):
    def setUp(self):
        _CACHE_CLASSIFICACAO_TI.clear()

    def test_pre_identificacao_detecta_itens_suspeitos_de_ti_e_tv(self):
        self.assertTrue(
            pre_identificar_suspeito_ti(
                "13227385 || MOUSE S/FIO OPTICO C/SCROLL WIF/BLUETO - PT",
                {"marca": "LOGITECH", "modelo": "M585"},
            )
        )
        self.assertTrue(
            pre_identificar_suspeito_ti(
                "13557306 || TABLET DIG;256GB;SAMSUNG/SMX736BZAHZTO - PT",
                {"marca": "SAMSUNG", "modelo": "SMX736BZAHZTO"},
            )
        )
        self.assertTrue(
            pre_identificar_suspeito_ti(
                "SMART TV 55 POL 4K UHD LED HDMI",
                {"marca": "", "modelo": ""},
            )
        )
        self.assertTrue(
            pre_identificar_suspeito_ti(
                "SWITCH GIGABIT 24 PORTAS POE GERENCIAVEL",
                {"marca": "", "modelo": ""},
            )
        )

    def test_pre_identificacao_bloqueia_itens_industriais_sem_gastar_tokens(self):
        # Itens mecânicos/elétricos industriais e falsos positivos conhecidos
        self.assertFalse(
            pre_identificar_suspeito_ti(
                "15395567 || PARAFUSO CAB SEXT INT 10MM - PT || NORMA DIN 912",
                {"marca": "ASSISPAR", "modelo": "M10X100MM"},
            )
        )
        self.assertFalse(
            pre_identificar_suspeito_ti(
                "13037042 || VALVULA MANIF 350BAR ENCAIXE 50MM - PT",
                {"marca": "HYDAC", "modelo": "6061220"},
            )
        )
        self.assertFalse(
            pre_identificar_suspeito_ti(
                "13212484 || PROJETOR LUMIN MONT 100-277V - PT || PROJETOR LUMINOSO MONTADO; TIPO LAMPADA APLICADA: LED; 165W",
                {"marca": "NORTEL", "modelo": "ELE91319"},
            )
        )
        self.assertFalse(
            pre_identificar_suspeito_ti(
                "13304068 || PROCESSAD E01-0702-059 SPECTRAFLOW - PT || PARTES E PECAS; NOME DO ITEM: PROCESSADOR; APLICACAO: ANALISADOR QUIMICO",
                {"marca": "SPECTRAFLOW", "modelo": "E01-0702-059"},
            )
        )

    @patch("bots.classificador_tecnologia.requests.post")
    def test_fluxo_completo_por_item_individual_e_renderizacao_word(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "content": [
                {
                    "type": "text",
                    "text": '{"pertence_segmento": true, "categoria": "Computadores/Notebooks", "fornecedores": ["Agis", "SND", "Pauta", "Mazer", "All Nations", "Ingram Micro", "Belmicro"], "incerteza": false}',
                }
            ]
        }
        mock_post.return_value = mock_resp

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            # Item 1 da cotação: Válvula industrial (não deve chamar IA nem ganhar "Onde encontrar?")
            item_valvula = {"item_num": "1", "descricao": "VALVULA ESFERA INOX 1/2 POL"}
            res1 = analisar_item_tecnologia(
                item_valvula,
                "VALVULA ESFERA INOX 1/2 POL",
                {"marca": "SWAGELOK", "modelo": "SS-43GS4"},
            )
            self.assertIsNone(res1)
            self.assertNotIn("onde_encontrar", item_valvula)
            mock_post.assert_not_called()

            # Item 2 da mesma cotação: Notebook corporativo (deve chamar IA e preencher "Onde encontrar?")
            item_notebook = {"item_num": "2", "descricao": "NOTEBOOK I7 16GB SSD 512GB TELA 14 POL"}
            res2 = analisar_item_tecnologia(
                item_notebook,
                "NOTEBOOK I7 16GB SSD 512GB TELA 14 POL",
                {"marca": "DELL", "modelo": "LATITUDE 5440"},
            )
            self.assertIsNotNone(res2)
            self.assertIn("onde_encontrar", item_notebook)
            self.assertIn("Agis", item_notebook["onde_encontrar"])
            self.assertIn("Ingram Micro", item_notebook["onde_encontrar"])
            self.assertEqual(mock_post.call_count, 1)

            # Testa a formatação enxuta no Word (.docx)
            doc = Document()
            adicionar_paragrafo_onde_encontrar(doc, item_valvula)
            self.assertEqual(len(doc.paragraphs), 0)

            adicionar_paragrafo_onde_encontrar(doc, item_notebook)
            self.assertEqual(len(doc.paragraphs), 1)
            texto_paragrafo = doc.paragraphs[0].text
            self.assertIn("Onde encontrar? (Computadores/Notebooks):", texto_paragrafo)
            self.assertIn("Agis · SND · Pauta · Mazer · All Nations · Ingram Micro · Belmicro", texto_paragrafo)
            self.assertIn("(sugestão do segmento)", texto_paragrafo)

    @patch("bots.classificador_tecnologia.requests.post")
    def test_ia_descarta_item_suspeito_que_nao_eh_informatica(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "content": [
                {
                    "type": "text",
                    "text": '{"pertence_segmento": false, "categoria": "", "fornecedores": [], "incerteza": false}',
                }
            ]
        }
        mock_post.return_value = mock_resp

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            item_industrial = {"item_num": "1", "descricao": "MONITOR DE GASES PORTATIL INDUSTRIAL MULTIGAS"}
            res = analisar_item_tecnologia(
                item_industrial,
                "MONITOR DE GASES PORTATIL INDUSTRIAL MULTIGAS",
                {"marca": "MSA", "modelo": "ALTAIR 4XR"},
            )
            self.assertIsNone(res)
            self.assertNotIn("onde_encontrar", item_industrial)


if __name__ == "__main__":
    unittest.main()
