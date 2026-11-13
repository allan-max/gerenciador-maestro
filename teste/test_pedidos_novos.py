# -*- coding: utf-8 -*-
"""Pedidos novos do Coupa: JSON dos lidos, fora da planilha e e-mail (docs/superpowers/specs/2026-11-13-pedidos-novos-coupa-design.md)."""
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bots.coupa import pedidos_novos as pn

LOG = logging.getLogger('test_pedidos_novos')
RAIZ = Path(__file__).resolve().parent.parent


class ArquivosTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.planilha = self.dir / 'PEDIDOS.json'
        self.lidos = self.dir / 'PEDIDOS-LIDOS-COUPA.json'
        self.fora = self.dir / 'PEDIDOS-FORA-PLANILHA.json'

    def tearDown(self):
        self.tmp.cleanup()

    def escrever_planilha(self, pedidos):
        self.planilha.write_text(json.dumps({'PEDIDOS': pedidos}), encoding='utf-8')

    def test_numeros_planilha_normaliza(self):
        self.escrever_planilha([{'PEDIDO': ' 4513771955 '}, {'PEDIDO': 4513657837}, None, {'CIDADE': 'x'}])
        self.assertEqual(pn.numeros_planilha(str(self.planilha)), {'4513771955', '4513657837'})

    def test_numeros_planilha_json_quebrado_tenta_de_novo(self):
        self.escrever_planilha([{'PEDIDO': '1'}])
        real = json.load
        falhas = [json.JSONDecodeError('x', 'doc', 0)] * 2

        def load(f, *a, **k):
            if falhas:
                raise falhas.pop()
            return real(f, *a, **k)

        with patch.object(pn.json, 'load', side_effect=load), patch.object(pn.time, 'sleep') as dormir:
            self.assertEqual(pn.numeros_planilha(str(self.planilha)), {'1'})
        self.assertEqual(dormir.call_count, 2)

    def test_numeros_planilha_sempre_quebrado_lanca(self):
        self.planilha.write_text('{"PEDIDOS": [', encoding='utf-8')
        with patch.object(pn.time, 'sleep'):
            with self.assertRaises(pn.ErroLeituraPlanilha):
                pn.numeros_planilha(str(self.planilha))

    def test_numeros_planilha_sem_arquivo_lanca(self):
        with patch.object(pn.time, 'sleep'):
            with self.assertRaises(pn.ErroLeituraPlanilha):
                pn.numeros_planilha(str(self.dir / 'nao-existe.json'))

    def test_carregar_lidos_sem_arquivo(self):
        self.assertEqual(pn.carregar_lidos(str(self.lidos)), [])

    def test_registrar_lido_acrescenta_e_substitui(self):
        pn.registrar_lido(str(self.lidos), {'pedido': '1', 'total': 'a'})
        pn.registrar_lido(str(self.lidos), {'pedido': '2'})
        pn.registrar_lido(str(self.lidos), {'pedido': '1', 'total': 'b'})
        dados = json.loads(self.lidos.read_text(encoding='utf-8'))
        self.assertEqual([p['pedido'] for p in dados], ['1', '2'])
        self.assertEqual(dados[0]['total'], 'b')

    def test_fora_planilha_recalcula(self):
        lidos = [{'pedido': '1'}, {'pedido': '2'}, {'pedido': '3'}]
        fora = pn.gravar_fora_planilha(str(self.fora), lidos, {'2'})
        self.assertEqual([p['pedido'] for p in fora], ['1', '3'])
        self.assertEqual(json.loads(self.fora.read_text(encoding='utf-8')), fora)
        self.assertEqual(pn.gravar_fora_planilha(str(self.fora), lidos, {'1', '2', '3'}), [])
        self.assertEqual(json.loads(self.fora.read_text(encoding='utf-8')), [])


PEDIDO = {'pedido': '4513771955', 'evento': '304622', 'cidade_estado': 'Barão de Cocais MG', 'frete': 'EXW',
          'total': '963,00', 'vendedor': 'MICHELI', 'itens': [{'texto': 'MOCHILA', 'prazo': '27/12/2026'}],
          'alertas': ['abrir chamado no 4513771955 por ser importado.']}
SMTP = {'SMTP_USER': 'robo@x.com', 'SMTP_PASSWORD': 'segredo'}


class EmailTest(unittest.TestCase):
    def test_email_lista_pedidos(self):
        assunto, texto, html = pn.gerar_email_pedidos_novos([PEDIDO])
        self.assertIn('1 pedido', assunto)
        for corpo in (texto, html):
            for trecho in ('4513771955', '304622', 'MOCHILA', 'importado'):
                self.assertIn(trecho, corpo)

    def test_email_escapa_html(self):
        _, _, html = pn.gerar_email_pedidos_novos([dict(PEDIDO, itens=[{'texto': '<b>x</b>', 'prazo': ''}])])
        self.assertNotIn('<b>x</b>', html)

    def test_envio_vazio_nao_envia(self):
        with patch.object(pn, '_conectar_e_enviar_smtp', return_value=True) as enviar:
            self.assertFalse(pn.enviar_email_pedidos_novos([], SMTP, LOG))
        enviar.assert_not_called()

    def test_envio_destino_padrao(self):
        with patch.object(pn, '_conectar_e_enviar_smtp', return_value=True) as enviar:
            self.assertTrue(pn.enviar_email_pedidos_novos([PEDIDO], SMTP, LOG))
        self.assertEqual(enviar.call_args.args[2], ['vendas7@venturainformatica.com.br'])

    def test_envio_destino_configurado(self):
        with patch.object(pn, '_conectar_e_enviar_smtp', return_value=True) as enviar:
            pn.enviar_email_pedidos_novos([PEDIDO], dict(SMTP, emails_pedidos_novos='a@x.com, b@x.com'), LOG)
        self.assertEqual(enviar.call_args.args[2], ['a@x.com', 'b@x.com'])

    def test_envio_sem_smtp_nao_envia(self):
        with patch.dict(os.environ, {'SMTP_USER': '', 'SMTP_PASSWORD': ''}), \
                patch.object(pn, '_conectar_e_enviar_smtp', return_value=True) as enviar:
            self.assertFalse(pn.enviar_email_pedidos_novos([PEDIDO], {}, LOG))
        enviar.assert_not_called()


if __name__ == '__main__':
    unittest.main()
