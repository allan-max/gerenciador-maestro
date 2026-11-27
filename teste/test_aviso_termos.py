# -*- coding: utf-8 -*-
"""Eventos com "Aceita os Termos e condições?": leitura dos termos na tela (HTML real do evento 312674), aceite,
e o e-mail com a lista de eventos que tinham termos (enviado mesmo quando o robô aceita)."""
import os
import sys
import unittest
import urllib.parse
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bots.avisos import aviso_termos as A
from bots.portal.termos_participacao import aceitar_termos, ler_termos, termos_pendentes

# Como o portal mostra (mapeado em 24/11/2026, /quotes/external_responses/312674/event_info), sem os rádios marcados
TELA_REAL = '''
<h2>Aceitar os termos e condições</h2>
<div class="flexColumns">
 <div class="flexColumns__column"><h4>Termos e condições</h4><p>O frete será de responsabilidade do fornecedor.</p>
  <p>A cotação deverá ser elaborada conforme a última ficha técnica enviada e validada pela área responsável.</p></div>
 <div class="flexColumns__column s-term" data-id="127846"> <h4> Aceita os Termos e condições? </h4>
  <div class="form_element"> <label class="inlineSelection"> <input type="radio" name="term_127846" id="term_127846_true" value="true" class="s-termAccept"> Sim </label>
  <label class="inlineSelection"> <input type="radio" name="term_127846" id="term_127846_false" value="false" class="s-termReject"> Não </label> </div></div>
</div>
<div class="flexColumns">
 <div class="flexColumns__column"><h4>Termos e condições</h4><p>Endereço de entrega: Rua Piauí, 1815 – Savassi, Belo Horizonte/MG – CEP 30.150-325</p></div>
 <div class="flexColumns__column s-term" data-id="127847"> <h4> Aceita os Termos e condições? </h4>
  <div class="form_element"> <label class="inlineSelection"> <input type="radio" name="term_127847" id="term_127847_true" value="true" class="s-termAccept"> Sim </label>
  <label class="inlineSelection"> <input type="radio" name="term_127847" id="term_127847_false" value="false" class="s-termReject"> Não </label> </div></div>
</div>
<select id="participation"><option value="true">Pretendo</option></select>'''

# Se o portal trocar as classes: só o texto da pergunta e rádios com rótulo Sim/Não
TELA_SEM_CLASSES = '''
<div class="linha"><div class="col">Termos e condições Frete CIF Itabira.</div>
 <div class="col"><span>Aceita os termos e condições?</span>
  <label><input type="radio" name="t1" id="t1a" value="1"> Sim</label>
  <label><input type="radio" name="t1" id="t1b" value="0"> Não</label></div></div>'''


class TelaTest(unittest.TestCase):
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
        return self.driver.execute_script("return Array.from(document.querySelectorAll('input[type=radio]:checked')).map(r => r.id);")

    def test_le_os_termos_da_tela_real_com_o_texto(self):
        self.abrir(TELA_REAL)
        termos = ler_termos(self.driver)
        self.assertEqual([t['id'] for t in termos], ['127846', '127847'])
        self.assertTrue(termos[0]['texto'].startswith('O frete será de responsabilidade do fornecedor.'))
        self.assertIn('última ficha técnica', termos[0]['texto'])
        self.assertTrue(termos[1]['texto'].startswith('Endereço de entrega: Rua Piauí, 1815'))
        self.assertEqual([t['aceito'] for t in termos], [False, False])

    def test_aceita_e_depois_le_como_aceito(self):
        self.abrir(TELA_REAL)
        self.assertEqual(aceitar_termos(self.driver), 2)
        self.assertEqual(sorted(self.marcados()), ['term_127846_true', 'term_127847_true'])
        self.assertEqual(termos_pendentes(self.driver), 0)
        self.assertEqual([t['aceito'] for t in ler_termos(self.driver)], [True, True])

    def test_acha_pelo_texto_da_pergunta_sem_as_classes(self):
        self.abrir(TELA_SEM_CLASSES)
        termos = ler_termos(self.driver)
        self.assertEqual(len(termos), 1)
        self.assertIn('Frete CIF Itabira', termos[0]['texto'])
        self.assertEqual(aceitar_termos(self.driver), 1)
        self.assertEqual(self.marcados(), ['t1a'])   # nunca o Não

    def test_pagina_sem_termos(self):
        self.abrir('<select id="participation"></select><p>Termos de uso</p>')
        self.assertEqual(ler_termos(self.driver), [])


class Robo:
    config = {"SMTP_USER": "robo@teste", "SMTP_PASSWORD": "x", "emails_relatorio": "Compras@Teste.com, "}


class AvisoTest(unittest.TestCase):
    TERMOS = [{"texto": "O frete será de responsabilidade do fornecedor.", "aceito": False},
              {"texto": "Endereço de entrega: Rua Piauí, 1815", "aceito": False}]

    def test_anota_so_quando_tem_termo(self):
        r = Robo()
        self.assertFalse(A.anotar(r, "1", [], "T"))
        self.assertTrue(A.anotar(r, "312674", self.TERMOS, "NEGOCIAÇÃO - RC 22100995", True))
        self.assertEqual(list(r.eventos_com_termos), ["312674"])

    def test_situacoes(self):
        r = Robo()
        A.anotar(r, "1", self.TERMOS); A.concluir(r, "1", {"itens": []})
        A.anotar(r, "2", [{"texto": "x", "aceito": True}]); A.concluir(r, "2", {"itens": []})
        A.anotar(r, "3", self.TERMOS); A.concluir(r, "3", "SKIPPED")
        A.anotar(r, "4", self.TERMOS); A.concluir(r, "4", "ENCERRADO")
        A.anotar(r, "5", self.TERMOS); A.concluir(r, "5", None)
        A.concluir(r, "999", {"itens": []})   # evento sem termos: nada muda
        s = {n: e["situacao"] for n, e in r.eventos_com_termos.items()}
        self.assertEqual(s, {"1": A.SITUACAO_ACEITOS, "2": A.SITUACAO_JA_ACEITOS, "3": A.SITUACAO_NAO_ACEITOS,
                             "4": A.SITUACAO_ENCERRADO, "5": A.SITUACAO_SEM_RESULTADO})

    def test_conteudo_do_email(self):
        r = Robo()
        A.anotar(r, "312674", self.TERMOS, "NEGOCIAÇÃO - RC 22100995", True); A.concluir(r, "312674", {"itens": []})
        A.anotar(r, "312700", self.TERMOS, "Cotação X"); A.concluir(r, "312700", "SKIPPED")
        assunto, texto, corpo = A.gerar_conteudo("Vale Base Metals", list(r.eventos_com_termos.values()))
        self.assertIn("2 evento(s) com Termos e condições", assunto)
        self.assertIn("1 precisa(m) de atenção", assunto)
        for trecho in ("Evento 312674 (NEGOCIAÇÃO)", "O frete será de responsabilidade do fornecedor.",
                       "Endereço de entrega: Rua Piauí, 1815", A.SITUACAO_ACEITOS, A.SITUACAO_NAO_ACEITOS):
            self.assertIn(trecho, texto)
        self.assertIn("Rua Piauí", corpo)

    def test_envia_para_vendas7_e_emails_do_config_e_limpa_a_lista(self):
        r = Robo()
        A.anotar(r, "312674", self.TERMOS, "T", True); A.concluir(r, "312674", {"itens": []})
        with patch("bots.avisos.relatorio_email._conectar_e_enviar_smtp", return_value=True) as smtp:
            self.assertTrue(A.enviar(r, "Coupa"))
        remetente, _senha, destinatarios, msg, _bot, _log = smtp.call_args[0]
        self.assertEqual(destinatarios, ["compras@teste.com", A.DESTINATARIO_FIXO])
        self.assertIn("Termos e condições", msg["Subject"])
        self.assertEqual(r.eventos_com_termos, {})
        with patch("bots.avisos.relatorio_email._conectar_e_enviar_smtp") as smtp:
            self.assertFalse(A.enviar(r, "Coupa"))   # lista vazia: não manda nada
            smtp.assert_not_called()

    def test_sem_smtp_nao_quebra(self):
        r = Robo(); r.config = {}
        A.anotar(r, "1", self.TERMOS)
        with patch.dict(os.environ, {"SMTP_USER": "", "SMTP_PASSWORD": ""}), \
                patch("bots.avisos.relatorio_email._conectar_e_enviar_smtp") as smtp:
            self.assertFalse(A.enviar(r, "Coupa"))
            smtp.assert_not_called()


if __name__ == '__main__':
    unittest.main()
