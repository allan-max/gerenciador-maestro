"""Senhas das contas do portal e validação do que chega do navegador antes de virar comando no servidor.

Os comandos vêm do portal (Node) já autenticados, mas o conteúdo é digitado pelo usuário: aqui ele é conferido
antes de entrar num VBScript, numa linha de comando ou no banco de usuários.
"""
import base64
import hashlib
import hmac
import os
import re

PREFIXO_HASH = 'pbkdf2_sha256'
ITERACOES = 260000
SENHA_MIN = 4   # o mesmo piso da tela de nova senha do portal (index.html)


def gerar_hash(senha):
    sal = os.urandom(16)
    dk = hashlib.pbkdf2_hmac('sha256', senha.encode('utf-8'), sal, ITERACOES)
    return '$'.join((PREFIXO_HASH, str(ITERACOES), base64.b64encode(sal).decode(), base64.b64encode(dk).decode()))


def eh_hash(valor):
    return isinstance(valor, str) and valor.startswith(PREFIXO_HASH + '$')


def conferir_senha(guardada, digitada):
    """True se a senha confere. Aceita o hash novo e o texto puro antigo (que o login troca pelo hash)."""
    if not isinstance(digitada, str) or not digitada or guardada in (None, ''):
        return False
    if eh_hash(guardada):
        try:
            _, iteracoes, sal, dk = guardada.split('$')
            calculado = hashlib.pbkdf2_hmac('sha256', digitada.encode('utf-8'), base64.b64decode(sal), int(iteracoes))
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(calculado, base64.b64decode(dk))
    return hmac.compare_digest(str(guardada).encode('utf-8'), digitada.encode('utf-8'))


def senha_valida(senha):
    return isinstance(senha, str) and len(senha) >= SENHA_MIN


EMAIL_VENTURA = re.compile(r'^[a-z0-9._%+-]+@venturainformatica\.com\.br$')


def email_ventura(email):
    return bool(EMAIL_VENTURA.match(email or ''))


# Painel dev: tudo menos senha e tokens
CAMPOS_PAINEL_DEV = ('verificado', 'admin', 'dev', 'dashboard_access', 'planilha_access', 'portais_access',
                     'bloqueado', 'vendedor', 'ultima_localizacao', 'ultimo_login')


def usuarios_sem_segredos(usuarios, online=()):
    return {email: {**{k: info[k] for k in CAMPOS_PAINEL_DEV if k in info}, 'online': email in online}
            for email, info in usuarios.items()}


# Ariba: botões de empresa do portal mandam "1 2 3"
PRIORIDADES_ARIBA = re.compile(r'^[0-9]+([ ,][0-9]+)*$')
# Findes: números de evento digitados à mão ("123/2026", "PE-45")
EVENTO_FINDES = re.compile(r'^[A-Za-z0-9._/-]{1,60}$')


def prioridades_ariba(texto):
    texto = ' '.join(str(texto or '').split()) or '1'
    if len(texto) > 60 or not PRIORIDADES_ARIBA.match(texto):
        raise ValueError('Prioridades inválidas: use só números separados por espaço.')
    return texto


def eventos_findes(texto):
    eventos = [e.strip() for e in str(texto or '').split(',') if e.strip()]
    invalidos = [e for e in eventos if not EVENTO_FINDES.match(e)]
    if invalidos:
        raise ValueError('Evento inválido: use letras, números, ".", "/", "-" ou "_".')
    return eventos


def texto_vbs(texto):
    """Conteúdo seguro entre aspas num VBScript (aspas dobradas; quebra de linha encerraria a string)."""
    texto = str(texto)
    if '\r' in texto or '\n' in texto:
        raise ValueError('Quebra de linha não é permitida.')
    return texto.replace('"', '""')


NOME_IMPRESSORA = re.compile(r'^[A-Za-z0-9 ._()\\-]{1,100}$')


def nome_impressora(nome):
    if not isinstance(nome, str) or not NOME_IMPRESSORA.match(nome):
        raise ValueError('Nome de impressora inválido.')
    return nome
