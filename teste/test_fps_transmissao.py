import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent


class TestFpsTransmissao60(unittest.TestCase):
    def test_vale_bot_configurado_60fps_e_cdp(self):
        caminho = RAIZ / 'gerenciador' / 'bots' / 'vale_bot.py'
        conteudo = caminho.read_text(encoding='utf-8')
        self.assertIn('(1.0 / 60.0)', conteudo)
        self.assertIn("Page.captureScreenshot", conteudo)
        self.assertIn("Input.dispatchMouseEvent", conteudo)
        self.assertIn("--remote-allow-origins=*", conteudo)

    def test_coupa_autenticacao_configurado_60fps_e_cdp(self):
        caminho = RAIZ / 'gerenciador' / 'bots' / 'coupa' / 'mixins' / 'autenticacao.py'
        conteudo = caminho.read_text(encoding='utf-8')
        self.assertIn('(1.0 / 60.0)', conteudo)
        self.assertIn("Page.captureScreenshot", conteudo)
        self.assertIn("Input.dispatchMouseEvent", conteudo)
        self.assertIn("--remote-allow-origins=*", conteudo)

    def test_coupa_resposta_configurado_60fps_e_cdp(self):
        caminho = RAIZ / 'gerenciador' / 'bots' / 'coupa' / 'mixins' / 'resposta.py'
        conteudo = caminho.read_text(encoding='utf-8')
        self.assertIn('(1.0 / 60.0)', conteudo)
        self.assertIn("Page.captureScreenshot", conteudo)
        self.assertIn("Input.dispatchMouseEvent", conteudo)

    def test_interfaces_web_sem_flicker_opacity(self):
        coupa_html = (RAIZ / 'portaismaestro' / 'public' / 'coupa.html').read_text(encoding='utf-8')
        vale_html = (RAIZ / 'portaismaestro' / 'public' / 'vale.html').read_text(encoding='utf-8')
        tela_js = (RAIZ / 'portaismaestro' / 'public' / 'minhas-cotacoes' / 'tela.js').read_text(encoding='utf-8')

        for html in (coupa_html, vale_html):
            self.assertIn("elImgCaptcha.src = src;", html)
            self.assertNotIn("this.style.opacity = '0.5'", html)

        self.assertNotIn("img.style.opacity = '0.5'", tela_js)


if __name__ == '__main__':
    unittest.main()
