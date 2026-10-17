"""Corrige no COTAÇÕES.json o RESPOSTA com "NÃO" de acento quebrado (ex.: "NÃƒO RESPONDIDO").

Uso: python scripts/normalizar_respostas.py            (só mostra quantas linhas mudariam)
     python scripts/normalizar_respostas.py --gravar   (faz backup ao lado e grava)

Rode o --gravar com o gerenciador e os robôs parados: eles guardam a base em memória. Se outro serviço
alterar o arquivo durante a execução, a gravação é cancelada (planilha_manager compara antes de trocar).
"""
import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CORRETO = 'NÃO RESPONDIDO'


def quebrada(valor):
    texto = str(valor or '').strip().upper()
    return texto != CORRETO and texto.startswith('N') and texto.endswith(' RESPONDIDO')


def normalizar(linhas):
    novas = [{**r, 'RESPOSTA': CORRETO} if isinstance(r, dict) and quebrada(r.get('RESPOSTA')) else r for r in linhas]
    return novas, sum(1 for a, b in zip(linhas, novas) if a is not b)


def executar(manager, gravar):
    manager.iniciar()
    with manager.lock:
        novas, total = normalizar(manager.cotacoes.get('COTAÇÃO', []))
        if not gravar or total == 0:
            return total
        backup = manager.caminho_cotacoes[:-5] + datetime.now().strftime(' antes-normalizar %Y%m%d-%H%M%S.json')
        shutil.copy2(manager.caminho_cotacoes, backup)
        manager.cotacoes = {**manager.cotacoes, 'COTAÇÃO': novas}
        if not manager.salvar():
            raise SystemExit(f'Falha ao gravar; a base não foi alterada (backup em {backup}).')
    return total


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--gravar', action='store_true', help='grava a correção (sem isto, só conta)')
    args = parser.parse_args()
    from planilha_manager import planilha_manager
    total = executar(planilha_manager, args.gravar)
    print(f"{total} linha(s) {'corrigida(s)' if args.gravar else 'seriam corrigidas; rode com --gravar'}")
