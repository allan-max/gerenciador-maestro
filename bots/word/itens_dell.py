"""Eventos com itens da marca DELL (Coupa e Vale): vão para Words próprios, 3 eventos por arquivo,
com a orientação de onde cotar Dell no final do arquivo."""
import re

EVENTOS_POR_WORD_DELL = 3

_DELL = re.compile(r'\bdell\b', re.IGNORECASE)

TITULO_MENSAGEM_DELL = "Itens Dell"
LINHAS_MENSAGEM_DELL = [
    "Agis",
    "Ingrammicro",
    "Scansource",
    "Direto com o fabricante Dell(quando for quantidade enviar por e-mail, pegar com a equipe contato atual),",
    "TD synnex( este fornecedor para demandas onde não temos o registro usam a premier page, nela vocês podem gerar "
    "o E-quote para que possamos seguir com a precificação, com a Dell Premier permite consultar produtos, "
    "personalizar configurações e gerar uma eQuote cotação eletrônica)-vendas9@venturainformatica.com.br",
    "https://www.dell.com/premier/home/br/pt/rc1835497",
]
# A senha da Dell Premier NÃO fica no código (o repositório é público): vem da chave abaixo do CONFIG.json
# (fora do Git) e entra no Word logo depois do e-mail. Sem a chave, o Word sai sem a linha da senha.
CHAVE_SENHA_DELL = 'dell_premier_senha'


def linhas_mensagem_dell(config=None):
    linhas = list(LINHAS_MENSAGEM_DELL)
    senha = str((config or {}).get(CHAVE_SENHA_DELL) or '').strip()
    if senha:
        linhas.insert(len(linhas) - 1, senha)   # entre o e-mail e o link, como na orientação original
    return linhas


def item_e_dell(item):
    """Marca extraída pela IA ou "Dell" escrito no título/descrição/textos do item."""
    marca = str(((item or {}).get('dados_ia') or {}).get('marca', '') or '')
    if _DELL.search(marca):
        return True
    textos = ' '.join(str((item or {}).get(k, '') or '')
                      for k in ('titulo', 'descricao', 'texto_item', 'texto_compra', 'part_number', 'marca'))
    return bool(_DELL.search(textos))


def evento_tem_dell(evento):
    return any(item_e_dell(it) for it in (evento or {}).get('itens', []) or [])


def adicionar_mensagem_dell(doc, config=None):
    """Bloco no final do Word: separador, título em negrito e as linhas da orientação (com a senha do CONFIG.json)."""
    from docx.shared import Pt

    p_linha = doc.add_paragraph("_" * 100)
    p_linha.paragraph_format.space_before = Pt(10)
    titulo = doc.add_paragraph()
    titulo.paragraph_format.space_before = Pt(6)
    titulo.add_run(TITULO_MENSAGEM_DELL).bold = True
    for linha in linhas_mensagem_dell(config):
        doc.add_paragraph(linha)
