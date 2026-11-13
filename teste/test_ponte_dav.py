"""Ponte Maestro -> Cotafrete (ponte_dav.py): pedir o DAV, esperar e devolver ao portal."""

import base64
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ponte_dav  # noqa: E402

PLANILHA = {"name": "base.xlsx", "data": base64.b64encode(b"xlsx").decode("ascii")}
DADOS = {"evento": "301015", "portal": "coupa", "vendedor": "allan", "planilha": PLANILHA}


class Resp:
    def __init__(self, status, corpo=None, content=b""):
        self.status_code = status
        self._corpo = corpo
        self.content = content

    def json(self):
        if self._corpo is None:
            raise ValueError("sem json")
        return self._corpo


class Http:
    """Respostas em ordem; guarda o que foi pedido."""

    def __init__(self, post, gets=()):
        self._post = post
        self._gets = list(gets)
        self.pedidos = []

    def post(self, url, **kw):
        self.pedidos.append(("POST", url, kw))
        if isinstance(self._post, Exception):
            raise self._post
        return self._post

    def get(self, url, **kw):
        self.pedidos.append(("GET", url, kw))
        r = self._gets.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def corpo(estado, **campos):
    base = {"id": 7, "cotacao": "301015", "estado": estado, "numero": None,
            "avisos": [], "erros": [], "itens": [], "pdf": False}
    base.update(campos)
    return base


class TestPonteDav(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"COTAFRETE_TOKEN": "tok", "COTAFRETE_URL": "http://cf:8000/"})
        self.env.start()
        self.emitidos = []

    def tearDown(self):
        self.env.stop()

    def emitir(self, nome, dados):
        self.emitidos.append((nome, dados))

    def rodar(self, http, relogio=None):
        return ponte_dav.gerar_dav(dict(DADOS), self.emitir, http=http, dormir=lambda s: None,
                                   relogio=relogio or (lambda: 0))

    def status(self):
        return [d for n, d in self.emitidos if n == "status_dav"]

    def test_pronto_baixa_o_pdf_e_devolve_davs_e_ncms(self):
        itens = [{"item": 1, "codigo": "13370", "ncm": None, "origem": "cadastro HSE"},
                 {"item": 2, "codigo": None, "ncm": "84716053", "origem": "IA"}]
        http = Http(Resp(201, corpo("conferindo")), [
            Resp(200, corpo("gerando")),
            Resp(200, corpo("pronto", numero="16700", pdf=True, itens=itens, avisos=["Item 1: confira"])),
            Resp(200, content=b"%PDF-1.4")])
        pronto = self.rodar(http)
        self.assertEqual(pronto["davs"], [{"name": "DAV_16700.pdf",
                                           "data": base64.b64encode(b"%PDF-1.4").decode("ascii")}])
        self.assertEqual(pronto["itens"], [{"item": 1, "ncm": "", "origem": "cadastro HSE"},
                                           {"item": 2, "ncm": "84716053", "origem": "IA"}])
        self.assertEqual(pronto["avisos"], ["Item 1: confira"])
        self.assertIn(("dav_pronto", pronto), self.emitidos)
        self.assertEqual([s["estado"] for s in self.status()], ["gerando", "gerando", "gerando", "pronto"])
        metodo, url, kw = http.pedidos[0]
        self.assertEqual((metodo, url), ("POST", "http://cf:8000/api/dav"))
        self.assertEqual(kw["headers"], {"Authorization": "Bearer tok"})
        self.assertEqual(kw["data"], {"vendedor": "allan", "cotacao": "301015"})
        self.assertEqual(kw["files"]["planilha"], ("base.xlsx", b"xlsx"))
        self.assertEqual(http.pedidos[-1][1], "http://cf:8000/api/dav/7/pdf")

    def test_erros_da_conferencia_voltam_com_os_motivos(self):
        http = Http(Resp(201, corpo("conferindo")), [
            Resp(200, corpo("erros", erros=["Item 1: código inativo", "Item 2: sem NCM"]))])
        self.assertIsNone(self.rodar(http))
        ultimo = self.status()[-1]
        self.assertEqual(ultimo["estado"], "erro")
        self.assertEqual(ultimo["mensagem"], "Item 1: código inativo · Item 2: sem NCM")
        self.assertNotIn("dav_pronto", [n for n, _ in self.emitidos])

    def test_planilha_recusada_na_hora_422(self):
        http = Http(Resp(422, corpo("erros", erros=["Planilha antiga"])))
        self.assertIsNone(self.rodar(http))
        self.assertEqual(self.status()[-1]["mensagem"], "Planilha antiga")
        self.assertEqual(len(http.pedidos), 1)

    def test_incerto_nunca_e_refeito(self):
        http = Http(Resp(200, corpo("incerto", numero="16702", erros=["Confira no HSE"])))
        self.assertIsNone(self.rodar(http))
        self.assertTrue(self.status()[-1]["mensagem"].startswith("DAV em dúvida no HSE"))
        self.assertEqual([p[0] for p in http.pedidos], ["POST"])   # nem pergunta de novo

    def test_cotafrete_fora_do_ar(self):
        self.assertIsNone(self.rodar(Http(ConnectionError("recusado"))))
        self.assertIn("Cotafrete fora do alcance (http://cf:8000)", self.status()[-1]["mensagem"])

    def test_token_errado(self):
        self.assertIsNone(self.rodar(Http(Resp(401, {"detail": "Token inválido."}))))
        self.assertEqual(self.status()[-1]["mensagem"], "O Cotafrete recusou o pedido do DAV: Token inválido.")

    def test_sem_token_nem_chama(self):
        os.environ["COTAFRETE_TOKEN"] = ""
        http = Http(Resp(201, corpo("conferindo")))
        self.assertIsNone(self.rodar(http))
        self.assertEqual(self.status()[-1]["mensagem"], ponte_dav.SEM_TOKEN)
        self.assertEqual(http.pedidos, [])

    def test_sem_planilha(self):
        dados = dict(DADOS, planilha=None)
        ponte_dav.gerar_dav(dados, self.emitir, http=Http(None))
        self.assertEqual(self.status()[-1]["estado"], "erro")

    def test_piscada_de_rede_tenta_de_novo(self):
        http = Http(Resp(201, corpo("gerando")), [
            ConnectionError("x"), ConnectionError("y"),
            Resp(200, corpo("erro", erros=["HSE fora"]))])
        self.rodar(http)
        self.assertEqual(self.status()[-1]["mensagem"], "HSE fora")

    def test_rede_caida_de_vez_desiste(self):
        http = Http(Resp(201, corpo("gerando")), [ConnectionError("x")] * ponte_dav.FALHAS_SEGUIDAS)
        self.rodar(http)
        self.assertIn("fora do alcance", self.status()[-1]["mensagem"])

    def test_passou_do_limite_desiste_sem_refazer(self):
        tempos = iter([0, ponte_dav.LIMITE_S + 1])
        http = Http(Resp(201, corpo("gerando")))
        self.rodar(http, relogio=lambda: next(tempos))
        self.assertIn("Confira no Cotafrete (envio 7)", self.status()[-1]["mensagem"])
        self.assertEqual([p[0] for p in http.pedidos], ["POST"])

    def test_pdf_que_nao_baixa_pede_reenvio(self):
        http = Http(Resp(200, corpo("pronto", numero="16703")), [Resp(404, {"detail": "PDF não encontrado"})])
        self.assertIsNone(self.rodar(http))
        self.assertIn("Reenvie a resposta (não gera outro DAV)", self.status()[-1]["mensagem"])

    def test_iniciar_ignora_a_mesma_cotacao_rodando(self):
        solta = threading.Event()

        class Lento(Http):
            def post(self, url, **kw):
                solta.wait(5)
                return Resp(422, corpo("erros", erros=["x"]))

        self.assertTrue(ponte_dav.iniciar(dict(DADOS), self.emitir, http=Lento(None)))
        self.assertFalse(ponte_dav.iniciar(dict(DADOS), self.emitir, http=Lento(None)))
        solta.set()
        for th in threading.enumerate():
            if th.name == "ponte-dav-301015":
                th.join(5)
        self.assertTrue(ponte_dav.iniciar(dict(DADOS), self.emitir, http=Http(Resp(422, corpo("erros")))))


if __name__ == "__main__":
    unittest.main()
