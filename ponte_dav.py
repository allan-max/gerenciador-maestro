"""Ponte Maestro -> Cotafrete: gera o DAV da resposta a partir da Planilha Base.

O portal (server.js) manda `gerar_dav` com a planilha que o vendedor anexou.
Aqui, numa thread à parte (a fila dos robôs não espera):

1. POST {COTAFRETE_URL}/api/dav com a planilha, o vendedor e a cotação;
2. pergunta pelo id de tempos em tempos (o Cotafrete nunca chama o Maestro);
3. com o DAV pronto, baixa o PDF e devolve ao portal `dav_pronto`
   (o PDF no formato de `davs` e o NCM de cada item); o portal coloca a
   resposta na fila de sempre (modo `responder`).

Erros da conferência, erro do HSE ou estado `incerto` voltam ao portal como
`status_dav` com estado `erro` e os motivos. `incerto` NUNCA é refeito daqui:
gerar em dobro no HSE (produção) é pior que esperar o vendedor conferir.

Reenviar a mesma planilha é seguro: o Cotafrete devolve o DAV que já existe
(trava pelo hash), então uma queda do gerenciador no meio só pede reenvio.

Configuração no `.env`:
    COTAFRETE_URL    (padrão http://192.168.1.250:8000)
    COTAFRETE_TOKEN  (igual ao DAV_API_TOKEN do .env do Cotafrete)

Código compatível com Python 3.8 de propósito (sem `X | None`, sem walrus).
"""

import base64
import logging
import os
import threading
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

URL_PADRAO = "http://192.168.1.250:8000"
INTERVALO_S = 10            # entre uma pergunta e outra
LIMITE_S = 30 * 60          # passou disto sem terminar: desiste e avisa (não refaz)
FALHAS_SEGUIDAS = 6         # perguntas sem resposta seguidas até dar "fora do alcance"
TIMEOUT_S = 30

SEM_TOKEN = "Gerenciador sem COTAFRETE_TOKEN no .env: não dá para pedir o DAV ao Cotafrete."
FORA_DO_ALCANCE = "Cotafrete fora do alcance ({url}). O DAV não foi gerado; tente de novo em alguns minutos."

_ativos = set()             # cotações com uma ponte rodando agora
_trava = threading.Lock()


def _url() -> str:
    return (os.environ.get("COTAFRETE_URL") or URL_PADRAO).strip().rstrip("/")


def _token() -> str:
    return (os.environ.get("COTAFRETE_TOKEN") or "").strip()


def _cabecalho() -> Dict[str, str]:
    return {"Authorization": "Bearer " + _token()}


def _detalhe(resp) -> str:
    try:
        corpo = resp.json()
    except Exception:  # noqa: BLE001 — resposta que não é JSON
        return "HTTP {}".format(resp.status_code)
    if isinstance(corpo, dict):
        if corpo.get("erros"):
            return " · ".join(str(e) for e in corpo["erros"])
        if corpo.get("detail"):
            return str(corpo["detail"])
    return "HTTP {}".format(resp.status_code)


def _status(emitir, evento: str, estado: str, mensagem: str, corpo: Optional[dict] = None) -> None:
    corpo = corpo or {}
    emitir("status_dav", {
        "evento": evento,
        "estado": estado,                 # gerando | erro | pronto
        "mensagem": mensagem,
        "id": corpo.get("id"),
        "numero": corpo.get("numero"),
        "avisos": corpo.get("avisos") or [],
        "erros": corpo.get("erros") or [],
    })


def iniciar(dados: dict, emitir: Callable, http=None) -> bool:
    """Handler do socket: valida e solta a thread. False = ignorado (já roda)."""
    evento = str((dados or {}).get("evento") or "").strip()
    if not evento:
        logger.warning("gerar_dav sem evento: ignorado")
        return False
    with _trava:
        if evento in _ativos:
            logger.info("gerar_dav %s: já há uma ponte rodando, ignorado", evento)
            return False
        _ativos.add(evento)
    th = threading.Thread(target=_rodar_e_soltar, args=(dados, emitir, http),
                          name="ponte-dav-" + evento, daemon=True)
    th.start()
    return True


def _rodar_e_soltar(dados, emitir, http) -> None:
    evento = str(dados.get("evento")).strip()
    try:
        gerar_dav(dados, emitir, http=http)
    except Exception as e:  # noqa: BLE001 — a thread nunca morre calada
        logger.exception("Ponte do DAV %s quebrou", evento)
        _status(emitir, evento, "erro", "Erro interno ao pedir o DAV: {}".format(e))
    finally:
        with _trava:
            _ativos.discard(evento)


def gerar_dav(dados: dict, emitir: Callable, http=None,
              dormir: Callable[[float], None] = time.sleep,
              relogio: Callable[[], float] = time.monotonic) -> Optional[dict]:
    """Roda a ponte inteira (bloqueia). Devolve o payload de `dav_pronto` ou None."""
    if http is None:
        import requests as http  # noqa: N813 — import tardio: os testes trocam o cliente
    evento = str(dados.get("evento") or "").strip()
    planilha = dados.get("planilha") or {}
    vendedor = str(dados.get("vendedor") or "").strip() or "maestro"
    if not planilha.get("data"):
        _status(emitir, evento, "erro", "Anexe a Planilha Base para gerar o DAV.")
        return None
    if not _token():
        _status(emitir, evento, "erro", SEM_TOKEN)
        return None
    url = _url()
    try:
        conteudo = base64.b64decode(planilha["data"])
    except Exception:  # noqa: BLE001
        _status(emitir, evento, "erro", "A Planilha Base chegou corrompida. Anexe de novo.")
        return None

    _status(emitir, evento, "gerando", "Enviando a Planilha Base ao Cotafrete...")
    try:
        resp = http.post(url + "/api/dav", headers=_cabecalho(), timeout=TIMEOUT_S,
                         data={"vendedor": vendedor[:60], "cotacao": evento[:60]},
                         files={"planilha": (planilha.get("name") or "planilha.xlsx", conteudo)})
    except Exception as e:  # noqa: BLE001 — rede, DNS, recusa
        logger.warning("Ponte do DAV %s: POST falhou: %s", evento, e)
        _status(emitir, evento, "erro", FORA_DO_ALCANCE.format(url=url))
        return None
    if resp.status_code not in (200, 201, 422):
        _status(emitir, evento, "erro", "O Cotafrete recusou o pedido do DAV: " + _detalhe(resp))
        return None
    corpo = resp.json()
    logger.info("Ponte do DAV %s: Cotafrete id %s (%s)", evento, corpo.get("id"), corpo.get("estado"))

    inicio = relogio()
    ultimo = None
    falhas = 0
    while True:
        estado = corpo.get("estado")
        if estado in ("erros", "erro", "incerto"):
            motivo = " · ".join(corpo.get("erros") or []) or "O Cotafrete não gerou o DAV."
            if estado == "incerto":
                motivo = "DAV em dúvida no HSE (não refaço sozinho): " + motivo
            _status(emitir, evento, "erro", motivo, corpo)
            return None
        if estado == "pronto":
            return _entregar(http, url, evento, corpo, emitir)
        if estado != ultimo:
            texto = "Conferindo a planilha no HSE..." if estado == "conferindo" else "Gerando o DAV no HSE..."
            _status(emitir, evento, "gerando", texto, corpo)
            ultimo = estado
        if relogio() - inicio > LIMITE_S:
            _status(emitir, evento, "erro",
                    "O DAV passou de {} min sem terminar. Confira no Cotafrete (envio {}) antes de reenviar."
                    .format(LIMITE_S // 60, corpo.get("id")), corpo)
            return None
        dormir(INTERVALO_S)
        try:
            r = http.get("{}/api/dav/{}".format(url, corpo["id"]), headers=_cabecalho(), timeout=TIMEOUT_S)
            if r.status_code != 200:
                raise RuntimeError(_detalhe(r))
            corpo = r.json()
            falhas = 0
        except Exception as e:  # noqa: BLE001 — piscada de rede: tenta de novo
            falhas += 1
            logger.warning("Ponte do DAV %s: consulta %s falhou (%s/%s): %s",
                           evento, corpo.get("id"), falhas, FALHAS_SEGUIDAS, e)
            if falhas >= FALHAS_SEGUIDAS:
                _status(emitir, evento, "erro", FORA_DO_ALCANCE.format(url=url), corpo)
                return None


def _entregar(http, url: str, evento: str, corpo: dict, emitir: Callable) -> Optional[dict]:
    try:
        r = http.get("{}/api/dav/{}/pdf".format(url, corpo["id"]), headers=_cabecalho(), timeout=TIMEOUT_S)
        if r.status_code != 200 or not r.content:
            raise RuntimeError(_detalhe(r))
    except Exception as e:  # noqa: BLE001
        logger.warning("Ponte do DAV %s: PDF não baixou: %s", evento, e)
        _status(emitir, evento, "erro",
                "O DAV {} foi gerado, mas o PDF não baixou. Reenvie a resposta (não gera outro DAV)."
                .format(corpo.get("numero")), corpo)
        return None
    nome = "DAV_{}.pdf".format(corpo.get("numero") or corpo.get("id"))
    pronto = {
        "evento": evento,
        "id": corpo.get("id"),
        "numero": corpo.get("numero"),
        "avisos": corpo.get("avisos") or [],
        "davs": [{"name": nome, "data": base64.b64encode(r.content).decode("ascii")}],
        "itens": _itens(corpo.get("itens") or []),
    }
    emitir("dav_pronto", pronto)
    _status(emitir, evento, "pronto", "DAV {} pronto.".format(corpo.get("numero")), corpo)
    return pronto


def _itens(itens: List[dict]) -> List[dict]:
    """NCM no formato do robô (8 dígitos) e a origem: cadastro HSE / planilha / IA."""
    saida = []
    for i in itens:
        ncm = "".join(c for c in str(i.get("ncm") or "") if c.isdigit())
        saida.append({"item": i.get("item"), "ncm": ncm if len(ncm) == 8 else "",
                      "origem": i.get("origem") or ""})
    return saida
