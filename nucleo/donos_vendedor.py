"""Dono de uma cotação pelo texto da coluna VENDEDOR (Minhas Cotações).

A coluna pode ter só o primeiro nome ("GUSTAVO", cotações antigas) ou o nome completo ("Gustavo Cabral", atribuições
novas). Uma célula pertence a uma conta quando o primeiro nome é igual e os outros nomes da célula aparecem, na mesma
ordem, no nome completo da conta ("Gustavo Cabral" -> "Gustavo Cabral Calvi"). Se bater com duas pessoas diferentes
(dois Gustavos e a célula só diz "GUSTAVO"), a cotação não vai para ninguém. Contas da mesma pessoa (mesmo nome
completo, ou contas antigas sem nome com o mesmo vendedor) contam como uma pessoa só.

Mesma regra em portaismaestro/lib/dono-cotacao.js (cotações da Planilha Anmyna, lidas pelo portal).
"""
import re
import unicodedata

SEPARADORES_CELULA = re.compile(r'[/,;+]')


def _tokens(texto):
    sem_acento = unicodedata.normalize('NFD', str(texto or '')).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^A-Z0-9]+', ' ', sem_acento.upper()).split()


def _subsequencia(curta, longa):
    it = iter(longa)
    return all(any(t == x for x in it) for t in curta)


def _casa(tokens_celula, nome_conta):
    t = _tokens(nome_conta)
    return bool(t and tokens_celula and tokens_celula[0] == t[0] and _subsequencia(tokens_celula[1:], t[1:]))


def contas_vendedoras(usuarios):
    """Contas que recebem cotações: com vendedor vinculado e não bloqueadas."""
    return [{'email': email, 'nome': str(info.get('nome') or ''), 'vendedor': str(info.get('vendedor') or '')}
            for email, info in (usuarios or {}).items()
            if isinstance(info, dict) and str(info.get('vendedor') or '').strip() and not info.get('bloqueado')]


def _pessoa(conta):
    return ' '.join(_tokens(conta.get('nome') or conta.get('vendedor')))


def donos_da_cotacao(celula, contas):
    """E-mails das contas donas da cotação (vazio se ninguém, ou se o nome for ambíguo)."""
    donos = set()
    for parte in SEPARADORES_CELULA.split(str(celula or '')):
        tokens = _tokens(parte)
        if not tokens:
            continue
        achadas = [c for c in contas if _casa(tokens, c.get('nome')) or _casa(tokens, c.get('vendedor'))]
        if achadas and len({_pessoa(c) for c in achadas}) == 1:
            donos.update(c['email'] for c in achadas)
    return donos
