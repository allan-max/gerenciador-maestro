"""Ponte Maestro -> Cotabot (VM): cota os itens e devolve a Planilha Base preenchida.

Mesmo desenho da ponte do DAV (ponte_dav.py). O portal (server.js) manda:

- `cotabot_cotar` {pedido, evento, vendedor, itens[{descricao, quantidade}], sites}
  Aqui, numa thread à parte (a fila dos robôs não espera):
    1. POST {COTABOT_URL}/api/cotar;
    2. pergunta pela tarefa de tempos em tempos (o Cotabot nunca chama o Maestro);
    3. a cada mudança devolve `cotabot_status` (fila, cotando item N, passo atual...);
    4. no fim baixa a planilha e devolve `cotabot_pronto` {pedido, planilha{name, data base64}}.
- `cotabot_uso_pedir` -> devolve `cotabot_uso` (uso do plano Claude que roda na VM).

Configuração no `.env`:
    COTABOT_URL    (padrão http://192.168.1.250:8765)
    COTABOT_TOKEN  (igual ao COTABOT_TOKEN do .env do Cotabot na VM)

Código compatível com Python 3.8 de propósito (sem `X | None`, sem walrus).
"""

import base64
import json
import logging
import os
import threading
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

URL_PADRAO = "http://192.168.1.250:8765"
INTERVALO_S = 10            # entre uma pergunta e outra
LIMITE_S = 12 * 60 * 60     # 24 itens x até 40 min cada, com folga para a fila
FALHAS_SEGUIDAS = 30        # ~5 min sem resposta da VM até dar "fora do alcance"
TIMEOUT_S = 30

SEM_TOKEN = "Gerenciador sem COTABOT_TOKEN no .env: não dá para pedir a cotação ao Cotabot."
FORA_DO_ALCANCE = "Cotabot fora do alcance ({url}). Confira se a VM e o iniciar_vm.bat estão ligados."

_ativos = set()             # pedidos com uma ponte rodando agora
_trava = threading.Lock()


def _url() -> str:
    return (os.environ.get("COTABOT_URL") or URL_PADRAO).strip().rstrip("/")


def _token() -> str:
    return (os.environ.get("COTABOT_TOKEN") or "").strip()


def _cabecalho() -> Dict[str, str]:
    return {"Authorization": "Bearer " + _token()}


def _detalhe(resp) -> str:
    try:
        corpo = resp.json()
    except Exception:  # noqa: BLE001 — resposta que não é JSON
        return "HTTP {}".format(resp.status_code)
    if isinstance(corpo, dict) and corpo.get("erro"):
        return str(corpo["erro"])
    return "HTTP {}".format(resp.status_code)


def _escolhido(res: dict) -> Optional[dict]:
    anuncios = res.get("anuncios") or []
    idx = res.get("escolhido")
    if isinstance(idx, int) and 0 <= idx < len(anuncios):
        a = anuncios[idx] or {}
        return {"fornecedor": str(a.get("fornecedor") or "")[:120], "preco": str(a.get("preco") or "")[:40],
                "link": str(a.get("link") or "")[:600]}
    return None


def resumo_tarefa(t: dict) -> dict:
    """Só o que a tela mostra (sem prints, sem textos longos)."""
    itens = []
    for i in t.get("itens") or []:
        res = i.get("resultado") or {}
        itens.append({
            "numero": i.get("numero"),
            "status": i.get("status"),                     # fila | cotando | pronto | erro
            "passos": i.get("passos") or 0,
            "passo_atual": str(i.get("passo_atual") or "")[:160],
            "erro": str(i.get("erro") or "")[:300],
            "anuncios": len(res.get("anuncios") or []),
            "escolhido": _escolhido(res),
            "nao_encontrado": bool(res.get("nao_encontrado")),
            "duracao_s": res.get("duracao_s"),
        })
    return {"id": t.get("id"), "status": t.get("status"), "posicao_fila": t.get("posicao_fila"),
            "erro_planilha": str(t.get("erro_planilha") or "")[:300], "itens": itens}


def _status(emitir, pedido: str, estado: str, mensagem: str, tarefa: Optional[dict] = None) -> None:
    emitir("cotabot_status", {"pedido": pedido, "estado": estado, "mensagem": mensagem,
                              "tarefa": tarefa})


def _mensagem(t: dict) -> str:
    itens = t.get("itens") or []
    if t.get("status") == "fila":
        return "Na fila do Cotabot."
    feitos = sum(1 for i in itens if i.get("status") in ("pronto", "erro"))
    atual = next((i for i in itens if i.get("status") == "cotando"), None)
    if atual:
        return "Cotando item {} de {}.".format(atual.get("numero"), len(itens))
    return "{} de {} itens cotados.".format(feitos, len(itens))


def iniciar(dados: dict, emitir: Callable, http=None) -> bool:
    """Handler do socket: valida e solta a thread. False = ignorado (já roda)."""
    pedido = str((dados or {}).get("pedido") or "").strip()
    if not pedido:
        logger.warning("cotabot_cotar sem pedido: ignorado")
        return False
    with _trava:
        if pedido in _ativos:
            return False
        _ativos.add(pedido)
    th = threading.Thread(target=_rodar_e_soltar, args=(dados, emitir, http),
                          name="ponte-cotabot-" + pedido, daemon=True)
    th.start()
    return True


def _rodar_e_soltar(dados, emitir, http) -> None:
    pedido = str(dados.get("pedido")).strip()
    try:
        cotar(dados, emitir, http=http)
    except Exception as e:  # noqa: BLE001 — a thread nunca morre calada
        logger.exception("Ponte do Cotabot %s quebrou", pedido)
        _status(emitir, pedido, "erro", "Erro interno ao pedir a cotação: {}".format(e))
    finally:
        with _trava:
            _ativos.discard(pedido)


def _itens(itens) -> List[dict]:
    saida = []
    for i in (itens or [])[:24]:
        desc = str((i or {}).get("descricao") or "").strip()[:3000]
        if desc:
            saida.append({"descricao": desc, "quantidade": (i or {}).get("quantidade") or 1})
    return saida


def cotar(dados: dict, emitir: Callable, http=None,
          dormir: Callable[[float], None] = time.sleep,
          relogio: Callable[[], float] = time.monotonic) -> Optional[dict]:
    """Roda a ponte inteira (bloqueia). Devolve o payload de `cotabot_pronto` ou None."""
    if http is None:
        import requests as http  # noqa: N813 — import tardio: os testes trocam o cliente
    pedido = str(dados.get("pedido") or "").strip()
    itens = _itens(dados.get("itens"))
    if not itens:
        _status(emitir, pedido, "erro", "Nenhum item com descrição para cotar.")
        return None
    if not _token():
        _status(emitir, pedido, "erro", SEM_TOKEN)
        return None
    url = _url()
    sites = dados.get("sites") or ""
    if isinstance(sites, list):
        sites = "\n".join(str(s) for s in sites)

    _status(emitir, pedido, "enviando", "Enviando os itens ao Cotabot...")
    try:
        resp = http.post(url + "/api/cotar", headers=_cabecalho(), timeout=TIMEOUT_S,
                         json={"evento": str(dados.get("evento") or "")[:40],
                               "vendedor": str(dados.get("vendedor") or "")[:80],
                               "sites": str(sites)[:4000], "itens": itens})
    except Exception as e:  # noqa: BLE001 — rede, DNS, recusa
        logger.warning("Ponte do Cotabot %s: POST falhou: %s", pedido, e)
        _status(emitir, pedido, "erro", FORA_DO_ALCANCE.format(url=url))
        return None
    if resp.status_code != 200:
        _status(emitir, pedido, "erro", "O Cotabot recusou o pedido: " + _detalhe(resp))
        return None
    tid = (resp.json() or {}).get("id")
    if not tid:
        _status(emitir, pedido, "erro", "O Cotabot não devolveu o número da tarefa.")
        return None
    logger.info("Ponte do Cotabot %s: tarefa %s", pedido, tid)

    inicio = relogio()
    ultimo = None
    falhas = 0
    while True:
        dormir(INTERVALO_S)
        try:
            r = http.get("{}/api/tarefa/{}".format(url, tid), headers=_cabecalho(), timeout=TIMEOUT_S)
            if r.status_code != 200:
                raise RuntimeError(_detalhe(r))
            t = r.json()
            falhas = 0
        except Exception as e:  # noqa: BLE001 — piscada de rede: tenta de novo
            falhas += 1
            logger.warning("Ponte do Cotabot %s: consulta %s falhou (%s/%s): %s",
                           pedido, tid, falhas, FALHAS_SEGUIDAS, e)
            if falhas >= FALHAS_SEGUIDAS:
                _status(emitir, pedido, "erro", FORA_DO_ALCANCE.format(url=url))
                return None
            continue
        resumo = resumo_tarefa(t)
        if t.get("status") == "pronto":
            return _entregar(http, url, pedido, tid, resumo, t, emitir)
        chave = json.dumps(resumo, sort_keys=True)
        if chave != ultimo:
            _status(emitir, pedido, "fila" if t.get("status") == "fila" else "cotando", _mensagem(t), resumo)
            ultimo = chave
        if relogio() - inicio > LIMITE_S:
            _status(emitir, pedido, "erro",
                    "A cotação passou de {} h sem terminar. Confira o Cotabot na VM (tarefa {})."
                    .format(LIMITE_S // 3600, tid), resumo)
            return None


def _entregar(http, url: str, pedido: str, tid: str, resumo: dict, t: dict, emitir: Callable) -> Optional[dict]:
    if not t.get("planilha"):
        _status(emitir, pedido, "erro", "Itens cotados, mas a planilha não foi gerada: {}"
                .format(t.get("erro_planilha") or "erro desconhecido"), resumo)
        return None
    try:
        r = http.get("{}/api/planilha/{}".format(url, tid), headers=_cabecalho(), timeout=TIMEOUT_S * 4)
        if r.status_code != 200 or not r.content:
            raise RuntimeError(_detalhe(r))
    except Exception as e:  # noqa: BLE001
        logger.warning("Ponte do Cotabot %s: planilha não baixou: %s", pedido, e)
        _status(emitir, pedido, "erro", "Itens cotados, mas a planilha não baixou da VM (tarefa {}).".format(tid),
                resumo)
        return None
    pronto = {"pedido": pedido, "tarefa": resumo,
              "planilha": {"name": os.path.basename(str(t.get("planilha")))[:120],
                           "data": base64.b64encode(r.content).decode("ascii")}}
    emitir("cotabot_pronto", pronto)
    return pronto


def ler_uso(emitir: Callable, http=None) -> None:
    """Handler de `cotabot_uso_pedir`: thread curta, devolve `cotabot_uso`."""
    def rodar():
        cliente = http
        if cliente is None:
            import requests as cliente  # noqa: N813
        url = _url()
        if not _token():
            emitir("cotabot_uso", {"online": False, "mensagem": SEM_TOKEN})
            return
        try:
            r = cliente.get(url + "/api/uso", headers=_cabecalho(), timeout=TIMEOUT_S * 2)
            if r.status_code != 200:
                raise RuntimeError(_detalhe(r))
            emitir("cotabot_uso", {"online": True, "uso": r.json()})
        except Exception as e:  # noqa: BLE001
            logger.info("Cotabot: uso não lido: %s", e)
            emitir("cotabot_uso", {"online": False, "mensagem": FORA_DO_ALCANCE.format(url=url)})
    threading.Thread(target=rodar, name="cotabot-uso", daemon=True).start()
