import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent   # raiz do gerenciador, qualquer que seja o nome da pasta
PORTAL = RAIZ.parent / 'portaismaestro'          # o portal só existe ao lado na máquina de desenvolvimento


class TestFpsTransmissao60(unittest.TestCase):
    def test_transmissor_60fps_cdp_direto(self):
        conteudo = (RAIZ / 'bots' / 'transmissor_captcha.py').read_text(encoding='utf-8')
        self.assertIn('(1.0 / 60.0)', conteudo)
        self.assertIn("Page.captureScreenshot", conteudo)
        self.assertIn("webSocketDebuggerUrl", conteudo)
        self.assertIn("Page.startScreencast", conteudo)

    def test_vale_bot_usa_transmissor_e_cdp(self):
        conteudo = (RAIZ / 'bots' / 'vale_bot.py').read_text(encoding='utf-8')
        self.assertIn("manter_transmissao(self", conteudo)
        self.assertIn("Input.dispatchMouseEvent", conteudo)
        self.assertIn("--remote-allow-origins=*", conteudo)
        self.assertIn("--disable-backgrounding-occluded-windows", conteudo)

    def test_coupa_autenticacao_usa_transmissor_e_cdp(self):
        conteudo = (RAIZ / 'bots' / 'coupa' / 'mixins' / 'autenticacao.py').read_text(encoding='utf-8')
        self.assertIn("manter_transmissao(self", conteudo)
        self.assertIn("Input.dispatchMouseEvent", conteudo)
        self.assertIn("--remote-allow-origins=*", conteudo)
        self.assertIn("--disable-backgrounding-occluded-windows", conteudo)

    def test_coupa_resposta_usa_transmissor_e_cdp(self):
        conteudo = (RAIZ / 'bots' / 'coupa' / 'mixins' / 'resposta.py').read_text(encoding='utf-8')
        self.assertIn("manter_transmissao(self", conteudo)
        self.assertIn("Input.dispatchMouseEvent", conteudo)

    @unittest.skipUnless((PORTAL / 'public').is_dir(), 'portaismaestro não está ao lado do gerenciador')
    def test_interfaces_web_sem_flicker_opacity(self):
        coupa_html = (PORTAL / 'public' / 'coupa.html').read_text(encoding='utf-8')
        vale_html = (PORTAL / 'public' / 'vale.html').read_text(encoding='utf-8')
        tela_js = (PORTAL / 'public' / 'minhas-cotacoes' / 'tela.js').read_text(encoding='utf-8')

        for html in (coupa_html, vale_html):
            self.assertIn("elImgCaptcha.src = src;", html)
            self.assertNotIn("this.style.opacity = '0.5'", html)

        self.assertNotIn("img.style.opacity = '0.5'", tela_js)


if __name__ == '__main__':
    unittest.main()
