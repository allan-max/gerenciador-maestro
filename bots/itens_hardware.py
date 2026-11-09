"""Eventos de informática/hardware/TV/tecnologia (Coupa e Vale): vão para Words próprios, no máximo 3 eventos por
arquivo, com marcas/modelos parecidos juntos e a lista de fornecedores de hardware no final do arquivo.
Um item é de hardware quando o classificador de tecnologia confirmou o segmento (ONDE ENCONTRAR?)."""
import re

EVENTOS_POR_WORD_HARDWARE = 3

TITULO_MENSAGEM_HARDWARE = "Hardware"
FORNECEDORES_HARDWARE = [
    "Agis", "Mazer", "Pauta", "SND", "Fagundez", "GMI", "Scansource", "Ingrammicro", "Martins", "Gazin",
    "Fujioka", "Golden", "Allnations", "Mais proxima", "Rcell", "Gibson", "Tambasa", "BELMICRO", "COLEÇAO",
    "ibyte", "miranda",
]


def item_e_hardware(item):
    return bool((item or {}).get('onde_encontrar_info'))


def evento_tem_hardware(evento):
    return any(item_e_hardware(it) for it in (evento or {}).get('itens', []) or [])


def _norm(texto):
    return re.sub(r'\s+', ' ', str(texto or '')).strip().lower()


def chave_semelhanca(evento):
    """Ordena para marcas e modelos parecidos ficarem no mesmo arquivo: categoria, marca, produto buscado."""
    for it in (evento or {}).get('itens', []) or []:
        info = (it or {}).get('onde_encontrar_info')
        if info:
            return (_norm(info.get('categoria')), _norm(info.get('marca')), _norm(info.get('termo_busca')),
                    _norm(it.get('descricao')))
    return ('~', '', '', '')


def lotes_hardware(eventos):
    ordenados = sorted(eventos, key=chave_semelhanca)
    return [ordenados[i:i + EVENTOS_POR_WORD_HARDWARE] for i in range(0, len(ordenados), EVENTOS_POR_WORD_HARDWARE)]


def adicionar_mensagem_hardware(doc):
    """Bloco no final do Word: separador, "Hardware" em negrito e os fornecedores numa linha."""
    from docx.shared import Pt

    p_linha = doc.add_paragraph("_" * 100)
    p_linha.paragraph_format.space_before = Pt(10)
    titulo = doc.add_paragraph()
    titulo.paragraph_format.space_before = Pt(6)
    titulo.add_run(TITULO_MENSAGEM_HARDWARE).bold = True
    doc.add_paragraph(", ".join(FORNECEDORES_HARDWARE))
