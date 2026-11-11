# -*- coding: utf-8 -*-
"""aceitar_termos: marca o "Sim" de "Aceita os Termos e condições?" (div.s-term) antes do Pretendo."""
import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bots.termos_participacao import aceitar_termos, termos_pendentes


def _termo(data_id, marcado=False, desabilitado=False):
    sim = ' checked="checked"' if marcado else ''
    dis = ' disabled="disabled"' if desabilitado else ''
    return f'''
<div class="flexColumns__column s-term" data-id="{data_id}">
<h4>Aceita os Termos e condições?</h4>
<div class="form_element">
<label class="inlineSelection">
<input type="radio" name="term_{data_id}" id="term_{data_id}_true" value="true" class="s-termAccept"{dis}{sim}> Sim
</label>
<label class="inlineSelection">
<input type="radio" name="term_{data_id}" id="term_{data_id}_false" value="false" class="s-termReject"{dis}> Não
</label>
</div>
<div class="form_element s-termCommentWrapper hidden">
<textarea name="term_{data_id}_rejection" id="term_{data_id}_rejection" class="s-termComment"></textarea>
</div>
</div>'''


class FakeDriver:
    """Sem Chrome: só confere que erro no execute_script não derruba a extração."""
    def execute_script(self, *_):
        raise RuntimeError("sem página")


class TestAceitarTermosSemNavegador(unittest.TestCase):
    def test_erro_no_navegador_devolve_zero(self):
        self.assertEqual(aceitar_termos(FakeDriver()), 0)


class TestAceitarTermosNoChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from selenium import webdriver
            op = webdriver.ChromeOptions()
            op.add_argument('--headless=new')
            cls.driver = webdriver.Chrome(options=op)
        except Exception as e:
            raise unittest.SkipTest(f"Chrome indisponível: {e}")

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()

    def abrir(self, corpo):
        self.driver.get('data:text/html;charset=utf-8,' + urllib.parse.quote(f'<html><body>{corpo}</body></html>'))

    def marcados(self):
        return self.driver.execute_script(
            "return Array.from(document.querySelectorAll('input[type=radio]:checked')).map(r => r.id);")

    def test_marca_sim_em_todos_os_termos(self):
        self.abrir(_termo(1) + _termo(2))
        self.assertEqual(aceitar_termos(self.driver), 2)
        self.assertEqual(sorted(self.marcados()), ['term_1_true', 'term_2_true'])

    def test_troca_nao_por_sim(self):
        self.abrir(_termo(3).replace('class="s-termReject">', 'class="s-termReject" checked>'))
        aceitar_termos(self.driver)
        self.assertEqual(self.marcados(), ['term_3_true'])

    def test_termo_ja_aceito_e_desabilitado_fica_como_esta(self):
        self.abrir(_termo(124363, marcado=True, desabilitado=True))
        self.assertEqual(aceitar_termos(self.driver), 0)
        self.assertEqual(self.marcados(), ['term_124363_true'])

    def test_sim_com_valor_fora_do_padrao(self):
        # o "Sim" com value="Sim" (sem a classe s-termAccept) travava o evento
        html = _termo(5).replace('value="true" class="s-termAccept"', 'value="Sim"').replace('class="s-termReject"', '')
        self.abrir(html)
        self.assertEqual(aceitar_termos(self.driver), 1)
        self.assertEqual(self.marcados(), ['term_5_true'])
        self.assertEqual(termos_pendentes(self.driver), 0)

    def test_sim_reconhecido_so_pelo_rotulo(self):
        html = _termo(6).replace('value="true" class="s-termAccept"', 'value="A"').replace(
            'value="false" class="s-termReject"', 'value="B"')
        self.abrir(html)
        self.assertEqual(aceitar_termos(self.driver), 1)
        self.assertEqual(self.marcados(), ['term_6_true'])

    def test_nunca_marca_o_nao(self):
        html = _termo(7).replace('value="true" class="s-termAccept"', 'value="x"').replace(' Sim\n', ' Concordo\n')
        self.abrir(html)
        self.assertEqual(aceitar_termos(self.driver), 0)
        self.assertEqual(self.marcados(), [])
        self.assertEqual(termos_pendentes(self.driver), 1)

    def test_termos_pendentes_conta_os_sem_sim(self):
        self.abrir(_termo(8) + _termo(9, marcado=True))
        self.assertEqual(termos_pendentes(self.driver), 1)
        aceitar_termos(self.driver)
        self.assertEqual(termos_pendentes(self.driver), 0)

    def test_sem_termos_nao_faz_nada(self):
        self.abrir('<select id="participation"><option value="true">Pretendo</option></select>')
        self.assertEqual(aceitar_termos(self.driver), 0)


if __name__ == '__main__':
    unittest.main()
