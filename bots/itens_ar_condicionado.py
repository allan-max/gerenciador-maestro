# -*- coding: utf-8 -*-
"""Eventos de ar-condicionado (Coupa e Vale): Words próprios com no máximo 3 eventos, produtos parecidos juntos,
e no final a lista de distribuidores e fabricantes de ar-condicionado com a regra de compra."""
import re
import unicodedata

EVENTOS_POR_WORD_AR = 3

TITULO_MENSAGEM_AR = "PRINCIPAIS FORNECEDORES DE AR-CONDICIONADO"
CABECALHO_AR = ("Distribuidores – Marcas diversas", "Fabricantes")
DISTRIBUIDORES_AR = ["Friovix", "Frigelar", "Dufrio", "Climario", "Martins", "Fujioka", "Tambasa"]
FABRICANTES_AR = ["Elgin", "Philco", "Midea / Carrier", "Electrolux", "Consul", "Agratto"]
TITULO_REGRA_AR = "REGRA DE COMPRA"
REGRA_COMPRA_AR = [
    "- Compras em quantidade: cotar diretamente com o fabricante, que costuma oferecer preços mais competitivos.",
    "- Ao cotar com o fabricante, sempre conferir: pedido mínimo e valor do frete.",
]

# Comparado sem acento e em minúsculas. "split" sozinho não basta: "split pin" (contrapino) é peça mecânica.
_PRODUTOS = [
    r'\bar[ -]?condicionados?\b', r'\bcondicionador(es)? de ar\b', r'\b\d{1,3}[.]?\d{3} ?btus?(/h)?\b',
    r'\bhi[ -]?wall\b', r'\bsplit\b[^.;]{0,40}\b(inverter|btu|hi[ -]?wall|piso[ -]teto|cassete)\b',
    r'\b(inverter|piso[ -]teto|cassete)\b[^.;]{0,40}\bsplit\b',
]
_REGEX = re.compile('|'.join(_PRODUTOS))
_CAMPOS = ('titulo', 'descricao', 'texto_item', 'texto_compra')


def _sem_acento(texto):
    texto = unicodedata.normalize('NFKD', str(texto or '').lower())
    return ''.join(c for c in texto if not unicodedata.combining(c))


def _texto(item):
    return _sem_acento(' '.join(str((item or {}).get(k, '') or '') for k in _CAMPOS))


def item_e_ar(item):
    return bool(_REGEX.search(_texto(item)))


def evento_tem_ar(evento):
    return any(item_e_ar(it) for it in (evento or {}).get('itens', []) or [])


def _chave(evento):
    """Junta os parecidos: mesma capacidade (BTUs); empate pelo número do evento."""
    for it in (evento or {}).get('itens', []) or []:
        if item_e_ar(it):
            btu = re.search(r'(\d{1,3})[.]?(\d{3}) ?btu', _texto(it))
            return (int(btu.group(1) + btu.group(2)) if btu else 0, str(evento.get('numero_evento', '')))
    return (0, str((evento or {}).get('numero_evento', '')))


def lotes_ar(eventos):
    ordenados = sorted(eventos or [], key=_chave)
    return [ordenados[i:i + EVENTOS_POR_WORD_AR] for i in range(0, len(ordenados), EVENTOS_POR_WORD_AR)]


def adicionar_mensagem_ar(doc):
    """Bloco no final do Word: título, tabela Distribuidores | Fabricantes e a regra de compra."""
    from docx.shared import Pt

    p_linha = doc.add_paragraph("_" * 100)
    p_linha.paragraph_format.space_before = Pt(10)
    titulo = doc.add_paragraph()
    titulo.paragraph_format.space_before = Pt(6)
    titulo.add_run(TITULO_MENSAGEM_AR).bold = True

    linhas = max(len(DISTRIBUIDORES_AR), len(FABRICANTES_AR))
    tabela = doc.add_table(rows=linhas + 1, cols=2)
    for c, texto in enumerate(CABECALHO_AR):
        tabela.cell(0, c).paragraphs[0].add_run(texto).bold = True
    for i in range(linhas):
        tabela.cell(i + 1, 0).text = DISTRIBUIDORES_AR[i] if i < len(DISTRIBUIDORES_AR) else ""
        tabela.cell(i + 1, 1).text = FABRICANTES_AR[i] if i < len(FABRICANTES_AR) else ""

    regra = doc.add_paragraph()
    regra.paragraph_format.space_before = Pt(6)
    regra.add_run(TITULO_REGRA_AR).bold = True
    for linha in REGRA_COMPRA_AR:
        doc.add_paragraph(linha)
