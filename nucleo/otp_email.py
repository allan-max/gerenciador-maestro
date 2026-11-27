"""Código OTP da Vale lido direto da caixa de e-mail (IMAP da Locaweb), para a verificação diária rodar sem ninguém no painel.

O Coupa da Vale Base Metals manda "Seu código de verificação de login da Coupa" para vendas@. Só leitura: a caixa é aberta
em modo somente leitura, nada é apagado nem marcado como lido. Sem LOCAL_WEB_EMAIL/LOCAL_WEB_SENHA no .env, fica desligado
e o código continua sendo digitado no painel.
"""
import email
import email.header
import email.utils
import imaplib
import os
import re
from datetime import timedelta

IMAP_HOST = 'imap.email-ssl.com.br'
IMAP_PORTA = 993
REMETENTE = 'do_not_reply@valebasemetals.coupahost.com'
ASSUNTO = 'código de verificação'    # o mesmo remetente também manda lembretes de pedido de compra
FOLGA_RELOGIO = timedelta(minutes=2)  # relógio do Coupa x do servidor; o código vale 15 min
CODIGO = re.compile(r'^\s*(\d{6})\s*$', re.MULTILINE)
MESES = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')   # IMAP: em inglês, nunca pelo locale


def configurado():
    return bool(os.environ.get('LOCAL_WEB_EMAIL') and os.environ.get('LOCAL_WEB_SENHA'))


def _texto(valor):
    return str(email.header.make_header(email.header.decode_header(valor or '')))


def extrair_codigo(msg):
    """Código de 6 dígitos da parte de texto (no e-mail ele fica sozinho numa linha), ou None."""
    for parte in msg.walk():
        if parte.get_content_type() == 'text/plain':
            texto = parte.get_payload(decode=True).decode(parte.get_content_charset() or 'utf-8', errors='ignore')
            achado = CODIGO.search(texto)
            if achado:
                return achado.group(1)
    return None


def codigo_da_mensagem(bruto, desde):
    """Código se a mensagem for um OTP do Coupa da Vale enviado depois de `desde` (datetime com fuso)."""
    msg = email.message_from_bytes(bruto)
    if REMETENTE not in (email.utils.parseaddr(msg.get('From', ''))[1] or '').lower():
        return None
    if ASSUNTO not in _texto(msg.get('Subject')).lower():
        return None
    try:
        enviado = email.utils.parsedate_to_datetime(msg.get('Date'))
    except (TypeError, ValueError):
        return None
    if enviado is None or enviado.tzinfo is None or enviado < desde - FOLGA_RELOGIO:
        return None    # código antigo: pode ser de outro pedido, já usado ou expirado
    return extrair_codigo(msg)


def buscar_codigo(desde, conectar=None):
    """Código do OTP mais novo enviado depois de `desde`, ou None. Erros de rede/senha sobem para quem chamou."""
    if not configurado():
        return None
    caixa = (conectar or (lambda: imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORTA, timeout=20)))()
    try:
        caixa.login(os.environ['LOCAL_WEB_EMAIL'], os.environ['LOCAL_WEB_SENHA'])
        caixa.select('INBOX', readonly=True)
        d = desde - FOLGA_RELOGIO
        dia = f'{d.day:02d}-{MESES[d.month - 1]}-{d.year}'   # o filtro roda no servidor: não baixa a caixa inteira
        _, achados = caixa.search(None, f'(FROM "{REMETENTE}" SINCE {dia})')
        for num in reversed(achados[0].split()):
            _, dados = caixa.fetch(num, '(BODY.PEEK[])')       # PEEK: não marca como lido
            codigo = codigo_da_mensagem(dados[0][1], desde)
            if codigo:
                return codigo
        return None
    finally:
        try:
            caixa.logout()
        except Exception:
            pass
