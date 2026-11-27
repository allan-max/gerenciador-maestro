"""Código do robô da Vale (bots/vale_base_metais/) como um arquivo só, para os testes que leem o código pela árvore.

O robô é dividido em principal.py + mixins, como o do Coupa. Vários testes conferem o código sem importar o Selenium:
carregam métodos da classe ValeScraper pelo nome ou procuram trechos no texto. Aqui o texto de cada arquivo é juntado
sem mudança: o que está fora das classes (imports, constantes, logger) primeiro e, depois, os métodos de todas as
classes dentro de uma classe ValeScraper única. FONTE_VALE_BASE_METAIS é o caminho desse arquivo (temporário, um por processo).
"""
import ast
import atexit
import os
import tempfile
from pathlib import Path

PASTA_VALE = Path(__file__).resolve().parent.parent / 'bots' / 'vale_base_metais'
ARQUIVOS_VALE = [PASTA_VALE / 'principal.py', *sorted((PASTA_VALE / 'mixins').glob('*.py'))]


def _inicio(no):
    decoradores = getattr(no, 'decorator_list', None)
    return decoradores[0].lineno if decoradores else no.lineno


def juntar():
    fora, metodos = [], []
    for arquivo in ARQUIVOS_VALE:
        texto = arquivo.read_text(encoding='utf-8')
        linhas = texto.splitlines()
        for no in ast.parse(texto).body:
            if isinstance(no, ast.ClassDef) and no.name.endswith(('Mixin', 'Scraper')):
                i = no.lineno - 1
                while not linhas[i].rstrip().endswith(':'):   # cabeçalho da classe em mais de uma linha
                    i += 1
                metodos += linhas[i + 1:no.end_lineno] + ['']
            elif not (isinstance(no, ast.ImportFrom) and no.level):   # "from .mixins..." só existe no pacote
                fora += linhas[_inicio(no) - 1:no.end_lineno]
    return '\n'.join(fora + ['', '', 'class ValeScraper:'] + metodos) + '\n'


def _gravar():
    descritor, caminho = tempfile.mkstemp(prefix='vale_base_completo_', suffix='.py')
    with os.fdopen(descritor, 'w', encoding='utf-8') as f:
        f.write(juntar())
    atexit.register(lambda: os.path.exists(caminho) and os.remove(caminho))
    return Path(caminho)


FONTE_VALE_BASE_METAIS = _gravar()
