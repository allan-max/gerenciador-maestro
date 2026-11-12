# -*- coding: utf-8 -*-
"""Eventos de eletroportáteis (Coupa e Vale): grill, mixer, cafeteira, sanduicheira, garrafa térmica, liquidificador,
air fryer, fritadeira, batedeira, multiprocessador, panela elétrica, chaleira, fogão/cooktop elétrico portátil e
semelhantes. Como a orientação de compra é longa, vão para Words próprios com no máximo 3 eventos, produtos
parecidos juntos, e a orientação de onde orçar no final do arquivo."""
import re
import unicodedata

EVENTOS_POR_WORD_ELETRO = 3

TITULO_MENSAGEM_ELETRO = "Principais fornecedores que trabalham com eletroportáteis"
LOCAIS_ELETRO = ["Martins", "Tambasa", "FastShop", "Casa e Video", "Fujioka", "Bartofil", "Gazin", "Friopeça", "Belmicro"]
REGRA_COMPRA_ELETRO = [
    "Compra em quantidade: cotar direto com o fabricante, que costuma ter o preço mais competitivo.",
    "No fabricante, sempre conferir: o pedido mínimo e o frete.",
]

# Comparado sem acento e em minúsculas. "garrafa" só térmica: garrafa de gás/oxigênio/PET não é eletroportátil.
_PRODUTOS = [
    r'\bgrill\b', r'\bmixer\b', r'\bmisteira\b', r'\bcaf[ae]i?teira\b', r'\bsanduicheira\b', r'\bgarrafa termica\b',
    r'\bliqu[ia]?di?ficador\b', r'\bliquificador\b', r'\bair ?fr[yi]e[rd]\b', r'\bfritadeira\b', r'\bbatedeira\b',
    r'\bmultiprocessador\b', r'\bprocessador de alimentos\b', r'\bpanela eletrica\b', r'\bpanela de (arroz|pressao) eletrica\b',
    r'\bchaleira\b', r'\bfogao eletrico\b', r'\bfogareiro eletrico\b', r'\bcooktop (eletrico|portatil)\b',
    r'\btorradeira\b', r'\bespremedor (de frutas|de citricos|eletrico)\b', r'\bpipoqueira eletrica\b',
    r'\bforno eletrico\b', r'\bmini ?forno\b', r'\bsorveteira\b', r'\biogurteira\b', r'\bomeleteira\b',
    r'\bwaffle\b', r'\bcuscuzeira eletrica\b',
]
_REGEX = re.compile('|'.join(_PRODUTOS))
_CAMPOS = ('titulo', 'descricao', 'texto_item', 'texto_compra')


def _sem_acento(texto):
    texto = unicodedata.normalize('NFKD', str(texto or '').lower())
    return ''.join(c for c in texto if not unicodedata.combining(c))


def produto_eletro(item):
    """Nome do eletroportátil achado no item ("cafeteira", "air fryer"...) ou '' se não for."""
    m = _REGEX.search(_sem_acento(' '.join(str((item or {}).get(k, '') or '') for k in _CAMPOS)))
    return m.group(0) if m else ''


def item_e_eletro(item):
    return bool(produto_eletro(item))


def evento_tem_eletro(evento):
    return any(item_e_eletro(it) for it in (evento or {}).get('itens', []) or [])


def _chave(evento):
    for it in (evento or {}).get('itens', []) or []:
        p = produto_eletro(it)
        if p:
            return (p.replace(' ', ''), str((evento or {}).get('numero_evento', '')))
    return ('', str((evento or {}).get('numero_evento', '')))


def lotes_eletro(eventos):
    """Eventos ordenados por produto (cafeteira com cafeteira...) em lotes de no máximo 3 por Word."""
    ordenados = sorted(eventos or [], key=_chave)
    return [ordenados[i:i + EVENTOS_POR_WORD_ELETRO] for i in range(0, len(ordenados), EVENTOS_POR_WORD_ELETRO)]


def linhas_mensagem_eletro():
    return ["Locais para orçar:"] + LOCAIS_ELETRO + ["", "Regra de compra:"] + REGRA_COMPRA_ELETRO


def adicionar_mensagem_eletro(doc):
    """Bloco no final do Word: separador, título em negrito, locais para orçar e regra de compra."""
    from docx.shared import Pt

    p_linha = doc.add_paragraph("_" * 100)
    p_linha.paragraph_format.space_before = Pt(10)
    titulo = doc.add_paragraph()
    titulo.paragraph_format.space_before = Pt(6)
    titulo.add_run(TITULO_MENSAGEM_ELETRO).bold = True
    for linha in linhas_mensagem_eletro():
        p = doc.add_paragraph()
        r = p.add_run(linha)
        if linha in ("Locais para orçar:", "Regra de compra:"):
            r.bold = True
