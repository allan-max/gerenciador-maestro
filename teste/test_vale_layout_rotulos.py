# -*- coding: utf-8 -*-
"""Robô da Vale (bots/vale_base_metais): layout de nov/2026 da linha do item (Coupa da Vale). Os textos vêm como
<div class="s-fieldLabel">Item Text</div><p class="s-textField">...</p> (sem dt/dd) e o 2º p.s-textField
virou "Manufacturer Name" vazio. Antes disso a DESCRIÇÃO saía em branco (eventos 62324, 62698, 62848...).
HTML real do evento 62698 (item expandido, campos de resposta removidos), lido num Chrome headless."""
import ast
import logging
import re
import unittest
from pathlib import Path
from urllib.parse import quote

from selenium.webdriver.common.by import By
from fonte_vale_base_metais import FONTE_VALE_BASE_METAIS

RAIZ = Path(__file__).resolve().parent.parent
METODOS = {'_ler_campos_linha', '_ler_frete_linha', '_texto_valido', '_filter_desc', 'parse_local',
           '_texto_por_rotulo', '_rotulo_nao_descritivo'}

CAMPO = ('<div class="flexColumns__column -quarter"><div class="field s-showField">'
         '<div class="label_readonly s-fieldLabel">{rotulo}</div><p class="s-textField">{texto}</p></div></div>')

LINHA = (
    '<div class="line s-itemsAndServicesLine -expanded"><form class="fieldGrid s-itemsAndServicesFieldGrid flexPosition">'
    '<div class="fieldContainer flexPosition__element -grow"><div class="flexColumns -justifyStart">'
    '<div class="flexColumns__column -full"><div class="flexColumns -justifyStart -topSection">'
    '<div class="flexColumns__column -full"><div class="flexColumns -justifyStart -titleSection">'
    '<div class="flexColumns__column -quarter"><div class="field s-showField -title s-description">'
    '<p class="s-textField">13550167 || CADEIRA ESCR;ACOLCH PT;PLAXMETAL/6001619</p></div></div></div></div>'
    '<div class="flexColumns__column -quarter"><div class="field s-showField s-manufacturer_name">'
    '<div class="label_readonly s-fieldLabel">Manufacturer Name</div><p class="s-textField">{fabricante}</p></div></div>'
    '<div class="flexColumns__column -quarter"><div class="field s-showField s-classification_of_goods">'
    '<div class="label_readonly s-fieldLabel">NCM</div><p class="s-textField"></p></div></div>'
    '<div class="flexColumns__column -quarter"><div class="field s-showField s-need_by_date">'
    '<div class="label_readonly s-fieldLabel">Necessário até</div><p class="s-textField">03/12/2026</p></div></div>'
    '<div class="flexColumns__column -quarter"><div class="field s-showField s-ship_to_address"><div class="addressField s-addressField">'
    '<div class="label_readonly s-fieldLabel">Endereço de entrega</div><div class="addressLines">'
    '<p class="clearfix s-itemsAndServicesAddressLine">4127<br>4127 | Salobo - Marabá:MPL:1064<br>Acampamento 3 Alfa S/N<br>'
    '33931478/0002-75<br>68514-971 Marabá PA<br>Brasil<br>Código de localização: 4127</p></div></div></div></div>'
    '<div class="flexColumns__column -quarter"><div class="field s-showField s-details">'
    '<div class="label_readonly s-fieldLabel">Detalhes</div><ul class="attachments__list s-attachmentList"></ul></div></div>'
    '<div class="flexColumns__column -quarter"><div class="field s-showField s-request_details">'
    '<div class="label_readonly s-fieldLabel">Detalhes da solicitação</div>'
    '<p class="placeholderText s-noResponses">Nenhum detalhe de solicitação presente</p></div></div>'
    '<div class="flexColumns__column -full"><div class="field s-showField -lineCustomFields s-request_line_custom_fields">'
    '<div class="flexColumns -justifyStart">{campos}</div></div></div></div></div>'
    '<div class="flexColumns__column -full"><div class="flexColumns -justifyStart -midSection">'
    '<div class="flexColumns__column -quarter"><div class="field s-showField s-quantity">'
    '<div class="label_readonly s-fieldLabel">Quantidade esperada</div><p class="valueWithUnit s-withUnitField">'
    '<span class="s-value">10</span>&nbsp;<span class="s-unit">Piece | pecas</span></p></div></div></div></div>'
    '<div class="flexColumns__column -full"><div class="flexColumns -justifyStart">'
    '<div class="flexColumns__column -quarter"><div class="field s-showField s-item_description">'
    '<div class="label_readonly s-fieldLabel">Descrição do item</div><p class="s-textField"></p></div></div></div></div>'
    '</div></div></form></div>'
)

ITEM_TEXT_62698 = ('CADEIRA ESCRITORIO;TIPO:OPERATIVA;ACOLCHOADO:ACOLCHOADO PRETO;REVESTIMENTO:POLIURETANO;'
                   'BASE:GIRATORIA C/5 PES C/RODIZIO; FABRICANTE/REFERENCIA: PLAXMETAL / 60016.19;')
COMPRA_62698 = ('EN || CHAIR, OFFICE;TYPE:OPERATIVE; MANUFACTURER/REFERENCE: PLAXMETAL / 60016.19; '
                '*********************************************************** PT || CADEIRA ESCRITORIO;TIPO:OPERATIVA; '
                'FABRICANTE/REFERENCIA: PLAXMETAL / 60016.19; *********************************************************** '
                'ES || CHAIR, OFFICE;TYPE:OPERATIVE;')


def linha_html(item_text, compra, fabricante=''):
    campos = ''.join(CAMPO.format(rotulo=r, texto=t) for r, t in (
        ('Item Text', item_text), ('Material Purchase Text', compra), ('Short Text Service', 'Nenhum'),
        ('NCM', '9401.39.00 (9401.39.00)')))
    return LINHA.format(campos=campos, fabricante=fabricante)


def montar_robo():
    ns = {'logger': logging.getLogger('teste-layout-vale'), 'By': By, 're': re}
    arvore = ast.parse(FONTE_VALE_BASE_METAIS.read_text(encoding='utf-8'))
    nos = [n for c in arvore.body if isinstance(c, ast.ClassDef) for n in c.body if getattr(n, 'name', None) in METODOS]
    exec(compile(ast.Module(body=nos, type_ignores=[]), str(FONTE_VALE_BASE_METAIS), 'exec'), ns)
    return type('Robo', (), {nome: ns[nome] for nome in METODOS})()


class LayoutRotulosTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from selenium import webdriver
            ops = webdriver.ChromeOptions()
            ops.add_argument('--headless=new')
            cls.driver = webdriver.Chrome(options=ops)
        except Exception as e:
            raise unittest.SkipTest(f'Chrome headless indisponível: {e}')
        cls.robo = montar_robo()

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()

    def ler(self, html):
        self.driver.get('data:text/html;charset=utf-8,' + quote('<html><body>' + html + '</body></html>'))
        return self.robo._ler_campos_linha(self.driver.find_element(By.CSS_SELECTOR, 'div.s-itemsAndServicesLine'))

    def test_item_text_vira_descricao(self):   # evento 62698
        c = self.ler(linha_html(ITEM_TEXT_62698, COMPRA_62698))
        self.assertEqual(c['tit'], '13550167 || CADEIRA ESCR;ACOLCH PT;PLAXMETAL/6001619')
        self.assertEqual(c['desc'], ITEM_TEXT_62698)
        self.assertEqual(c['texto_item'], ITEM_TEXT_62698)
        self.assertTrue(c['texto_compra'].startswith('PT || CADEIRA ESCRITORIO'))
        self.assertNotIn('EN ||', c['texto_compra'])
        self.assertEqual(c['qtd'], '10 Piece | pecas')
        self.assertEqual(c['loc'], 'Marabá PA')

    def test_item_text_nenhum_usa_material_purchase_text(self):   # padrão do evento 62324
        c = self.ler(linha_html('Nenhum', COMPRA_62698))
        self.assertTrue(c['desc'].startswith('PT || CADEIRA ESCRITORIO'))
        self.assertEqual(c['texto_item'], '')

    def test_fabricante_preenchido_nao_vira_descricao(self):
        c = self.ler(linha_html(ITEM_TEXT_62698, COMPRA_62698, fabricante='PLAXMETAL'))
        self.assertEqual(c['desc'], ITEM_TEXT_62698)

    def test_sem_textos_descricao_vazia(self):
        c = self.ler(linha_html('Nenhum', 'Nenhum'))
        self.assertEqual((c['desc'], c['texto_item'], c['texto_compra']), ('', '', ''))


if __name__ == '__main__':
    unittest.main()
