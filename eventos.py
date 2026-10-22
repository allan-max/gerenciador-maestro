"""Eventos do gerenciador para o dashboard de Atividade do portal.

Uma linha JSON por evento em logs/eventos.jsonl, gravada só por este processo:
{"em", "servico", "tipo", "nivel", "resumo", "ref", "detalhe"}.

- registrar(): acrescenta uma linha (append + flush). Falha ao gravar nunca
  derruba a operação: vira só um aviso no log.
- limpar(): tira o que tem mais de 7 dias, com troca atômica (temporário +
  os.replace). Roda na partida e uma vez por dia.
- ultimos(): os últimos N (até 500), opcionalmente só depois de `desde`.
"""
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

PADRAO = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'eventos.jsonl')
ARQUIVO = None   # desligado até ativar(): os testes rodam na pasta de produção e não podem sujar o arquivo real
DIAS = 7
MAX_EVENTOS = 500
TAM_RESUMO = 140
SERVICOS = {'gerenciador', 'coupa', 'vale', 'ariba', 'me', 'findes', 'impressao'}
NIVEIS = {'ok', 'info', 'aviso', 'erro'}

_trava = threading.Lock()


def ativar(arquivo=PADRAO):
    global ARQUIVO
    ARQUIVO = arquivo


def _agora():
    return datetime.now().astimezone()


def _hora(texto):
    try:
        d = datetime.fromisoformat(str(texto).strip())
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.astimezone()


def registrar(servico, tipo, nivel, resumo, ref=None, detalhe=None, arquivo=None):
    """Grava um evento. Nunca levanta exceção. Desligado (não faz nada) antes de ativar()."""
    caminho = arquivo or ARQUIVO
    if not caminho:
        return
    try:
        resumo = str(resumo)
        if len(resumo) > TAM_RESUMO:
            resumo = resumo[:TAM_RESUMO - 1] + '…'
        linha = json.dumps({'em': _agora().isoformat(timespec='seconds'),
                            'servico': servico if servico in SERVICOS else 'gerenciador',
                            'tipo': str(tipo), 'nivel': nivel if nivel in NIVEIS else 'info',
                            'resumo': resumo, 'ref': None if ref is None else str(ref),
                            'detalhe': detalhe or {}}, ensure_ascii=False, default=str)
        with _trava:
            os.makedirs(os.path.dirname(caminho), exist_ok=True)
            with open(caminho, 'a', encoding='utf-8') as f:
                f.write(linha + '\n')
                f.flush()
    except Exception as e:   # o evento é só para o painel
        logger.warning('Evento não gravado (%s): %s', e, resumo)


def _ler(caminho):
    if not caminho or not os.path.exists(caminho):
        return []
    saida = []
    with open(caminho, encoding='utf-8') as f:
        for linha in f:
            try:
                e = json.loads(linha)
            except ValueError:
                continue          # linha cortada por um desligamento no meio da gravação
            if isinstance(e, dict) and _hora(e.get('em')) is not None:
                saida.append(e)
    return saida


def limpar(arquivo=None, agora=None):
    """Mantém só os últimos 7 dias. Nunca levanta exceção."""
    caminho = arquivo or ARQUIVO
    corte = (agora or _agora()) - timedelta(days=DIAS)
    temporario = None
    try:
        with _trava:
            if not caminho or not os.path.exists(caminho):
                return
            manter = [e for e in _ler(caminho) if _hora(e['em']) >= corte]
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=os.path.dirname(caminho),
                                             prefix='eventos-', suffix='.tmp', delete=False) as f:
                temporario = f.name
                for e in manter:
                    f.write(json.dumps(e, ensure_ascii=False) + '\n')
            os.replace(temporario, caminho)
    except Exception as e:
        logger.warning('Limpeza dos eventos falhou: %s', e)
    finally:
        if temporario and os.path.exists(temporario):
            os.unlink(temporario)


def ler_todos(arquivo=None):
    """Todos, na ordem gravada (mais antigos primeiro), sem o corte de 500: o último backup do dia pode estar mais longe."""
    with _trava:
        return _ler(arquivo or ARQUIVO)


def ultimos(limite=200, desde=None, arquivo=None, agora=None):
    """Mais novos primeiro, só dos últimos 7 dias (o arquivo pode ter um dia a mais até a limpeza)."""
    try:
        limite = max(1, min(int(limite), MAX_EVENTOS))
    except (TypeError, ValueError):
        limite = 200
    corte = (agora or _agora()) - timedelta(days=DIAS)
    depois = _hora(desde) if desde else None
    with _trava:
        todos = _ler(arquivo or ARQUIVO)
    todos = [e for e in todos if _hora(e['em']) >= corte and (depois is None or _hora(e['em']) > depois)]
    todos.sort(key=lambda e: _hora(e['em']), reverse=True)
    return {'eventos': todos[:limite], 'total': len(todos), 'erros': {}}
