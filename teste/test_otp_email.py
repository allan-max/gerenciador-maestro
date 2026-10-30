"""OTP da Vale lido do e-mail (otp_email.py): só o código do Coupa da Vale, enviado depois do pedido, sem mexer na caixa."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header
from email.utils import format_datetime
from unittest.mock import patch

import otp_email

PEDIDO = datetime(2026, 10, 20, 10, 58, tzinfo=timezone(timedelta(hours=-3)))
ASSUNTO_OTP = 'Seu código de verificação de login da Coupa'


def mensagem(codigo='453022', assunto=ASSUNTO_OTP, de='Vale Base Metals <do_not_reply@valebasemetals.coupahost.com>',
             enviado=PEDIDO + timedelta(seconds=20)):
    """Mesmo formato do e-mail real (multipart, quoted-printable, assunto codificado), sem dados de pessoas."""
    msg = MIMEMultipart('alternative')
    msg['From'], msg['To'] = de, 'VENDAS@EXEMPLO.COM.BR'
    msg['Subject'] = Header(assunto, 'utf-8')
    msg['Date'] = format_datetime(enviado.astimezone(timezone.utc))
    texto = (f'Seu código de verificação de login da Coupa\n\nOlá, FULANO\n\nPara terminar a autenticação com a Coupa, '
             f'insira o\ncódigo a seguir ou clique no botão abaixo.\n\n{codigo}\n\nSeu código expirará em 15 minutos.\n')
    msg.attach(MIMEText(texto, 'plain', 'utf-8'))
    msg.attach(MIMEText(f'<p><b>{codigo}</b></p><p>Pedido 4513660992</p>', 'html', 'utf-8'))
    return msg.as_bytes()


class MensagemTest(unittest.TestCase):
    def test_codigo_do_coupa_da_vale_depois_do_pedido(self):
        self.assertEqual(otp_email.codigo_da_mensagem(mensagem(), PEDIDO), '453022')

    def test_ignora_lembrete_outro_remetente_e_codigo_antigo(self):
        lembrete = mensagem(assunto='Lembrete para confirmar o pedido de compra de Vale Base Metals nº 4513660992')
        self.assertIsNone(otp_email.codigo_da_mensagem(lembrete, PEDIDO))
        self.assertIsNone(otp_email.codigo_da_mensagem(mensagem(de='alguem@outro.com'), PEDIDO))
        self.assertIsNone(otp_email.codigo_da_mensagem(mensagem(enviado=PEDIDO - timedelta(minutes=10)), PEDIDO))
        # relógio do Coupa um pouco atrás do servidor ainda vale
        self.assertEqual(otp_email.codigo_da_mensagem(mensagem(enviado=PEDIDO - timedelta(seconds=60)), PEDIDO), '453022')


class CaixaFalsa:
    def __init__(self, mensagens):
        self.mensagens, self.chamadas = mensagens, []

    def login(self, usuario, senha):
        self.chamadas.append(('login', usuario))

    def select(self, pasta, readonly=False):
        self.chamadas.append(('select', pasta, readonly))

    def search(self, charset, criterio):
        self.chamadas.append(('search', criterio))
        return 'OK', [b' '.join(str(i + 1).encode() for i in range(len(self.mensagens)))]

    def fetch(self, num, partes):
        self.chamadas.append(('fetch', partes))
        return 'OK', [(b'', self.mensagens[int(num) - 1])]

    def logout(self):
        self.chamadas.append(('logout',))


class BuscarTest(unittest.TestCase):
    def setUp(self):
        alvo = patch.dict(os.environ, LOCAL_WEB_EMAIL='vendas@exemplo.com.br', LOCAL_WEB_SENHA='x')
        alvo.start()
        self.addCleanup(alvo.stop)

    def test_pega_o_mais_novo_sem_marcar_como_lido(self):
        caixa = CaixaFalsa([mensagem('111111', enviado=PEDIDO - timedelta(hours=1)),
                            mensagem('222222'),
                            mensagem('999999', assunto='Lembrete para confirmar o pedido de compra')])
        self.assertEqual(otp_email.buscar_codigo(PEDIDO, conectar=lambda: caixa), '222222')
        self.assertIn(('select', 'INBOX', True), caixa.chamadas)                       # somente leitura
        self.assertTrue(all(c[1] == '(BODY.PEEK[])' for c in caixa.chamadas if c[0] == 'fetch'))
        criterio = next(c[1] for c in caixa.chamadas if c[0] == 'search')
        self.assertEqual(criterio, '(FROM "do_not_reply@valebasemetals.coupahost.com" SINCE 20-Oct-2026)')   # mês em inglês
        self.assertEqual(caixa.chamadas[-1], ('logout',))

    def test_sem_codigo_novo_devolve_none(self):
        caixa = CaixaFalsa([mensagem('111111', enviado=PEDIDO - timedelta(hours=1))])
        self.assertIsNone(otp_email.buscar_codigo(PEDIDO, conectar=lambda: caixa))

    def test_sem_env_nem_conecta(self):
        with patch.dict(os.environ, LOCAL_WEB_SENHA=''):
            self.assertIsNone(otp_email.buscar_codigo(PEDIDO, conectar=lambda: self.fail('não devia conectar')))


if __name__ == '__main__':
    unittest.main()
