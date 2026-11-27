# -*- coding: utf-8 -*-
"""Evento reaberto por mudança de vencimento (Vale e Coupa): aviso com o vencimento antigo e o novo ao lado do
VENCIMENTO no Word e no campo "VENCIMENTO ALTERADO" do COTAÇÕES.json."""
import ast
import os
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from docx import Document

from bots.avisos import vencimento_alterado as va
from fonte_vale_base_metais import FONTE_VALE_BASE_METAIS

AVISO = "Vencimento alterado, vencimento antigo: 01/12/2026 Vencimento NOVO: 05/12/2026"


class AvisoTest(unittest.TestCase):
    def test_tabela_vale_antiga_e_tela_nova(self):
        # Tabela da Vale/Coupa em formato americano com am/pm; tela do evento já em dd/mm/aaaa - HH:MM
        self.assertEqual(va.aviso_vencimento_alterado("12/01/26 02:30 pm -03", "05/12/2026 - 14:30"), AVISO)

    def test_lista_coupa_em_portugues(self):
        self.assertEqual(va.aviso_vencimento_alterado("01/12/2026", "05/12/2026"), AVISO)

    def test_mesmo_vencimento_em_outro_formato_nao_avisa(self):
        self.assertEqual(va.aviso_vencimento_alterado("12/01/26 02:30 pm -03", "01/12/2026 - 14:30"), "")
        self.assertEqual(va.aviso_vencimento_alterado("01/12/26", "01/12/2026"), "")
        self.assertEqual(va.aviso_vencimento_alterado("12/01/26 02:30 pm -03", "01/12/2026"), "")

    def test_so_a_hora_mudou_mostra_as_horas(self):
        self.assertEqual(va.aviso_vencimento_alterado("12/01/26 02:30 pm -03", "01/12/2026 - 17:00"),
                         "Vencimento alterado, vencimento antigo: 01/12/2026 - 14:30 Vencimento NOVO: 01/12/2026 - 17:00")

    def test_data_ilegivel_nao_avisa(self):
        self.assertEqual(va.aviso_vencimento_alterado("", "05/12/2026"), "")
        self.assertEqual(va.aviso_vencimento_alterado("01/12/2026", "N/A"), "")


class AnotarTest(unittest.TestCase):
    def test_reaberto_ganha_aviso_e_campo_do_json(self):
        res = {"numero_evento": "62848", "data_vencimento": "05/12/2026 - 14:30"}
        va.anotar(res, {"re_extrair": True, "venc_anterior": "12/01/26 02:30 pm -03", "venc_tabela": "12/05/26 02:30 pm -03"})
        self.assertEqual(res["vencimento_alterado"], AVISO)
        self.assertEqual(va.extras_json(res), {"VENCIMENTO ALTERADO": AVISO})

    def test_sem_data_na_tela_usa_a_da_tabela(self):
        res = {"data_vencimento": "N/A"}
        va.anotar(res, {"re_extrair": True, "venc_anterior": "12/01/26 02:30 pm -03", "venc_tabela": "12/05/26 02:30 pm -03"})
        self.assertEqual(res["vencimento_alterado"], AVISO)

    def test_evento_novo_nao_ganha_aviso(self):
        res = {"data_vencimento": "05/12/2026 - 14:30"}
        va.anotar(res, {"re_extrair": False, "venc_anterior": "", "venc_tabela": "12/05/26 02:30 pm -03"})
        self.assertNotIn("vencimento_alterado", res)
        self.assertEqual(va.extras_json(res), {})

    def test_resultado_que_nao_e_evento_e_ignorado(self):
        for res in (None, "SKIPPED", "ENCERRADO"):
            self.assertEqual(va.anotar(res, {"re_extrair": True, "venc_anterior": "01/12/2026"}), "")


class WordTest(unittest.TestCase):
    def test_aviso_vermelho_no_mesmo_paragrafo_do_vencimento(self):
        doc = Document()
        p = doc.add_paragraph()
        p.add_run("VENCIMENTO: ").bold = True
        p.add_run("05/12/2026 - 14:30").bold = True
        va.escrever_no_word(p, {"vencimento_alterado": AVISO})
        self.assertEqual(p.text, "VENCIMENTO: 05/12/2026 - 14:30   " + AVISO)
        run = p.runs[-1]
        self.assertTrue(run.bold)
        self.assertEqual(str(run.font.color.rgb), "FF0000")

    def test_sem_aviso_nao_mexe_no_paragrafo(self):
        doc = Document()
        p = doc.add_paragraph("VENCIMENTO: 05/12/2026")
        va.escrever_no_word(p, {})
        self.assertEqual(p.text, "VENCIMENTO: 05/12/2026")


class LigacaoNosRobosTest(unittest.TestCase):
    """Os dois robôs guardam o vencimento antigo na tarefa, anotam o aviso, escrevem no Word e mandam ao JSON."""
    ARQUIVOS = {
        'vale': (str(FONTE_VALE_BASE_METAIS),),
        'coupa': ('bots/vale/mixins/extracao.py', 'bots/vale/mixins/planilhas.py'),
    }

    def codigo(self, nome):
        return "\n".join((RAIZ / a).read_text(encoding='utf-8') for a in self.ARQUIVOS[nome])

    def test_ligacao(self):
        for nome in self.ARQUIVOS:
            with self.subTest(nome):
                s = self.codigo(nome)
                self.assertIn("'venc_anterior': data_salva if re_extrair else \"\"", s)
                self.assertIn("vencimento_alterado.anotar(res, t)", s)
                self.assertIn("vencimento_alterado.escrever_no_word(p_cabecalho, ev)", s)
                self.assertIn("extras_linhas.append(vencimento_alterado.extras_json(ev))", s)
                self.assertIn("adicionar_linhas(self.NOME_ABA_ALVO, linhas_inserir, extras=extras_linhas)", s)
                for arquivo in self.ARQUIVOS[nome]:
                    ast.parse((RAIZ / arquivo).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
