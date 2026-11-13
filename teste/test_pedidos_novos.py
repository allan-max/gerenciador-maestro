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


class ExtrairPedidosFonteTest(unittest.TestCase):
    """O fluxo Selenium não roda aqui: confere no fonte que ele não baixa PDF nem grava na planilha."""

    def setUp(self):
        self.pedidos = (RAIZ / 'bots' / 'coupa' / 'mixins' / 'pedidos.py').read_text(encoding='utf-8')
        self.principal = (RAIZ / 'bots' / 'coupa' / 'principal.py').read_text(encoding='utf-8')

    def test_nao_baixa_pdf_nem_grava_planilha(self):
        for proibido in ('print_page', 'PEDIDOS\\', 'caminho_pdf', 'atualizar_pedidos_excel', 'salvar_pedido_database'):
            self.assertNotIn(proibido, self.pedidos)

    def test_usa_modulo_pedidos_novos(self):
        for nome in ('numeros_planilha', 'registrar_lido', 'gravar_fora_planilha', 'enviar_email_pedidos_novos'):
            self.assertIn(nome, self.pedidos)

    def test_caminhos_no_banco(self):
        for trecho in ('PEDIDOS-LIDOS-COUPA.json', 'PEDIDOS-FORA-PLANILHA.json', 'pasta_banco()'):
            self.assertIn(trecho, self.principal)


class GerenciadorTest(unittest.TestCase):
    def test_gerenciador_encaminha_extrair_pedidos(self):
        import re
        fonte = (RAIZ / 'gerenciador.py').read_text(encoding='utf-8')
        listas = re.findall(r"elif modo in \[([^\]]*)\]:", fonte)
        self.assertTrue(any("'extrair_pedidos'" in l and "'extrair'" in l and "'responder'" in l for l in listas))


class ExtrairPedidosFimTest(unittest.TestCase):
    """Sem navegador: o login falha logo no início e só o fim da extração roda."""

    def setUp(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from bots.coupa.mixins.pedidos import PedidosMixin
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.planilha, self.lidos, self.fora = d / 'PEDIDOS.json', d / 'PEDIDOS-LIDOS-COUPA.json', d / 'PEDIDOS-FORA-PLANILHA.json'
        self.bot = type('Bot', (PedidosMixin,), {})()
        self.bot.ARQUIVO_PLANILHA_PEDIDOS, self.bot.ARQUIVO_PEDIDOS_LIDOS = str(self.planilha), str(self.lidos)
        self.bot.ARQUIVO_PEDIDOS_FORA_PLANILHA = str(self.fora)
        self.bot.BASE_SUPPLIER_URL, self.bot.config, self.bot.sio = 'https://x', {}, MagicMock()
        self.bot.garantir_logado = MagicMock(side_effect=RuntimeError('login caiu'))

    def tearDown(self):
        self.tmp.cleanup()

    def conclusao(self):
        return self.bot.sio.emit.call_args.args[1]

    def test_erro_no_meio_ainda_grava_fora_planilha(self):
        self.planilha.write_text(json.dumps({'PEDIDOS': [{'PEDIDO': '2'}]}), encoding='utf-8')
        self.lidos.write_text(json.dumps([{'pedido': '1'}, {'pedido': '2'}]), encoding='utf-8')
        with patch('bots.coupa.mixins.pedidos.enviar_email_pedidos_novos', return_value=False) as enviar:
            self.assertEqual(self.bot.extrair_pedidos(), [])
        self.assertEqual(json.loads(self.fora.read_text(encoding='utf-8')), [{'pedido': '1'}])
        enviar.assert_called_once()
        c = self.conclusao()
        self.assertEqual((c['evento'], c['sucesso'], c['total'], c['erro']), ('Listagem de Pedidos', False, 0, 'login caiu'))

    def test_planilha_ilegivel_aborta_sem_email(self):
        with patch('bots.coupa.mixins.pedidos.enviar_email_pedidos_novos') as enviar, patch.object(pn.time, 'sleep'):
            self.assertEqual(self.bot.extrair_pedidos(), [])
        enviar.assert_not_called()
        self.bot.garantir_logado.assert_not_called()
        self.assertFalse(self.conclusao()['sucesso'])
        self.assertFalse(self.fora.exists())


if __name__ == '__main__':
    unittest.main()
