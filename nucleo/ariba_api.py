"""Comandos do robô SAP Ariba (AribaSourcing) pela API, no lugar de abrir o console e digitar a prioridade.

O AribaSourcing sobe sozinho no boot (tarefa agendada) e fica esperando comando:
  ligar    -> POST {gateway}/api/ariba/iniciar   {"codigoTarefa": ..., "dados": {"prioridade": ["1", "3"]}}
  desligar -> POST {servico}/api/ariba/parar     (direto no robô: o gateway ainda não repassa essa rota)
  estado   -> GET  {servico}/health              (sem autenticação; não gera linha no log do gateway)

CONFIG.json (opcionais): ariba_gateway_url, ariba_servico_url, ariba_tarefa.
.env (opcional): ARIBA_API_KEY, quando o AribaSourcing estiver com chave de API configurada.
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime

GATEWAY_PADRAO = 'http://localhost:5001'
SERVICO_PADRAO = 'http://localhost:6005'
TAREFA_PADRAO = 'AribaSourcing'
PARADO = 'AGUARDANDO_COMANDO'


class ErroAriba(Exception):
    pass


def _url(config, chave, padrao):
    return str((config or {}).get(chave) or padrao).rstrip('/')


def _chamar(metodo, url, corpo=None, timeout=5):
    """(status HTTP, corpo JSON como dict). Levanta ErroAriba se não houver resposta."""
    dados = None if corpo is None else json.dumps(corpo, ensure_ascii=False).encode('utf-8')
    requisicao = urllib.request.Request(url, data=dados, method=metodo)
    if dados is not None:
        requisicao.add_header('Content-Type', 'application/json; charset=utf-8')
    chave = os.environ.get('ARIBA_API_KEY', '').strip()
    if chave:
        requisicao.add_header('X-Api-Key', chave)
    try:
        with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
            status, texto = resposta.status, resposta.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        status, texto = e.code, e.read().decode('utf-8', 'replace')
    except (urllib.error.URLError, OSError) as e:
        raise ErroAriba(f'sem resposta de {url} ({getattr(e, "reason", e)})')
    try:
        conteudo = json.loads(texto) if texto.strip() else {}
    except ValueError:
        conteudo = {}
    return status, conteudo if isinstance(conteudo, dict) else {}


def saude(config, timeout=1):
    """Resposta do /health do robô, ou None se ele não estiver no ar."""
    try:
        status, corpo = _chamar('GET', _url(config, 'ariba_servico_url', SERVICO_PADRAO) + '/health', timeout=timeout)
    except ErroAriba:
        return None
    return corpo if status == 200 else None


def processando(config):
    """O robô está no ar e com prioridade definida (em ciclo ou esperando o próximo)?"""
    s = saude(config)
    return bool(s) and s.get('estado') != PARADO


def garantir_no_ar(config, espera_s=40):
    """Se o robô não responde, inicia a tarefa agendada e espera o /health."""
    if saude(config):
        return True
    tarefa = str((config or {}).get('ariba_tarefa') or TAREFA_PADRAO)
    subprocess.run(['schtasks', '/Run', '/TN', tarefa], capture_output=True, timeout=30)
    limite = time.time() + espera_s
    while time.time() < limite:
        time.sleep(2)
        if saude(config):
            return True
    return False


def lista_prioridades(texto):
    """'1 3' ou '1,3' -> ['1', '3'] (1 = ALIANÇA, 2 = VENTURA, 3 = UNIÃO; o robô valida)."""
    return [p for p in str(texto or '').replace(',', ' ').split() if p]


def iniciar(config, prioridades):
    """Inicia o processamento ou troca a prioridade. Devolve a mensagem do robô."""
    corpo = {'codigoTarefa': 'GM-' + datetime.now().strftime('%Y%m%d-%H%M%S'),
             'dados': {'prioridade': lista_prioridades(prioridades)}}
    status, resposta = _chamar('POST', _url(config, 'ariba_gateway_url', GATEWAY_PADRAO) + '/api/ariba/iniciar',
                               corpo, timeout=15)
    if status != 200 or not resposta.get('sucesso'):
        raise ErroAriba(resposta.get('mensagem') or f'o gateway respondeu HTTP {status}')
    return resposta.get('mensagem') or 'Processamento iniciado'


def parar(config):
    """Interrompe o ciclo em andamento; o robô continua no ar esperando um novo comando."""
    status, resposta = _chamar('POST', _url(config, 'ariba_servico_url', SERVICO_PADRAO) + '/api/ariba/parar',
                               timeout=15)
    if status != 200:
        raise ErroAriba(resposta.get('mensagem') or f'o AribaSourcing respondeu HTTP {status}')
    return resposta.get('mensagem') or 'Processamento interrompido'
