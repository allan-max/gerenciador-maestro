# -*- coding: utf-8 -*-
"""Ar-condicionado (Coupa e Vale): Words próprios com no máximo 3 eventos, mesma capacidade junta e, no final,
a tabela Distribuidores | Fabricantes com a regra de compra."""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from docx import Document

from bots.word import itens_ar_condicionado as A
from bots.itens.classificador_tecnologia import _CACHE_CLASSIFICACAO_TI
from test_fornecedores_ti import _resposta_ia


class DeteccaoTest(unittest.TestCase):
    def test_reconhece(self):
        for d in ["AR CONDICIONADO SPLIT 12000 BTUS", "Ar-condicionado janela 7.500 BTU", "CONDICIONADOR DE AR 18.000 BTU/H",
                  "SPLIT HI-WALL INVERTER 9000", "AR CONDICIONADO PISO TETO 36000", "APARELHO 24.000 BTUS 220V",
                  "SPLIT INVERTER 220V", "CONTROLE REMOTO PARA AR-CONDICIONADO"]:
            self.assertTrue(A.item_e_ar({'descricao': d}), d)

    def test_nao_confunde(self):
        for d in ["SPLIT PIN AÇO INOX 3MM", "CONTRAPINO SPLIT 4X40", "FILTRO DE AR MOTOR CAT", "VALVULA ESFERA",
                  "COMPRESSOR DE AR 10 PCM", "CAFETEIRA ELETRICA"]:
            self.assertFalse(A.item_e_ar({'descricao': d}), d)

    def test_lotes_de_3_mesma_capacidade_junta(self):
        ev = lambda n, d: {"numero_evento": n, "itens": [{"descricao": d}]}
        lotes = A.lotes_ar([ev("1", "AR CONDICIONADO 18000 BTUS"), ev("2", "AR CONDICIONADO 9000 BTUS"),
                            ev("3", "AR CONDICIONADO 18.000 BTUS"), ev("4", "AR CONDICIONADO 12000 BTUS"),
                            ev("5", "AR CONDICIONADO 9.000 BTUS")])
        self.assertEqual([[e["numero_evento"] for e in l] for l in lotes], [["2", "5", "4"], ["1", "3"]])


class WordArTest(unittest.TestCase):
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
            self._evento("7001", "AR CONDICIONADO SPLIT 12000 BTUS INVERTER"),
            self._evento("7002", "VALVULA ESFERA INOX 1/2 POL"),
            self._evento("7003", "AR CONDICIONADO SPLIT 9000 BTUS"),
            self._evento("7004", "AR CONDICIONADO SPLIT 12.000 BTUS"),
            self._evento("7005", "CONDICIONADOR DE AR 24000 BTUS"),
        ]
        bot.ARQUIVO_MARCAS_BLOQUEADAS = os.path.join(self.pasta, "nao_existe.xlsx")
        bot.PASTA_RELATORIOS = self.pasta
        bot.PASTA_BLOQUEADOS = self.pasta
        bot.config = {}
        bot.extrair_dados_gemini = lambda texto: {"marca": "", "modelo": "", "part_number": ""}
        bot.encontrar_vendedor_similar = lambda it, d: None
        enviados = []
        bot.atualizar_excel = lambda dados: enviados.extend(d["numero_evento"] for d in dados) or False
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}), \
                patch("bots.itens.classificador_tecnologia.requests.post", return_value=_resposta_ia(pertence=False)):
            bot.gerar_word()
        return {a: Document(os.path.join(self.pasta, a)) for a in os.listdir(self.pasta) if a.endswith(".docx")}, enviados

    def _conferir(self, docs, enviados):
        ar = sorted(a for a in docs if a.startswith("AR_CONDICIONADO_Eventos_"))
        self.assertEqual(ar, ["AR_CONDICIONADO_Eventos_7003_7001_7004.docx", "AR_CONDICIONADO_Eventos_7005.docx"])
        for nome in ar:
            textos = [p.text for p in docs[nome].paragraphs]
            self.assertLessEqual(sum("EVENTO:" in t for t in textos), 3, nome)
            self.assertEqual(textos.count(A.TITULO_MENSAGEM_AR), 1)
            self.assertEqual(textos[-3:], [A.TITULO_REGRA_AR] + A.REGRA_COMPRA_AR, "regra de compra fecha o Word")
            tabela = docs[nome].tables[-1]
            colunas = [[c.text for c in col.cells] for col in tabela.columns]
            self.assertEqual(colunas[0], [A.CABECALHO_AR[0]] + A.DISTRIBUIDORES_AR)
            self.assertEqual([t for t in colunas[1] if t], [A.CABECALHO_AR[1]] + A.FABRICANTES_AR)
        outros = [a for a in docs if not a.startswith("AR_CONDICIONADO_")]
        self.assertEqual(len(outros), 1)
        self.assertIn("7002", outros[0])
        self.assertNotIn(A.TITULO_MENSAGEM_AR, [p.text for p in docs[outros[0]].paragraphs])
        self.assertEqual(sorted(enviados), ["7001", "7002", "7003", "7004", "7005"])   # também vão para a planilha

    def test_coupa(self):
        from bots.vale import CoupaScraper
        self._conferir(*self._rodar(CoupaScraper))

    def test_vale(self):
        from bots.vale_base_metais import ValeScraper
        self._conferir(*self._rodar(ValeScraper))


if __name__ == '__main__':
    unittest.main()
