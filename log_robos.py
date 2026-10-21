"""Log dos robôs (log-coupa.txt / log-vale.txt): data e máquina em cada linha e rotação (docs/melhorias-bots, fase 5).

Antes: só HH:MM:SS (não dava para saber o dia nem quem escreveu), arquivo crescendo sem limite (41 MB) e o
import da Vale quebrava se o servidor de rede estivesse fora. Agora, sem o servidor, segue só no console.
"""
import logging
import os
import socket
import sys
import time
from logging.handlers import RotatingFileHandler

import ambiente

TAMANHO_MAX = 10 * 1024 * 1024
ARQUIVOS_GUARDADOS = 5
LEITURA_MAX = 1024 * 1024
FORMATO = logging.Formatter(f'%(asctime)s [{socket.gethostname()}] [%(levelname)s] %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')


def caminho_log(nome):
    return os.path.join(ambiente.base_dir(), 'Logs', nome)


def configurar(logger, nome):
    """Um console e um arquivo com rotação no logger do robô. Pode ser chamado de novo sem duplicar."""
    for h in [h for h in logger.handlers if isinstance(h, logging.FileHandler)]:
        logger.removeHandler(h)
        h.close()
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        logger.addHandler(logging.StreamHandler(sys.stdout))
    for h in logger.handlers:
        h.setFormatter(FORMATO)
    caminho = caminho_log(nome)
    try:
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        arquivo = RotatingFileHandler(caminho, maxBytes=TAMANHO_MAX, backupCount=ARQUIVOS_GUARDADOS, encoding='utf-8')
    except OSError as e:
        logger.warning(f'Log em arquivo indisponível ({caminho}): {e}. Seguindo só no console.')
        return None
    arquivo.setFormatter(FORMATO)
    logger.addHandler(arquivo)
    return caminho


def novas_linhas(caminho, pos):
    """(linhas completas novas, nova posição). pos None = começa do fim. Arquivo menor que pos = rodou: lê do começo.
    Abre e fecha a cada leitura: no Windows a rotação precisa renomear o arquivo, e um leitor aberto impede."""
    try:
        tamanho = os.path.getsize(caminho)
    except OSError:
        return [], 0 if pos is None else pos   # ainda não existe: quando aparecer, lê do começo
    if pos is None:
        return [], tamanho
    if tamanho < pos:
        pos = 0
    if tamanho == pos:
        return [], pos
    with open(caminho, 'rb') as f:
        f.seek(pos)
        bloco = f.read(LEITURA_MAX)   # o resto vem nas próximas leituras
    fim = bloco.rfind(b'\n')
    if fim < 0:
        return [], pos          # linha ainda sendo escrita
    texto = bloco[:fim + 1].decode('utf-8', errors='ignore')
    return [l.strip() for l in texto.splitlines() if l.strip()], pos + fim + 1


def acompanhar(caminho, enviar, continuar, intervalo=0.5):
    """Repassa ao painel cada linha nova do log enquanto continuar() for verdadeiro."""
    pos = None
    while continuar():
        linhas, pos = novas_linhas(caminho, pos)
        for linha in linhas:
            enviar(linha)
        time.sleep(intervalo)
