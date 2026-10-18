"""Produção ou teste local: um lugar só decide onde ficam as bases (docs/melhorias-bots, fase 1).

Antes cada arquivo comparava URL_SERVIDOR com "http://localhost:8000" e o testar_local.bat usa
127.0.0.1: o teste local gravava na base de produção. Teste = URL_SERVIDOR em localhost/127.0.0.1
ou MAESTRO_TESTE=1; as bases de teste ficam em base_teste/, ao lado do código.
"""
import os
from urllib.parse import urlparse

RAIZ = os.path.dirname(os.path.abspath(__file__))
BASE_PRODUCAO = r"\\SERVIDOR2\Publico\ALLAN"
BASE_TESTE = os.path.join(RAIZ, 'base_teste')


def modo_teste():
    url = os.environ.get('URL_SERVIDOR', '')
    return os.environ.get('MAESTRO_TESTE') == '1' or urlparse(url).hostname in ('localhost', '127.0.0.1')


def base_dir():
    return BASE_TESTE if modo_teste() else BASE_PRODUCAO


def pasta_database():
    return os.path.join(base_dir(), 'database')


def pasta_banco():
    return os.path.join(pasta_database(), 'Banco-de-dados')


def eh_producao(caminho):
    return os.path.normcase(str(caminho).replace('/', '\\')).startswith(os.path.normcase(BASE_PRODUCAO + '\\'))


def exigir_trava(caminho):
    """Na base de produção só grava o processo que tem a trava do gerenciador (instancia_unica)."""
    import instancia_unica
    if eh_producao(caminho) and not instancia_unica.trava_ativa():
        raise PermissionError(f'Gravação recusada em {caminho}: só o gerenciador em execução grava na base de produção '
                              '(para testar, use testar_local.bat ou MAESTRO_TESTE=1).')
