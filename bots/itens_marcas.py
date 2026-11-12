"""Marcas com fornecedores próprios (Coupa e Vale): os eventos dessas marcas vão para o final do Word, com produtos
parecidos juntos, e logo depois dos eventos de cada marca entra a lista de onde cotar aquela marca.

Para incluir outra marca, acrescente uma entrada em MARCAS (a ordem da lista é a ordem no final do Word)."""
import re

MARCAS = [
    {
        "nome": "Intelbras",
        "regex": re.compile(r'\bintelbras\b', re.IGNORECASE),
        "titulo": "Itens Intelbras",
        "linhas": ["Sudeste", "Router", "Linhavix", "Mazer", "Plantec"],
    },
    {
        "nome": "Logitech",
        "regex": re.compile(r'\blogitech\b', re.IGNORECASE),
        "titulo": "Principais fornecedores que trabalham com a marca Logitech",
        "linhas": ["Agis", "Mazer", "Pauta", "SND", "Fagundez", "GMI", "Port", "Scansource", "Ingrammicro",
                   "Martins", "Gazin", "Fujioka", "Golden", "Allnations", "Gibson", "COLEÇAO"],
    },
]
_POR_NOME = {m["nome"]: m for m in MARCAS}

_CAMPOS_TEXTO = ('titulo', 'descricao', 'texto_item', 'texto_compra', 'part_number', 'marca')


def item_da_marca(item, marca):
    """Marca extraída pela IA ou o nome da marca escrito no título/descrição/textos do item."""
    regex = _POR_NOME[marca]["regex"]
    if regex.search(str(((item or {}).get('dados_ia') or {}).get('marca', '') or '')):
        return True
    return bool(regex.search(' '.join(str((item or {}).get(k, '') or '') for k in _CAMPOS_TEXTO)))


def marca_do_evento(evento):
    """Primeira marca de MARCAS com algum item no evento; None se não tiver nenhuma."""
    itens = (evento or {}).get('itens', []) or []
    for m in MARCAS:
        if any(item_da_marca(it, m["nome"]) for it in itens):
            return m["nome"]
    return None


def _chave_produto(evento, marca):
    """Produto da marca no evento (modelo da IA, senão título/descrição do item) para juntar os parecidos."""
    for it in (evento or {}).get('itens', []) or []:
        if item_da_marca(it, marca):
            ia = (it or {}).get('dados_ia') or {}
            produto = str(ia.get('modelo') or ia.get('part_number') or it.get('titulo') or '').strip().lower()
            if produto in ('', 'não', 'nao'):
                produto = str(it.get('descricao', '') or '').strip().lower()
            return (produto, str(evento.get('numero_evento', '')))
    return ('', str((evento or {}).get('numero_evento', '')))


def marcas_no_final(eventos):
    """Mesma lista: eventos sem marca primeiro (ordem mantida), depois cada marca de MARCAS agrupada por produto."""
    eventos = list(eventos or [])
    saida = [e for e in eventos if marca_do_evento(e) is None]
    for m in MARCAS:
        saida += sorted((e for e in eventos if marca_do_evento(e) == m["nome"]), key=lambda e: _chave_produto(e, m["nome"]))
    return saida


def mensagem_depois_do_evento(eventos, i):
    """Nome da marca cuja lista de fornecedores entra depois do evento i (último evento daquela marca); senão None."""
    marca = marca_do_evento(eventos[i])
    if marca is None:
        return None
    if i + 1 < len(eventos) and marca_do_evento(eventos[i + 1]) == marca:
        return None
    return marca


def adicionar_mensagem_marca(doc, marca):
    """Bloco no Word: separador, título em negrito e onde cotar a marca, um por linha."""
    from docx.shared import Pt

    m = _POR_NOME[marca]
    p_linha = doc.add_paragraph("_" * 100)
    p_linha.paragraph_format.space_before = Pt(10)
    titulo = doc.add_paragraph()
    titulo.paragraph_format.space_before = Pt(6)
    titulo.add_run(m["titulo"]).bold = True
    for linha in m["linhas"]:
        doc.add_paragraph(linha)
