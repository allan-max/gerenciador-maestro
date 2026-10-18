"""Recupera cotações extraídas (estão no eventos.jsonl) que sumiram do COTAÇÕES.json, como em 13/10.

Uso: python scripts/recuperar_perdidas.py --de 2026-10-13 --ate 2026-10-14            (só mostra)
     python scripts/recuperar_perdidas.py --de 2026-10-13 --ate 2026-10-14 --gravar   (aplica)

- Ainda abertas (vencimento hoje ou depois): saem do JSONL, e o robô extrai de novo no próximo ciclo.
- Já vencidas ou sem data: vão para relatorios/recuperacao_<de>_<ate>.csv, para conferência.
Cotações BLOQUEADAS também nunca entram no COTAÇÕES.json: as abertas voltam a ser bloqueadas na reextração.
Rode o --gravar com o gerenciador parado (o script pega a trava da base).
"""
import argparse
import csv
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

FONTES = (('coupa', 'eventos.jsonl', False), ('vale', 'eventos_nimbi.jsonl', True))   # Vale grava MM/DD


def vencimento_tabela(texto, americano):
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2,4})', str(texto or ''))
    if not m:
        return None
    a, b, ano = map(int, m.groups())
    mes, dia = (a, b) if americano else (b, a)
    try:
        return date(ano + 2000 if ano < 100 else ano, mes, dia)
    except ValueError:
        return None


def ler_jsonl(caminho):
    with open(caminho, encoding='utf-8') as f:
        return [l for l in f.read().splitlines() if l.strip()]


def perdidas(linhas, numeros_no_banco, de, ate):
    """Última linha de cada evento extraído no período que não está no COTAÇÕES.json."""
    ultimos = {}
    for linha in linhas:
        try:
            d = json.loads(linha)
        except ValueError:
            continue
        if d.get('titulo'):
            ultimos[str(d['titulo']).strip()] = d
    return {n: d for n, d in ultimos.items()
            if de <= str(d.get('data', '')) <= ate and n not in numeros_no_banco}


def gravar_jsonl(caminho, linhas):
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=os.path.dirname(caminho), prefix='maestro-',
                                     suffix='.tmp', delete=False) as f:
        f.write('\n'.join(linhas) + '\n')
    os.replace(f.name, caminho)


def executar(pasta_database, numeros_no_banco, de, ate, gravar, hoje=None, pasta_relatorios=None):
    hoje = hoje or date.today()
    abertas, vencidas = {}, []
    for portal, arquivo, americano in FONTES:
        caminho = os.path.join(pasta_database, arquivo)
        if not os.path.exists(caminho):
            continue
        linhas = ler_jsonl(caminho)
        achadas = perdidas(linhas, numeros_no_banco, de, ate)
        venc = {n: vencimento_tabela(d.get('vencimento_tabela'), americano) for n, d in achadas.items()}
        abertas[portal] = sorted(n for n, v in venc.items() if v and v >= hoje)
        vencidas += [(portal, n, achadas[n].get('data'), achadas[n].get('vencimento_tabela')) for n, v in sorted(venc.items())
                     if not (v and v >= hoje)]
        if gravar and abertas[portal]:
            shutil.copy2(caminho, caminho + datetime.now().strftime('.antes-recuperacao-%Y%m%d-%H%M%S'))
            remover = set(abertas[portal])
            gravar_jsonl(caminho, [l for l in linhas if str(json.loads(l).get('titulo', '')).strip() not in remover])
    relatorio = None
    if vencidas:
        pasta = pasta_relatorios or os.path.join(RAIZ, 'relatorios')
        os.makedirs(pasta, exist_ok=True)
        relatorio = os.path.join(pasta, f'recuperacao_{de}_{ate}.csv')
        with open(relatorio, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(['PORTAL', 'COTAÇÃO', 'EXTRAÍDA EM', 'VENCIMENTO (TABELA)'])
            w.writerows(vencidas)
    return abertas, vencidas, relatorio


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--de', required=True, help='AAAA-MM-DD')
    parser.add_argument('--ate', required=True, help='AAAA-MM-DD')
    parser.add_argument('--gravar', action='store_true', help='tira as abertas do JSONL (sem isto, só mostra)')
    args = parser.parse_args()
    import ambiente
    from planilha_manager import planilha_manager
    if args.gravar and not ambiente.modo_teste():
        from instancia_unica import JaRodando, instancia_unica
        try:
            _trava = instancia_unica(ambiente.pasta_banco())
        except JaRodando as e:
            raise SystemExit(f'Pare o gerenciador antes de gravar ({e}).')
    planilha_manager.iniciar()
    numeros = {str(r.get('COTAÇÃO') or '').strip() for r in planilha_manager.cotacoes.get('COTAÇÃO', []) if isinstance(r, dict)}
    abertas, vencidas, relatorio = executar(ambiente.pasta_database(), numeros, args.de, args.ate, args.gravar)
    for portal, nums in abertas.items():
        acao = 'tiradas do JSONL (serão extraídas de novo)' if args.gravar else 'seriam tiradas do JSONL; rode com --gravar'
        print(f"{portal}: {len(nums)} aberta(s) {acao}: {', '.join(nums) or '-'}")
    print(f"{len(vencidas)} vencida(s) ou sem data para conferir" + (f": {relatorio}" if relatorio else ''))
