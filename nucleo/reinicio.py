"""Reinício suave do gerenciador.

O atualizador (scripts/atualizador.py) não mata nem abre o gerenciador: grava um pedido (gerenciador.reiniciar).
O gerenciador vê o pedido, espera não ter nada em andamento, fecha limpo e abre o processo novo ele mesmo, no
mesmo contexto em que foi aberto (janela, sessão, prioridade). Antes o novo nascia como filho da tarefa agendada.

Robôs externos (ME, Ariba, Findes, ComprasGov) rodam em janela própria: seguem ligados durante a troca e o
gerenciador novo os readota. O que o processo antigo sabe e o novo precisa fica no arquivo de troca.
"""
import functools
import json
import os
import subprocess
import sys
import threading
import time

ARQ_PEDIDO = 'gerenciador.reiniciar'
ARQ_TROCA = 'gerenciador.troca'
VALIDADE_TROCA_S = 600   # troca mais velha que isso é de outro dia (gerenciador ficou parado): não vale


def _ler(caminho):
    try:
        with open(caminho, encoding='utf-8') as f:
            dados = json.load(f)
    except (OSError, ValueError):
        return None
    return dados if isinstance(dados, dict) else None


def _gravar(caminho, dados):
    tmp = caminho + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(dados, f)
    os.replace(tmp, caminho)


def _apagar(caminho):
    try:
        os.remove(caminho)
    except OSError:
        pass


# ---------- pedido (atualizador -> gerenciador) ----------
def pedir(raiz, versao):
    _gravar(os.path.join(raiz, ARQ_PEDIDO), {'versao': versao, 'em': time.time()})


def ler_pedido(raiz):
    return _ler(os.path.join(raiz, ARQ_PEDIDO))


def limpar_pedido(raiz):
    _apagar(os.path.join(raiz, ARQ_PEDIDO))


def tentar(raiz, versao, motivos, fechar, relancar):
    """Uma checagem. Com pedido para outra versão e nada em andamento: fecha, abre o novo e devolve True.
    `motivos()` lista o que está em andamento; `fechar()` solta tudo que o processo novo vai precisar e devolve
    False se algo começou bem na hora (o pedido continua valendo para a próxima checagem)."""
    pedido = ler_pedido(raiz)
    if not pedido:
        return False
    if pedido.get('versao') == versao:   # já é esta versão (pedido repetido): nada a trocar
        limpar_pedido(raiz)
        return False
    if motivos():
        return False
    if fechar() is False:
        return False
    limpar_pedido(raiz)
    relancar()
    return True


def relancar(popen=subprocess.Popen):
    """Mesmo Python, mesmos argumentos, mesma pasta. Sem creationflags: herda a janela de quem abriu o gerenciador."""
    return popen([sys.executable, *sys.argv], cwd=os.getcwd())


# ---------- comandos de usuário em andamento ----------
class Contador:
    def __init__(self):
        self._trava = threading.Lock()
        self._ativos = []

    def contar(self, nome):
        def decorar(funcao):
            @functools.wraps(funcao)
            def contada(*args, **kwargs):
                with self._trava:
                    self._ativos.append(nome)
                try:
                    return funcao(*args, **kwargs)
                finally:
                    with self._trava:
                        self._ativos.remove(nome)
            return contada
        return decorar

    def ativos(self):
        with self._trava:
            return list(self._ativos)


comandos = Contador()


# ---------- troca (processo antigo -> processo novo) ----------
def salvar_troca(raiz, dados):
    _gravar(os.path.join(raiz, ARQ_TROCA), {**dados, 'em': time.time()})


def ler_troca(raiz, agora=None):
    """O que o processo antigo deixou; lido uma vez só. Sem troca (ou velha): {}."""
    caminho = os.path.join(raiz, ARQ_TROCA)
    dados = _ler(caminho)
    _apagar(caminho)
    if not dados or (agora or time.time()) - dados.get('em', 0) > VALIDADE_TROCA_S:
        return {}
    return dados


def retomada_piloto(troca, agora, pausa_s):
    """Último ciclo do piloto antes do reinício e quanto falta da pausa: o novo não dispara um ciclo extra."""
    ultimo = (troca or {}).get('ultimo_ciclo') or {}
    if not ultimo.get('portal'):
        return None
    return {'portal': ultimo['portal'], 'falta_s': max(0, int(ultimo.get('fim', 0) + pausa_s - agora))}


def posicao_log(tamanho, salvo):
    """De onde continuar o log de um robô que seguiu rodando: onde o antigo parou; sem troca, só linhas novas."""
    if salvo is None or salvo > tamanho:   # maior que o arquivo: o log foi trocado nesse meio tempo
        return tamanho
    return salvo
