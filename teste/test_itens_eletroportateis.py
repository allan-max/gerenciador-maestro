# -*- coding: utf-8 -*-
"""Eletroportáteis (Coupa e Vale): Words próprios com no máximo 3 eventos, produtos parecidos juntos e a orientação
de onde orçar + regra de compra no final do arquivo."""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from docx import Document

from bots import itens_eletroportateis as E
from bots.classificador_tecnologia import _CACHE_CLASSIFICACAO_TI
from test_fornecedores_ti import _resposta_ia


class DeteccaoTest(unittest.TestCase):
    def test_produtos_da_lista(self):
        for d in ["GRILL ELETRICO 220V", "Mixer de mão 400W", "CAFETEIRA ELÉTRICA 30 XÍCARAS", "CAFEITEIRA 1L",
                  "SANDUICHEIRA BRITANIA", "GARRAFA TÉRMICA 1,8L", "LIQUIDIFICADOR 3L", "LIQUIFICADOR INDUSTRIAL",
                  "AIR FRYER 4L", "FRITADEIRA ELETRICA SEM OLEO", "BATEDEIRA PLANETARIA", "MULTIPROCESSADOR 3 EM 1",
                  "PANELA ELÉTRICA DE ARROZ", "CHALEIRA ELETRICA INOX", "FOGÃO ELÉTRICO PORTÁTIL 1 BOCA",
                  "COOKTOP ELETRICO PORTATIL", "TORRADEIRA 2 FATIAS"]:
            self.assertTrue(E.item_e_eletro({'descricao': d}), d)

    def test_nao_confunde(self):
        for d in ["GARRAFA DE OXIGENIO 10M3", "GARRAFA PET 500ML", "VALVULA ESFERA", "GRILLAGEM METALICA",
                  "CAFE EM PO 500G", "PANELA DE ALUMINIO 10L", "FORNO INDUSTRIAL A GAS"]:
            self.assertFalse(E.item_e_eletro({'descricao': d}), d)

    def test_texto_de_compra_tambem_conta(self):
        self.assertTrue(E.evento_tem_eletro({'itens': [{'titulo': 'Não', 'descricao': 'ITEM 1',
                                                        'texto_compra': 'CAFETEIRA ELETRICA 127V'}]}))

    def test_lotes_de_3_com_parecidos_juntos(self):
        ev = lambda n, d: {"numero_evento": n, "itens": [{"descricao": d}]}
        lotes = E.lotes_eletro([ev("1", "CAFETEIRA"), ev("2", "AIR FRYER"), ev("3", "CAFETEIRA"),
                                ev("4", "LIQUIDIFICADOR"), ev("5", "AIR FRYER")])
        self.assertEqual([[e["numero_evento"] for e in l] for l in lotes], [["2", "5", "1"], ["3", "4"]])

    def test_mensagem(self):
        linhas = E.linhas_mensagem_eletro()
        self.assertEqual(linhas[0], "Locais para orçar:")
        self.assertEqual(linhas[1:10], ["Martins", "Tambasa", "FastShop", "Casa e Video", "Fujioka", "Bartofil",
                                        "Gazin", "Friopeça", "Belmicro"])
        self.assertIn("Regra de compra:", linhas)
        self.assertIn("No fabricante, sempre conferir: o pedido mínimo e o frete.", linhas)


class WordEletroTest(unittest.TestCase):
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
            self._evento("4001", "CAFETEIRA ELETRICA 30 XICARAS"),
            self._evento("4002", "VALVULA ESFERA INOX 1/2 POL"),
            self._evento("4003", "AIR FRYER 4 LITROS"),
            self._evento("4004", "CAFETEIRA ELETRICA 15 XICARAS"),
            self._evento("4005", "LIQUIDIFICADOR 3 LITROS"),
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
                patch("bots.classificador_tecnologia.requests.post", return_value=_resposta_ia(pertence=False)):
            bot.gerar_word()
        return {a: [p.text for p in Document(os.path.join(self.pasta, a)).paragraphs]
                for a in os.listdir(self.pasta) if a.endswith(".docx")}, enviados

    def _conferir(self, docs, enviados):
        eletro = sorted(a for a in docs if a.startswith("ELETROPORTATEIS_Eventos_"))
        self.assertEqual(eletro, ["ELETROPORTATEIS_Eventos_4003_4001_4004.docx", "ELETROPORTATEIS_Eventos_4005.docx"])
        esperado = [E.TITULO_MENSAGEM_ELETRO] + E.linhas_mensagem_eletro()
        for nome in eletro:
            self.assertLessEqual(sum("EVENTO:" in t for t in docs[nome]), 3, nome)
            self.assertEqual(docs[nome][-len(esperado):], esperado, nome)
            self.assertEqual(docs[nome].count(E.TITULO_MENSAGEM_ELETRO), 1)
        outros = [a for a in docs if not a.startswith("ELETROPORTATEIS_")]
        self.assertEqual(len(outros), 1)
        self.assertIn("4002", outros[0])
        self.assertNotIn(E.TITULO_MENSAGEM_ELETRO, docs[outros[0]])
        self.assertEqual(sorted(enviados), ["4001", "4002", "4003", "4004", "4005"])   # também vão para a planilha

    def test_coupa(self):
        from bots.coupa import CoupaScraper
        self._conferir(*self._rodar(CoupaScraper))

    def test_vale(self):
        from bots.vale_bot import ValeScraper
        self._conferir(*self._rodar(ValeScraper))


if __name__ == '__main__':
    unittest.main()
