"""Produção ou teste local: um lugar só decide onde ficam as bases (docs/melhorias-bots, fase 1).

Antes cada arquivo comparava URL_SERVIDOR com "http://localhost:8000" e o testar_local.bat usa
127.0.0.1: o teste local gravava na base de produção. Teste = URL_SERVIDOR em localhost/127.0.0.1
ou MAESTRO_TESTE=1; as bases de teste ficam em base_teste/, ao lado do código.
"""
import json
import os
import threading
from urllib.parse import urlparse

RAIZ = os.path.dirname(os.path.abspath(__file__))
BASE_PRODUCAO = r"\\SERVIDOR2\Publico\ALLAN"
BASE_TESTE = os.path.join(RAIZ, 'base_teste')


_modo = None   # decidido uma vez: o gerenciador pergunta antes do load_dotenv e os robôs depois


def modo_teste():
    global _modo
    if _modo is None:
        url = os.environ.get('URL_SERVIDOR', '')
        _modo = os.environ.get('MAESTRO_TESTE') == '1' or urlparse(url).hostname in ('localhost', '127.0.0.1')
    return _modo


def base_dir():
    return BASE_TESTE if modo_teste() else BASE_PRODUCAO


def pasta_database():
    return os.path.join(base_dir(), 'database')


def pasta_banco():
    return os.path.join(pasta_database(), 'Banco-de-dados')


def eh_producao(caminho):
    return os.path.normcase(str(caminho).replace('/', '\\')).startswith(os.path.normcase(BASE_PRODUCAO + '\\'))


PAGINAS_VERIFICACAO_PADRAO = 60
PAGINAS_VERIFICACAO_MAX = 500


def ler_config():
    """CONFIG.json como está agora (o painel admin pode ter acabado de salvar). {} se faltar ou estiver quebrado."""
    try:
        with open(os.path.join(RAIZ, 'CONFIG.json'), encoding='utf-8-sig') as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def paginas_verificacao(portal, config=None):
    """Teto de páginas da verificação ('coupa' ou 'vale'). O painel salva como texto ("60"); inválido = padrão."""
    cfg = ler_config() if config is None else config
    valor = cfg.get(f'{portal}_verificacao_max_paginas')
    if valor in (None, '') and portal == 'coupa':
        valor = cfg.get('verificacao_max_paginas')   # nome antigo, de antes do painel
    try:
        n = int(str(valor).strip())
    except (TypeError, ValueError):
        return PAGINAS_VERIFICACAO_PADRAO
    return min(max(n, 1), PAGINAS_VERIFICACAO_MAX)


# Robôs acrescentam linhas nos JSONL de eventos e o gerenciador regrava o arquivo inteiro (limpeza para re-extração):
# sem esta trava, uma linha acrescentada entre a leitura e a troca do arquivo se perdia. Todos rodam no mesmo processo.
TRAVA_JSONL = threading.RLock()


def acrescentar_jsonl(caminho, registro):
    with TRAVA_JSONL:
        with open(caminho, 'a', encoding='utf-8') as f:
            f.write(json.dumps(registro) + '\n')


def exigir_trava(caminho):
    """Na base de produção só grava o processo que tem a trava do gerenciador (instancia_unica)."""
    import instancia_unica
    if eh_producao(caminho) and not instancia_unica.trava_ativa():
        raise PermissionError(f'Gravação recusada em {caminho}: só o gerenciador em execução grava na base de produção '
                              '(para testar, use testar_local.bat ou MAESTRO_TESTE=1).')
