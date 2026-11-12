# -*- coding: utf-8 -*-
"""Marcas com fornecedores próprios (Intelbras, Logitech) no Coupa e na Vale: eventos da marca no final do Word,
produtos parecidos juntos, e a lista de onde cotar logo depois dos eventos de cada marca."""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from docx import Document

from bots import itens_marcas as M
from bots.classificador_tecnologia import _CACHE_CLASSIFICACAO_TI
from test_fornecedores_ti import _resposta_ia

INTELBRAS = M._POR_NOME["Intelbras"]
LOGITECH = M._POR_NOME["Logitech"]


class DeteccaoTest(unittest.TestCase):
    def test_marca_da_ia_ou_texto_do_item(self):
        self.assertTrue(M.item_da_marca({'dados_ia': {'marca': 'INTELBRAS S/A'}}, "Intelbras"))
        self.assertTrue(M.item_da_marca({'descricao': 'Camera IP Intelbras VIP 1230 B'}, "Intelbras"))
        self.assertTrue(M.item_da_marca({'titulo': 'Não', 'texto_compra': 'MOUSE MX MASTER MARCA: LOGITECH'}, "Logitech"))
        self.assertFalse(M.item_da_marca({'descricao': 'CABO INTELBRASIL 3M'}, "Intelbras"))   # palavra inteira
        self.assertFalse(M.item_da_marca({'descricao': 'VALVULA', 'dados_ia': {'marca': 'SWAGELOK'}}, "Logitech"))
        self.assertFalse(M.item_da_marca({}, "Logitech"))

    def test_marca_do_evento(self):
        self.assertEqual(M.marca_do_evento({'itens': [{'descricao': 'PARAFUSO'}, {'descricao': 'WEBCAM LOGITECH C920'}]}), "Logitech")
        self.assertIsNone(M.marca_do_evento({'itens': [{'descricao': 'PARAFUSO'}]}))

    def test_listas(self):
        self.assertEqual(INTELBRAS["titulo"], "Itens Intelbras")
        self.assertEqual(INTELBRAS["linhas"], ["Sudeste", "Router", "Linhavix", "Mazer", "Plantec"])
        self.assertEqual(LOGITECH["titulo"], "Principais fornecedores que trabalham com a marca Logitech")
        self.assertEqual(len(LOGITECH["linhas"]), 16)
        self.assertEqual((LOGITECH["linhas"][0], LOGITECH["linhas"][-1]), ("Agis", "COLEÇAO"))

    def test_ordem_e_onde_entra_cada_mensagem(self):
        ev = lambda n, d, modelo="": {"numero_evento": n, "itens": [{"descricao": d, "dados_ia": {"modelo": modelo}}]}
        lista = M.marcas_no_final([
            ev("1", "MOUSE LOGITECH M90", "M90"), ev("2", "PARAFUSO M10"), ev("3", "CAMERA INTELBRAS VIP 1230", "VIP 1230"),
            ev("4", "VALVULA ESFERA"), ev("5", "WEBCAM LOGITECH C920", "C920"), ev("6", "MOUSE LOGITECH M90", "M90")])
        self.assertEqual([e["numero_evento"] for e in lista], ["2", "4", "3", "5", "1", "6"])
        self.assertEqual([M.mensagem_depois_do_evento(lista, i) for i in range(len(lista))],
                         [None, None, "Intelbras", None, None, "Logitech"])
        self.assertEqual(M.marcas_no_final([]), [])


class WordMarcasTest(unittest.TestCase):
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
            self._evento("2001", "FECHADURA ELETRONICA INTELBRAS FR 101"),
            self._evento("2002", "VALVULA ESFERA INOX 1/2 POL"),
            self._evento("2003", "APRESENTADOR LOGITECH R400"),
            self._evento("2004", "PARAFUSO SEXTAVADO M10"),
            self._evento("2005", "PORTEIRO ELETRONICO INTELBRAS IPR 1010"),
        ]
        bot.ARQUIVO_MARCAS_BLOQUEADAS = os.path.join(self.pasta, "nao_existe.xlsx")
        bot.PASTA_RELATORIOS = self.pasta
        bot.PASTA_BLOQUEADOS = self.pasta
        bot.config = {}
        bot.extrair_dados_gemini = lambda texto: {"marca": "", "modelo": "", "part_number": ""}
        bot.encontrar_vendedor_similar = lambda it, d: None
        bot.atualizar_excel = lambda dados: False
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}), \
                patch("bots.classificador_tecnologia.requests.post", return_value=_resposta_ia(pertence=False)):
            bot.gerar_word()
        return {a: [p.text for p in Document(os.path.join(self.pasta, a)).paragraphs]
                for a in os.listdir(self.pasta) if a.endswith(".docx")}

    def _conferir(self, docs):
        self.assertEqual(len(docs), 1, docs.keys())
        textos = next(iter(docs.values()))
        eventos = [t.split("EVENTO: ")[1].split()[0] for t in textos if "EVENTO:" in t]
        self.assertEqual(sorted(eventos[:2]), ["2002", "2004"])
        self.assertEqual(eventos[2:], ["2001", "2005", "2003"], "Intelbras e depois Logitech, no final")
        blocos = [t for t in textos if "EVENTO:" in t or t in (INTELBRAS["titulo"], LOGITECH["titulo"])]
        ordem = [b if "EVENTO:" not in b else b.split("EVENTO: ")[1].split()[0] for b in blocos]
        self.assertEqual(ordem[2:], ["2001", "2005", INTELBRAS["titulo"], "2003", LOGITECH["titulo"]])
        esperado = [LOGITECH["titulo"]] + LOGITECH["linhas"]
        self.assertEqual(textos[-len(esperado):], esperado, "lista Logitech fecha o Word")
        i = textos.index(INTELBRAS["titulo"])
        self.assertEqual(textos[i:i + 1 + len(INTELBRAS["linhas"])], [INTELBRAS["titulo"]] + INTELBRAS["linhas"])
        self.assertEqual(textos.count(INTELBRAS["titulo"]), 1)
        self.assertEqual(textos.count(LOGITECH["titulo"]), 1)

    def test_coupa(self):
        from bots.coupa import CoupaScraper
        self._conferir(self._rodar(CoupaScraper))

    def test_vale(self):
        from bots.vale_bot import ValeScraper
        self._conferir(self._rodar(ValeScraper))

    def test_word_sem_marca_nao_tem_mensagem(self):
        from bots.vale_bot import ValeScraper
        bot = ValeScraper.__new__(ValeScraper)
        bot.salvar_doc([self._evento("3001", "PARAFUSO M10")], self.pasta, "X")
        textos = [p.text for p in Document(os.path.join(self.pasta, "X_3001.docx")).paragraphs]
        self.assertNotIn(INTELBRAS["titulo"], textos)
        self.assertNotIn(LOGITECH["titulo"], textos)


if __name__ == '__main__':
    unittest.main()
