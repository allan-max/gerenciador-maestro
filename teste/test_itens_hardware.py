# -*- coding: utf-8 -*-
"""Eventos de informática/hardware/TV (Coupa e Vale): Words próprios, no máximo 3 eventos por arquivo,
marcas/modelos parecidos juntos e a lista de fornecedores de hardware no final do arquivo."""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from docx import Document

from bots import itens_hardware as H
from bots.classificador_tecnologia import _CACHE_CLASSIFICACAO_TI
from test_fornecedores_ti import _resposta_ia


def _ev(num, *infos):
    return {'numero_evento': num, 'itens': [{'descricao': f'item {num}', 'onde_encontrar_info': i} for i in infos]}


class AgrupamentoTest(unittest.TestCase):
    def test_hardware_e_item_com_ti_confirmado(self):
        self.assertTrue(H.evento_tem_hardware({'itens': [{}, {'onde_encontrar_info': {'categoria': 'tvs'}}]}))
        self.assertFalse(H.evento_tem_hardware({'itens': [{'descricao': 'VALVULA'}]}))
        self.assertFalse(H.evento_tem_hardware({}))

    def test_no_maximo_3_por_arquivo_e_parecidos_juntos(self):
        lenovo = {'categoria': 'computadores', 'marca': 'LENOVO', 'termo_busca': 'LENOVO THINKPAD E14'}
        eventos = [
            _ev('1', {'categoria': 'tvs', 'marca': 'SAMSUNG', 'termo_busca': 'SAMSUNG UN50'}),
            _ev('2', lenovo),
            _ev('3', {'categoria': 'perifericos', 'marca': 'LOGITECH', 'termo_busca': 'LOGITECH M90'}),
            _ev('4', {'categoria': 'computadores', 'marca': 'HP', 'termo_busca': 'HP 250 G9'}),
            _ev('5', lenovo),
        ]
        lotes = H.lotes_hardware(eventos)
        self.assertEqual([[e['numero_evento'] for e in l] for l in lotes], [['4', '2', '5'], ['3', '1']])
        self.assertTrue(all(len(l) <= 3 for l in lotes))


class WordHardwareTest(unittest.TestCase):
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
            self._evento("2001", "NOTEBOOK LENOVO THINKPAD E14 I5 16GB"),
            self._evento("2002", "VALVULA ESFERA INOX 1/2 POL"),
            self._evento("2003", "SMART TV SAMSUNG 50 POL 4K UN50CU7700"),
            self._evento("2004", "NOTEBOOK LENOVO THINKPAD E14 I7 16GB"),
            self._evento("2005", "MONITOR LG 24 POL IPS 24MK430H"),
            self._evento("2006", "NOTEBOOK DELL LATITUDE 5440"),
        ]
        bot.ARQUIVO_MARCAS_BLOQUEADAS = os.path.join(self.pasta, "nao_existe.xlsx")
        bot.PASTA_RELATORIOS = self.pasta
        bot.PASTA_BLOQUEADOS = self.pasta

        def dados(texto):
            for marca in ("LENOVO", "SAMSUNG", "LG", "DELL"):
                if marca in texto:
                    return {"marca": marca, "modelo": "", "part_number": ""}
            return {"marca": "", "modelo": "", "part_number": ""}
        bot.extrair_dados_gemini = dados
        bot.encontrar_vendedor_similar = lambda it, d: None
        enviados = []
        bot.atualizar_excel = lambda lista: enviados.extend(d["numero_evento"] for d in lista) or False
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}), \
                patch("bots.classificador_tecnologia.requests.post", return_value=_resposta_ia()):
            bot.gerar_word()
        return {a: Document(os.path.join(self.pasta, a)) for a in os.listdir(self.pasta) if a.endswith(".docx")}, enviados

    def _conferir(self, docs, enviados):
        hw = sorted(a for a in docs if a.startswith("HARDWARE_Eventos_"))
        self.assertEqual(len(hw), 2, docs.keys())
        eventos_por_arquivo = [a[len("HARDWARE_Eventos_"):-len(".docx")].split("_") for a in hw]
        self.assertTrue(all(len(e) <= 3 for e in eventos_por_arquivo))
        self.assertEqual(sorted(sum(eventos_por_arquivo, [])), ["2001", "2003", "2004", "2005"])
        self.assertTrue(any({"2001", "2004"} <= set(e) for e in eventos_por_arquivo), "notebooks Lenovo juntos")
        for nome in hw:
            textos = [p.text for p in docs[nome].paragraphs]
            self.assertEqual(textos[-2:], [H.TITULO_MENSAGEM_HARDWARE, ", ".join(H.FORNECEDORES_HARDWARE)], nome)
            self.assertEqual(textos.count(H.TITULO_MENSAGEM_HARDWARE), 1)
        # Dell continua no Word próprio (com a mensagem Dell, sem a de hardware)
        self.assertIn("DELL_Eventos_2006.docx", docs)
        self.assertNotIn(H.TITULO_MENSAGEM_HARDWARE, [p.text for p in docs["DELL_Eventos_2006.docx"].paragraphs])
        # Item que não é de tecnologia segue o agrupamento normal, sem mensagem
        normais = [a for a in docs if a.startswith("Vencimento_")]
        self.assertEqual(normais, ["Vencimento_10-12-2026_Eventos_2002.docx"])
        self.assertNotIn(H.TITULO_MENSAGEM_HARDWARE, [p.text for p in docs[normais[0]].paragraphs])
        self.assertEqual(sorted(enviados), ["2001", "2002", "2003", "2004", "2005", "2006"])

    def test_coupa(self):
        from bots.coupa import CoupaScraper
        self._conferir(*self._rodar(CoupaScraper))

    def test_vale(self):
        from bots.vale_bot import ValeScraper
        self._conferir(*self._rodar(ValeScraper))


if __name__ == '__main__':
    unittest.main()
