"""Transmissão do captcha (robô -> site) fluida, mirando 60 quadros por segundo.

Antes cada quadro passava pelo chromedriver (HTTP -> chromedriver -> Chrome) dentro do mesmo laço que
trata os cliques e o DOM: na prática saíam poucos quadros por segundo, todos iguais, e o socket enchia
de imagens repetidas que chegavam atrasadas no navegador do vendedor ("travado").

Agora:
- a captura roda numa thread própria, falando direto com o Chrome pelo WebSocket do DevTools
  (sem o salto pelo chromedriver), via Page.startScreencast: o Chrome empurra cada quadro novo e uma
  segunda thread recorta só a região do captcha (sempre do quadro mais novo);
- sem screencast/WebSocket cai num Page.captureScreenshot por quadro (teto de ~12 quadros/s);
- quadro idêntico ao anterior não é enviado (o captcha fica parado a maior parte do tempo);
  um quadro repetido sai a cada KEEPALIVE_S para a tela não achar que o captcha sumiu;
- controle de fluxo: o próximo quadro só sai depois que o servidor confirmou o anterior (ack),
  então nunca se acumula fila de imagens velhas; servidor antigo sem ack é detectado e ignorado;
- a thread se encerra sozinha se o laço do robô parar de chamar manter_transmissao (watchdog),
  então não fica transmitindo depois que o captcha foi resolvido ou o login caiu por erro.
"""
import itertools
import json
import logging
import threading
import time
import urllib.request

logger = logging.getLogger("CoupaApp")

FPS_ALVO = 60
INTERVALO = 1.0 / FPS_ALVO          # (1.0 / 60.0)
QUALIDADE_JPEG = 70
KEEPALIVE_S = 1.0                   # tela esconde o captcha após 5-8 s sem quadro
WATCHDOG_S = 2.0                    # sem manter_transmissao por 2 s: thread encerra
ACK_TIMEOUT_S = 1.0


class _ConexaoCdp:
    """WebSocket direto com a aba do Chrome que o Selenium controla."""

    def __init__(self, driver, handle):
        import websocket   # websocket-client: dependência do próprio Selenium 4
        caps = getattr(driver, 'capabilities', None) or {}
        endereco = (caps.get('goog:chromeOptions') or {}).get('debuggerAddress')
        if not endereco:
            raise RuntimeError('debuggerAddress indisponível')
        # 'localhost' no Windows tenta IPv6 (::1) antes e o Chrome só escuta IPv4: custava ~4 s por conexão
        endereco = endereco.replace('localhost', '127.0.0.1')
        with urllib.request.urlopen(f'http://{endereco}/json', timeout=2) as r:
            alvos = json.loads(r.read().decode('utf-8'))
        paginas = [a for a in alvos if a.get('type') == 'page' and a.get('webSocketDebuggerUrl')]
        alvo = next((a for a in paginas if handle and a.get('id') == handle), None) or (paginas[0] if paginas else None)
        if not alvo:
            raise RuntimeError('aba do Chrome não encontrada no DevTools')
        url_ws = alvo['webSocketDebuggerUrl'].replace('ws://localhost:', 'ws://127.0.0.1:')
        # skip_utf8_validation: sem isto o websocket-client valida cada quadro (~100 KB) em Python puro,
        # segura o GIL e derruba a taxa de quadros pela metade
        self._ws = websocket.create_connection(url_ws, timeout=5, suppress_origin=True, skip_utf8_validation=True)
        self._ids = itertools.count(1)

    def comando(self, metodo, params=None):
        mid = next(self._ids)
        self._ws.send(json.dumps({'id': mid, 'method': metodo, 'params': params or {}}))
        while True:   # eventos não são habilitados; ainda assim ignora o que não for a resposta
            msg = json.loads(self._ws.recv())
            if msg.get('id') == mid:
                if 'error' in msg:
                    raise RuntimeError(str(msg['error']))
                return msg.get('result') or {}

    def fechar(self):
        try:
            self._ws.close()
        except Exception:
            pass


class TransmissorCaptcha:
    def __init__(self, driver, sio, rect, handle=None):
        self.driver = driver
        self.sio = sio
        self.handle = handle
        self._rect = dict(rect)
        self._trava = threading.Lock()
        self._ultimo_sinal = time.monotonic()
        self._parar = threading.Event()
        self._ack = threading.Event()
        self._usa_ack = None            # None = ainda não sabe se o servidor confirma quadros
        self._rapido = True             # optimizeForSpeed (Chrome 117+); desliga se o Chrome recusar
        self.quadros_enviados = 0
        self._thread = threading.Thread(target=self._rodar, name='transmissor-captcha', daemon=True)

    # ---- chamado pelo laço do robô ----
    def iniciar(self):
        self._thread.start()
        return self

    def sinal(self, rect=None):
        with self._trava:
            if rect:
                self._rect = dict(rect)
            self._ultimo_sinal = time.monotonic()

    def parar(self, esperar=True):
        """Esperar garante que nenhum quadro sai depois do 'captcha_resolvido' (reabriria o painel na tela)."""
        self._parar.set()
        if esperar and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(ACK_TIMEOUT_S + 0.5)

    @property
    def ativo(self):
        return self._thread.is_alive() and not self._parar.is_set()

    # ---- thread ----
    def _clip(self):
        with self._trava:
            r = self._rect
            vivo = time.monotonic() - self._ultimo_sinal < WATCHDOG_S
        return vivo, {'x': float(r.get('x', 0)), 'y': float(r.get('y', 0)),
                      'width': float(r.get('width') or 400), 'height': float(r.get('height') or 580), 'scale': 1.0}

    def _capturar(self, executar, clip):
        params = {'format': 'jpeg', 'quality': QUALIDADE_JPEG, 'clip': clip, 'fromSurface': True}
        if self._rapido:
            try:
                return executar('Page.captureScreenshot', dict(params, optimizeForSpeed=True)).get('data')
            except Exception as e:
                if 'optimizeForSpeed' not in str(e) and 'Invalid' not in str(e):
                    raise
                self._rapido = False   # Chrome antigo (Win Server 2012 = Chrome 109) não conhece o parâmetro
        return executar('Page.captureScreenshot', params).get('data')

    def _enviar(self, b64):
        dados = {'imagem': f'data:image/jpeg;base64,{b64}'}
        if self._usa_ack is False:
            self.sio.emit('imagem_captcha_do_robo', dados)
            return
        self._ack.clear()
        self.sio.emit('imagem_captcha_do_robo', dados, callback=lambda *a: self._ack.set())
        if self._ack.wait(ACK_TIMEOUT_S):
            self._usa_ack = True
        elif self._usa_ack is None:
            self._usa_ack = False      # servidor sem ack: segue sem controle de fluxo
            logger.info('Transmissão do captcha: servidor não confirma quadros, enviando sem controle de fluxo.')

    def _rodar(self):
        conexao = None
        try:
            try:
                conexao = _ConexaoCdp(self.driver, self.handle)
            except Exception as e:
                logger.info(f'Transmissão do captcha via chromedriver (sem WebSocket direto: {e}).')
            if conexao is not None:
                try:
                    if self._rodar_screencast(conexao):
                        return
                except Exception as e:
                    logger.info(f'Screencast indisponível, usando captura quadro a quadro: {e}')
                conexao.fechar()
                conexao = None
            if not hasattr(self.driver, 'execute_cdp_cmd'):
                return
            self._rodar_polling(self.driver.execute_cdp_cmd)
        except Exception:
            logger.exception('Falha na transmissão do captcha')
        finally:
            self._parar.set()
            if conexao:
                conexao.fechar()

    # ---- caminho rápido: o Chrome empurra quadros (Page.startScreencast) ----
    def _rodar_screencast(self, conexao):
        """Page.captureScreenshot para em ~12 quadros/s (cada chamada espera um quadro novo e codifica).
        No screencast o Chrome empurra cada quadro novo do compositor, só quando a tela muda.
        Esta thread só recebe e confirma; outra recorta a região do captcha sempre do quadro MAIS NOVO."""
        from PIL import Image   # já é dependência dos robôs
        import websocket
        ws = conexao._ws
        ws.settimeout(0.25)
        ultimo = {'quadro': None}
        chegou = threading.Event()
        fim = threading.Event()   # encerra só o recorte: se o screencast falhar, o polling ainda assume
        ws.send(json.dumps({'id': 0, 'method': 'Page.startScreencast',
                            'params': {'format': 'jpeg', 'quality': 85, 'everyNthFrame': 1}}))
        recortador = threading.Thread(target=self._recortar_e_enviar, args=(Image, ultimo, chegou, fim),
                                      name='transmissor-captcha-recorte', daemon=True)
        recortador.start()
        recebeu = False
        inicio = time.monotonic()
        try:
            while not self._parar.is_set():
                if not self._clip()[0]:
                    break
                try:
                    msg = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    if not recebeu and time.monotonic() - inicio > 3:
                        return False   # nenhum quadro em 3 s: deixa o caminho quadro a quadro assumir
                    continue
                if msg.get('id') == 0 and 'error' in msg:
                    return False
                if msg.get('method') != 'Page.screencastFrame':
                    continue
                p = msg.get('params') or {}
                # confirma na hora: o Chrome só manda o próximo quadro depois do ack
                ws.send(json.dumps({'id': 1, 'method': 'Page.screencastFrameAck', 'params': {'sessionId': p.get('sessionId')}}))
                recebeu = True
                ultimo['quadro'] = (p.get('data'), p.get('metadata') or {})
                chegou.set()
            return True
        finally:
            fim.set()
            chegou.set()
            recortador.join(ACK_TIMEOUT_S + 0.5)
            try:
                ws.send(json.dumps({'id': 2, 'method': 'Page.stopScreencast'}))
            except Exception:
                pass

    def _recortar_e_enviar(self, Image, ultimo, chegou, fim):
        import base64
        import io
        anterior_px = None
        ultimo_b64 = None
        ultimo_envio = 0.0
        while not (self._parar.is_set() or fim.is_set()):
            novo = chegou.wait(KEEPALIVE_S)
            if (self._parar.is_set() or fim.is_set()):
                break
            if not novo:   # tela parada: reenvia o último para o site não esconder o captcha
                if ultimo_b64:
                    self._enviar(ultimo_b64)
                    ultimo_envio = time.monotonic()
                continue
            chegou.clear()
            quadro = ultimo['quadro']
            if not quadro or not quadro[0]:
                continue
            dados, meta = quadro
            try:
                img = Image.open(io.BytesIO(base64.b64decode(dados)))
                largura_css = float(meta.get('deviceWidth') or img.width)
                escala = img.width / largura_css if largura_css else 1.0
                _, c = self._clip()
                x = (c['x'] - float(meta.get('scrollOffsetX') or 0)) * escala
                y = (c['y'] - float(meta.get('scrollOffsetY') or 0)) * escala
                caixa = (int(round(x)), int(round(y)), int(round(x + c['width'] * escala)), int(round(y + c['height'] * escala)))
                recorte = img.crop(caixa)
                if recorte.size != (int(c['width']), int(c['height'])):
                    # o site mapeia o clique pelo tamanho natural da imagem: mantém em pixels CSS
                    recorte = recorte.resize((int(c['width']), int(c['height'])))
                px = recorte.tobytes()
                if px == anterior_px and time.monotonic() - ultimo_envio < KEEPALIVE_S:
                    continue   # mudou outra parte da página, não o captcha
                saida = io.BytesIO()
                recorte.convert('RGB').save(saida, 'JPEG', quality=QUALIDADE_JPEG)
                b64 = base64.b64encode(saida.getvalue()).decode('ascii')
            except Exception as e:
                logger.debug(f'Quadro do captcha descartado: {e}')
                continue
            if (self._parar.is_set() or fim.is_set()):
                break
            self._enviar(b64)   # com ack: enquanto espera, quadros novos sobrescrevem `ultimo` (sempre o mais novo)
            anterior_px, ultimo_b64, ultimo_envio = px, b64, time.monotonic()
            self.quadros_enviados += 1

    # ---- caminho lento: um Page.captureScreenshot por quadro (sem WebSocket direto) ----
    def _rodar_polling(self, executar):
        ultimo_b64 = None
        ultimo_envio = 0.0
        falhas = 0
        while not self._parar.is_set():
            inicio = time.monotonic()
            vivo, clip = self._clip()
            if not vivo:
                break
            try:
                b64 = self._capturar(executar, clip)
                falhas = 0
            except Exception as e:
                falhas += 1
                if falhas >= 30:
                    logger.warning(f'Transmissão do captcha encerrada após falhas seguidas: {e}')
                    break
                time.sleep(0.05)
                continue
            if self._parar.is_set():
                break
            agora = time.monotonic()
            if b64 and (b64 != ultimo_b64 or agora - ultimo_envio >= KEEPALIVE_S):
                self._enviar(b64)
                ultimo_b64, ultimo_envio = b64, time.monotonic()
                self.quadros_enviados += 1
            resto = INTERVALO - (time.monotonic() - inicio)
            if resto > 0:
                self._parar.wait(resto)


def manter_transmissao(dono, driver, sio, rect):
    """Chamar a cada volta do laço do captcha. Inicia a transmissão (uma por robô) ou só renova o sinal de vida.
    Sem rect (elemento sem posição) não transmite: o laço usa o caminho lento antigo."""
    if not rect:
        return None
    atual = getattr(dono, '_transmissor_captcha', None)
    if atual is not None and atual.ativo and atual.driver is driver:
        atual.sinal(rect)
        return atual
    if atual is not None:
        atual.parar(esperar=False)
    try:
        handle = driver.current_window_handle
    except Exception:
        handle = None
    novo = TransmissorCaptcha(driver, sio, rect, handle).iniciar()
    dono._transmissor_captcha = novo
    return novo


def parar_transmissao(dono):
    atual = getattr(dono, '_transmissor_captcha', None)
    if atual is not None:
        atual.parar()
