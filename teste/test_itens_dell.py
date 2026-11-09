# -*- coding: utf-8 -*-
"""Eventos com itens DELL (Coupa e Vale): Words próprios com 3 eventos cada e a orientação "Itens Dell" no final."""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from docx import Document

from bots import itens_dell as D
from bots.classificador_tecnologia import _CACHE_CLASSIFICACAO_TI
from test_fornecedores_ti import _resposta_ia


class DeteccaoTest(unittest.TestCase):
    def test_marca_da_ia_ou_texto_do_item(self):
        self.assertTrue(D.item_e_dell({'dados_ia': {'marca': 'DELL TECHNOLOGIES'}}))
        self.assertTrue(D.item_e_dell({'descricao': 'Monitor Dell P2422H 24 pol'}))
        self.assertTrue(D.item_e_dell({'titulo': 'Não', 'texto_compra': 'NOTEBOOK LATITUDE MARCA: DELL'}))
        self.assertFalse(D.item_e_dell({'descricao': 'VALVULA ESFERA', 'dados_ia': {'marca': 'SWAGELOK'}}))
        self.assertFalse(D.item_e_dell({'descricao': 'CABO MODELO DELLAVIA 3M'}))   # palavra inteira
        self.assertFalse(D.item_e_dell({}))

    def test_senha_vem_do_config_e_nao_do_codigo(self):
        self.assertEqual(D.linhas_mensagem_dell({}), D.LINHAS_MENSAGEM_DELL)
        com = D.linhas_mensagem_dell({'dell_premier_senha': 'SENHA-TESTE'})
        self.assertEqual(com[-2:], ['SENHA-TESTE', 'https://www.dell.com/premier/home/br/pt/rc1835497'])
        self.assertIn('vendas9@venturainformatica.com.br', com[-3])

    def test_evento_com_um_item_dell_basta(self):
        ev = {'itens': [{'descricao': 'PARAFUSO M10'}, {'descricao': 'TECLADO DELL KB216'}]}
        self.assertTrue(D.evento_tem_dell(ev))
        self.assertFalse(D.evento_tem_dell({'itens': [{'descricao': 'PARAFUSO M10'}]}))


class WordDellTest(unittest.TestCase):
    def setUp(self):
        _CACHE_CLASSIFICACAO_TI.clear()
        self.pasta = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.pasta, ignore_errors=True)

    def _evento(self, num, descricao):
        return {"numero_evento": num, "req_num": "R" + num, "data_vencimento": "10/12/2026 - 10:00",
                "tipo_frete": "CIF", "vencimento_tabela": "",
                "itens": [{"item_num": "1", "titulo": "Não", "descricao": descricao, "quantidade": "1",
                           "local_entrega": "VITORIA", "texto_item": "", "texto_compra": ""}]}

    def _rodar(self, classe):
        bot = classe.__new__(classe)
        bot.dados_processados_lote = [
            self._evento("1001", "NOTEBOOK DELL LATITUDE 5440"),
            self._evento("1002", "VALVULA ESFERA INOX 1/2 POL"),
            self._evento("1003", "MONITOR DELL P2422H"),
            self._evento("1004", "TECLADO DELL KB216"),
            self._evento("1005", "MOUSE DELL MS116"),
        ]
        bot.ARQUIVO_MARCAS_BLOQUEADAS = os.path.join(self.pasta, "nao_existe.xlsx")
        bot.PASTA_RELATORIOS = self.pasta
        bot.PASTA_BLOQUEADOS = self.pasta
        bot.config = {"dell_premier_senha": "SENHA-TESTE"}
        bot.extrair_dados_gemini = lambda texto: {"marca": "DELL" if "DELL" in texto else "", "modelo": "", "part_number": ""}
        bot.encontrar_vendedor_similar = lambda it, d: None
        enviados = []
        bot.atualizar_excel = lambda dados: enviados.extend(d["numero_evento"] for d in dados) or False
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}), \
                patch("bots.classificador_tecnologia.requests.post", return_value=_resposta_ia(pertence=False)):
            bot.gerar_word()
        return {a: Document(os.path.join(self.pasta, a)) for a in os.listdir(self.pasta) if a.endswith(".docx")}, enviados

    def _conferir(self, docs, enviados):
        dell = sorted(a for a in docs if a.startswith("DELL_Eventos_"))
        self.assertEqual(dell, ["DELL_Eventos_1001_1003_1004.docx", "DELL_Eventos_1005.docx"])
        for nome in dell:
            textos = [p.text for p in docs[nome].paragraphs]
            esperado = [D.TITULO_MENSAGEM_DELL] + D.linhas_mensagem_dell({"dell_premier_senha": "SENHA-TESTE"})
            self.assertEqual(textos[-len(esperado):], esperado, nome)
            self.assertEqual(textos.count(D.TITULO_MENSAGEM_DELL), 1, "mensagem uma vez, no final do arquivo")
        outros = [a for a in docs if not a.startswith("DELL_")]
        self.assertEqual(len(outros), 1)
        self.assertIn("1002", outros[0])
        self.assertNotIn(D.TITULO_MENSAGEM_DELL, [p.text for p in docs[outros[0]].paragraphs])
        self.assertEqual(sorted(enviados), ["1001", "1002", "1003", "1004", "1005"])   # Dell também vai para a planilha

    def test_coupa(self):
        from bots.coupa import CoupaScraper
        self._conferir(*self._rodar(CoupaScraper))

    def test_vale(self):
        from bots.vale_bot import ValeScraper
        self._conferir(*self._rodar(ValeScraper))


if __name__ == '__main__':
    unittest.main()
