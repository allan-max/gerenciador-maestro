# -*- coding: utf-8 -*-
"""Insistência inteligente na extração dos itens (Coupa e Vale): descrição em branco quando o item
não expande, ou expande e os textos ainda não renderizaram. Os robôs são lidos pela árvore (ast),
sem importar Selenium/IA; o DOM é simulado por objetos falsos com estado (aberto/fechado)."""
import ast
import logging
import re
import unittest
from pathlib import Path

from selenium.webdriver.common.by import By

RAIZ = Path(__file__).resolve().parent.parent
LOGGER = logging.getLogger('teste-insistencia')
ROBOS = (
    ('coupa', ('bots/coupa/mixins/extracao.py', 'bots/coupa/mixins/utilitarios.py', 'bots/coupa/mixins/planilhas.py')),
    ('vale', ('bots/vale_bot.py',)),
)
METODOS = {'_extrair_itens_linhas', '_ler_campos_linha', '_ler_frete_linha', '_texto_valido', '_filter_desc', 'parse_local',
           '_frete_do_evento', '_localidade_com_frete'}
SELETOR_BOTAO = "img.s-expandLines, div.s-expandSidebar"


class Elemento:
    def __init__(self, text='', filho=None):
        self.text = text
        self._filho = filho

    def find_element(self, by, seletor):
        if self._filho is None:
            raise LookupError(seletor)
        return self._filho


class Linha:
    """Linha de item: o local de entrega e os textos só existem com o item aberto."""

    def __init__(self, aberto=False, cliques_sem_efeito=0, leituras_sem_texto=0, rotulos=None, frete='EXW'):
        self.aberto = aberto
        self.cliques_sem_efeito = cliques_sem_efeito   # cliques que o portal ignora (DOM lento)
        self.leituras_sem_texto = leituras_sem_texto   # leituras, já aberto, antes de os textos renderizarem
        self.rotulos = {'Texto de compra': 'PT || BOMBA CENTRIFUGA 10CV'} if rotulos is None else rotulos
        self.frete = frete   # Incoterms: como o local, só existe com o item aberto
        self.cliques = 0
        self.botao = Elemento()
        self._textos_prontos = False

    def clicar(self):
        self.cliques += 1
        if self.cliques_sem_efeito > 0:
            self.cliques_sem_efeito -= 1
            return
        self.aberto = not self.aberto

    def find_element(self, by, seletor):
        if 's-expandLines' in seletor:
            return self.botao
        if seletor == "div.s-description p.s-textField":
            # O título é a primeira leitura de cada tentativa: serve de relógio da renderização dos textos
            self._textos_prontos = self.aberto and self.leituras_sem_texto <= 0
            if self.aberto:
                self.leituras_sem_texto -= 1
            return Elemento('MOTOBOMBA')
        if 's-shipping_term' in seletor and self.aberto and self.frete:
            return Elemento(self.frete)
        if 'Local de entrega:' in seletor:
            if not self.aberto:
                raise LookupError(seletor)
            return Elemento('Local de entrega: RUA A - 100 - ITABIRA - MG')
        if '//dt' in seletor and self.aberto and self._textos_prontos:
            for rotulo, texto in self.rotulos.items():
                if f"'{rotulo}'" in seletor:
                    return Elemento(rotulo, filho=Elemento(texto))
        raise LookupError(seletor)

    def find_elements(self, by, seletor):
        if seletor == "p.s-textField":
            return [Elemento('MOTOBOMBA')]
        if seletor == "p.valueWithUnit":
            return [Elemento('1.500,00 BRL'), Elemento('10 UN')]
        return []


class Driver:
    def __init__(self, linhas):
        self.linhas = linhas
        self.scripts = []

    def find_elements(self, by, seletor):
        return self.linhas

    def execute_script(self, script, *args):
        self.scripts.append(script)


class Relogio:
    def __init__(self):
        self.esperas = []

    def sleep(self, s):
        self.esperas.append(s)


class Acoes:
    """Substitui o ActionChains: clicar no botão de expandir alterna a linha dona dele."""

    def __init__(self, driver):
        self.driver = driver
        self.alvo = None

    def click(self, elemento):
        self.alvo = elemento
        return self

    def perform(self):
        next(l for l in self.driver.linhas if l.botao is self.alvo).clicar()


def montar_robo(arquivos, linhas):
    ns = {'logger': LOGGER, 'By': By, 'ActionChains': Acoes, 'time': Relogio(), 're': re}
    nos = []
    for arquivo in arquivos:
        for classe in (n for n in ast.parse((RAIZ / arquivo).read_text(encoding='utf-8')).body if isinstance(n, ast.ClassDef)):
            nos += [n for n in classe.body if getattr(n, 'name', None) in METODOS]
    exec(compile(ast.Module(body=nos, type_ignores=[]), arquivos[0], 'exec'), ns)
    robo = type('Robo', (), {nome: ns[nome] for nome in METODOS if nome in ns})()
    robo.driver = Driver(linhas)
    return robo, ns['time']


class InsistenciaItensTest(unittest.TestCase):
    def extrair(self, arquivos, linha):
        robo, relogio = montar_robo(arquivos, [linha])
        itens, _ = robo._extrair_itens_linhas([linha])
        return itens[0], robo, relogio

    def test_item_que_abre_de_primeira_nao_insiste(self):
        for nome, arquivos in ROBOS:
            with self.subTest(nome):
                linha = Linha()
                item, robo, relogio = self.extrair(arquivos, linha)
                self.assertEqual(item, {
                    "item_num": 1, "titulo": "MOTOBOMBA", "descricao": "PT || BOMBA CENTRIFUGA 10CV", "quantidade": "10 UN",
                    "local_entrega": "ITABIRA - MG", "texto_item": "", "texto_compra": "PT || BOMBA CENTRIFUGA 10CV", "frete": "EXW"})
                self.assertEqual(linha.cliques, 1)
                self.assertEqual(relogio.esperas, [0.3])
                self.assertEqual(robo.driver.scripts, [])

    def test_item_que_nao_expande_na_primeira_e_reexpandido(self):
        for nome, arquivos in ROBOS:
            with self.subTest(nome):
                linha = Linha(cliques_sem_efeito=1)
                with self.assertLogs(LOGGER, 'WARNING') as logs:
                    item, robo, _ = self.extrair(arquivos, linha)
                self.assertEqual(item["descricao"], "PT || BOMBA CENTRIFUGA 10CV")
                self.assertEqual(item["local_entrega"], "ITABIRA - MG")
                self.assertEqual(linha.cliques, 2)
                self.assertTrue(linha.aberto)
                self.assertEqual(len(robo.driver.scripts), 1)
                self.assertIn("scrollIntoView({block: 'center'})", robo.driver.scripts[0])
                self.assertIn('Item 1', logs.output[0])

    def test_item_ja_aberto_sem_texto_rele_sem_clicar_de_novo(self):
        for nome, arquivos in ROBOS:
            with self.subTest(nome):
                linha = Linha(leituras_sem_texto=1)
                with self.assertLogs(LOGGER, 'INFO') as logs:
                    item, robo, _ = self.extrair(arquivos, linha)
                self.assertEqual(item["descricao"], "PT || BOMBA CENTRIFUGA 10CV")
                self.assertEqual(linha.cliques, 1)       # um segundo clique fecharia o item
                self.assertTrue(linha.aberto)
                self.assertEqual(robo.driver.scripts, [])
                self.assertTrue(any('aberto' in m for m in logs.output))

    def test_item_que_nunca_expande_para_em_3_tentativas_e_nao_quebra(self):
        for nome, arquivos in ROBOS:
            with self.subTest(nome):
                linha = Linha(cliques_sem_efeito=99)
                with self.assertLogs(LOGGER, 'WARNING'):
                    item, _, _ = self.extrair(arquivos, linha)
                self.assertEqual(linha.cliques, 3)
                self.assertEqual((item["titulo"], item["quantidade"], item["local_entrega"], item["descricao"]),
                                 ("MOTOBOMBA", "10 UN", "N/A", ""))

    def test_descricao_vazia_e_preenchida_pelo_texto_do_item(self):
        for nome, arquivos in ROBOS:
            with self.subTest(nome):
                item, _, _ = self.extrair(arquivos, Linha(rotulos={'Texto do item': 'ROLAMENTO 6205 ZZ'}))
                self.assertEqual(item["texto_item"], "ROLAMENTO 6205 ZZ")
                self.assertEqual(item["descricao"], "ROLAMENTO 6205 ZZ")


HTML_FRETE = ('<div class="s-itemsAndServicesLine"><p class="s-selectField">Unidade</p>'
              '<div class="field s-showField s-shipping_term"><div class="label_readonly s-fieldLabel">Incoterms</div>'
              '<p class="s-selectField">{}</p></div></div>')


class LinhaHtml:
    """Linha de item sobre HTML de verdade: os seletores do robô rodam no markup do portal (bs4 = CSS, lxml = XPath)."""

    def __init__(self, html):
        from bs4 import BeautifulSoup
        from lxml import html as lxml_html
        self.sopa = BeautifulSoup(html, 'html.parser')
        self.arvore = lxml_html.fromstring(html)

    def find_element(self, by, seletor):
        if by == By.CSS_SELECTOR:
            achado = self.sopa.select_one(seletor)
            if achado is None:
                raise LookupError(seletor)
            return Elemento(achado.get_text())
        achados = self.arvore.xpath(seletor)
        if not achados:
            raise LookupError(seletor)
        return Elemento(achados[0].text_content())


class FreteTest(unittest.TestCase):
    def robo(self, arquivos):
        return montar_robo(arquivos, [])[0]

    def test_incoterms_do_html_do_item_expandido(self):
        for nome, arquivos in ROBOS:
            for frete in ('EXW', 'CIF', 'FOB'):
                with self.subTest(nome, frete=frete):
                    self.assertEqual(self.robo(arquivos)._ler_frete_linha(LinhaHtml(HTML_FRETE.format(frete))), frete)

    def test_incoterms_e_limpo_e_achado_pelo_rotulo_sem_a_classe_shipping_term(self):
        html = HTML_FRETE.format('\n  cif ').replace(' s-shipping_term', '')
        for nome, arquivos in ROBOS:
            with self.subTest(nome):
                self.assertEqual(self.robo(arquivos)._ler_frete_linha(LinhaHtml(html)), 'CIF')

    def test_linha_sem_incoterms_devolve_vazio(self):
        for nome, arquivos in ROBOS:
            with self.subTest(nome):
                linha = LinhaHtml('<div class="s-itemsAndServicesLine"><p class="s-selectField">Unidade</p></div>')
                self.assertEqual(self.robo(arquivos)._ler_frete_linha(linha), '')

    def test_frete_de_cada_item_vai_para_o_dicionario(self):
        for nome, arquivos in ROBOS:
            with self.subTest(nome):
                linhas = [Linha(frete='CIF'), Linha(frete='FOB', cliques_sem_efeito=1), Linha(frete='')]
                robo, _ = montar_robo(arquivos, linhas)
                with self.assertLogs(LOGGER, 'WARNING'):
                    itens, _ = robo._extrair_itens_linhas(linhas)
                self.assertEqual([it['frete'] for it in itens], ['CIF', 'FOB', ''])
                self.assertEqual(linhas[2].cliques, 1)   # frete ausente não é motivo para insistir

    def test_frete_do_evento_vem_do_primeiro_item_que_informa(self):
        for nome, arquivos in ROBOS:
            with self.subTest(nome):
                robo = self.robo(arquivos)
                itens = [{'frete': ''}, {'frete': 'CIF'}, {'frete': 'FOB'}]
                self.assertEqual(robo._frete_do_evento(itens, 'Unidade'), 'CIF')     # a aba de informações não sobrescreve
                self.assertEqual(robo._frete_do_evento(itens, ''), 'CIF')
                self.assertEqual(robo._frete_do_evento([{'frete': ''}, {}], ' fob '), 'FOB')
                self.assertEqual(robo._frete_do_evento([{'frete': ''}], ''), 'EXW')
                self.assertEqual(robo._frete_do_evento([], None), 'EXW')

    def test_localidade_da_planilha_leva_o_frete(self):
        for nome, arquivos in ROBOS:
            with self.subTest(nome):
                robo = self.robo(arquivos)
                self.assertEqual(robo._localidade_com_frete('ITABIRA - MG', 'CIF'), 'ITABIRA - MG (CIF)')
                self.assertEqual(robo._localidade_com_frete('ITABIRA - MG', ''), 'ITABIRA - MG')
                self.assertEqual(robo._localidade_com_frete(' ITABIRA - MG ', 'N/A'), 'ITABIRA - MG')

    def test_planilha_continua_com_9_colunas_e_localidade_na_quinta(self):
        for arquivo in ('bots/coupa/mixins/planilhas.py', 'bots/vale_bot.py'):
            with self.subTest(arquivo):
                fonte = (RAIZ / arquivo).read_text(encoding='utf-8')
                vals = next(l for l in fonte.splitlines() if l.strip().startswith("vals = [ev['numero_evento']"))
                colunas = ast.parse(vals.strip()).body[0].value.elts
                self.assertEqual(len(colunas), 9)
                self.assertIn('_localidade_com_frete', ast.unparse(colunas[4]))


if __name__ == "__main__":
    unittest.main()
