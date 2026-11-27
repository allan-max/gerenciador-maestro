"""Ponte Maestro -> Cotabot na VM (ponte_cotabot.py): pedir a cotação, acompanhar e devolver a planilha."""

import base64
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleo import ponte_cotabot  # noqa: E402

DADOS = {"pedido": "p1", "evento": "310170", "vendedor": "allan", "sites": "",
         "itens": [{"descricao": "Geladeira 400L", "quantidade": 2, "sites": ["Martins", " Martins ", "google", ""],
                    "regras": ["Conferir frete."]}, {"descricao": "  "}]}


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


def tarefa(status, item_status="cotando", planilha=None):
    t = {"id": "abc123abc123", "status": status, "itens": [
        {"numero": 1, "status": item_status, "passos": 5, "passo_atual": "Google: geladeira",
         "resultado": {"anuncios": [{"fornecedor": "Loja", "preco": "R$ 10", "link": "https://x"}], "escolhido": 0}}]}
    if planilha:
        t["planilha"] = planilha
    return t


class PonteCotabotTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"COTABOT_TOKEN": "t" * 40, "COTABOT_URL": "http://vm:8765/"})
        self.env.start()
        self.emitidos = []

    def tearDown(self):
        self.env.stop()

    def emitir(self, nome, dados):
        self.emitidos.append((nome, dados))

    def rodar(self, http):
        return ponte_cotabot.cotar(DADOS, self.emitir, http=http, dormir=lambda s: None, relogio=lambda: 0)

    def test_fluxo_completo_entrega_planilha(self):
        http = Http(Resp(200, {"id": "abc123abc123"}), [
            Resp(200, tarefa("fila", "fila")),
            Resp(200, tarefa("cotando")),
            Resp(200, tarefa("cotando")),          # igual: não emite de novo
            Resp(200, tarefa("pronto", "pronto", planilha="planilha_base_cotacao_310170.xlsx")),
            Resp(200, content=b"XLSX"),
        ])
        pronto = self.rodar(http)
        self.assertEqual(pronto["planilha"]["name"], "planilha_base_cotacao_310170.xlsx")
        self.assertEqual(base64.b64decode(pronto["planilha"]["data"]), b"XLSX")
        self.assertEqual(pronto["tarefa"]["itens"][0]["escolhido"]["fornecedor"], "Loja")
        post = http.pedidos[0]
        self.assertEqual(post[1], "http://vm:8765/api/cotar")
        self.assertEqual(post[2]["headers"]["Authorization"], "Bearer " + "t" * 40)
        self.assertEqual(len(post[2]["json"]["itens"]), 1)   # item sem descrição sai
        item = post[2]["json"]["itens"][0]
        self.assertEqual(item["sites"], ["Martins", "google"])   # sem repetição nem vazio
        self.assertEqual(item["regras"], ["Conferir frete."])
        estados = [d["estado"] for n, d in self.emitidos if n == "cotabot_status"]
        self.assertEqual(estados, ["enviando", "fila", "cotando"])
        self.assertEqual(self.emitidos[-1][0], "cotabot_pronto")

    def test_sem_token_nao_chama_a_vm(self):
        with mock.patch.dict(os.environ, {"COTABOT_TOKEN": ""}):
            http = Http(Resp(200, {"id": "x"}))
            self.assertIsNone(self.rodar(http))
        self.assertEqual(http.pedidos, [])
        self.assertIn("COTABOT_TOKEN", self.emitidos[-1][1]["mensagem"])

    def test_vm_fora_do_ar(self):
        self.assertIsNone(self.rodar(Http(ConnectionError("recusou"))))
        self.assertEqual(self.emitidos[-1][1]["estado"], "erro")
        self.assertIn("fora do alcance", self.emitidos[-1][1]["mensagem"])

    def test_vm_recusa_token(self):
        self.assertIsNone(self.rodar(Http(Resp(401, {"erro": "token inválido"}))))
        self.assertIn("token inválido", self.emitidos[-1][1]["mensagem"])

    def test_quedas_seguidas_desistem(self):
        gets = [ConnectionError("caiu")] * ponte_cotabot.FALHAS_SEGUIDAS
        self.assertIsNone(self.rodar(Http(Resp(200, {"id": "abc123abc123"}), gets)))
        self.assertIn("fora do alcance", self.emitidos[-1][1]["mensagem"])

    def test_piscada_de_rede_continua(self):
        http = Http(Resp(200, {"id": "abc123abc123"}), [
            ConnectionError("piscou"),
            Resp(200, tarefa("pronto", "pronto", planilha="p.xlsx")),
            Resp(200, content=b"X"),
        ])
        self.assertIsNotNone(self.rodar(http))

    def test_pronto_sem_planilha_vira_erro(self):
        t = tarefa("pronto", "pronto")
        t["erro_planilha"] = "modelo sumiu"
        self.assertIsNone(self.rodar(Http(Resp(200, {"id": "abc123abc123"}), [Resp(200, t)])))
        self.assertIn("modelo sumiu", self.emitidos[-1][1]["mensagem"])

    def test_iniciar_ignora_pedido_repetido(self):
        with mock.patch.object(ponte_cotabot, "cotar", side_effect=lambda *a, **k: None), \
                mock.patch.object(ponte_cotabot.threading, "Thread") as th:
            self.assertTrue(ponte_cotabot.iniciar({"pedido": "p9"}, self.emitir))
            self.assertFalse(ponte_cotabot.iniciar({"pedido": "p9"}, self.emitir))
            self.assertFalse(ponte_cotabot.iniciar({}, self.emitir))
            self.assertEqual(th.call_count, 1)
        ponte_cotabot._ativos.discard("p9")


if __name__ == "__main__":
    unittest.main()
