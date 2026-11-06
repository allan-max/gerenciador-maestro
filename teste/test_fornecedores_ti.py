# -*- coding: utf-8 -*-
"""ONDE ENCONTRAR?: seleção determinística de fornecedores, links, Word do robô (Coupa e Vale),
mapa enviado ao portal e ausência de duplicidades."""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

from docx import Document

from bots import fornecedores_ti as F
from bots.classificador_tecnologia import _CACHE_CLASSIFICACAO_TI

NOMES_OFICIAIS = [
    "Agis", "Mazer", "Pauta", "SND", "Fagundez", "GMI", "ScanSource", "Ingram Micro", "Martins", "Gazin",
    "Fujioka", "Golden", "All Nations", "Mais Próxima", "Rcell", "Gibson", "Tambasa", "Belmicro", "Coleção",
    "iByte", "Miranda",
]


def _nomes(rec):
    return [f["nome"] for f in rec["fornecedores"]]


def _resposta_ia(pertence=True, categoria="Computadores/Notebooks"):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"content": [{"type": "text", "text": json.dumps(
        {"pertence_segmento": pertence, "categoria": categoria if pertence else "", "incerteza": False})}]}
    return resp


class TestConfiguracao(unittest.TestCase):
    def test_config_tem_os_21_fornecedores_sem_repeticao(self):
        self.assertEqual(F.nomes_fornecedores(), NOMES_OFICIAIS)

    def test_dominios_e_buscas_validos(self):
        with open(F.CAMINHO_CONFIG, encoding="utf-8") as f:
            bruto = json.load(f)
        ids = {c["id"] for c in bruto["categorias"]}
        for forn in bruto["fornecedores"]:
            self.assertTrue(set(forn["especialidades"]) <= ids, forn["nome"])
            self.assertTrue(set(forn["atende"]) <= ids, forn["nome"])
            if forn.get("busca"):
                self.assertTrue(forn["busca"].startswith("https://"), forn["nome"])
                self.assertIn("{q}", forn["busca"], forn["nome"])
        for marca, cat in bruto["marcas_categoria"].items():
            self.assertIn(cat, ids, marca)

    def test_toda_categoria_tem_ao_menos_3_fornecedores_com_link(self):
        config = F.carregar_config()
        for cat in config["categorias"]:
            rec = F.selecionar_fornecedores(cat["id"])
            self.assertGreaterEqual(len(rec), 3, cat["id"])
            self.assertLessEqual(len(rec), 5, cat["id"])

    def test_config_com_bom_e_recarga_por_mtime(self):
        pasta = tempfile.mkdtemp()
        try:
            caminho = os.path.join(pasta, "f.json")
            with open(F.CAMINHO_CONFIG, encoding="utf-8") as f:
                conteudo = f.read()
            with open(caminho, "w", encoding="utf-8-sig") as f:
                f.write(conteudo)
            config = F.carregar_config(caminho)
            self.assertEqual(len(config["fornecedores"]), 21)
            self.assertIs(F.carregar_config(caminho), config)  # cache enquanto o arquivo não muda
        finally:
            F.carregar_config(F.CAMINHO_CONFIG)
            shutil.rmtree(pasta, ignore_errors=True)


class TestSelecao(unittest.TestCase):
    def assert_selecao_valida(self, rec):
        nomes = _nomes(rec)
        self.assertGreaterEqual(len(nomes), 3)
        self.assertLessEqual(len(nomes), 5)
        self.assertEqual(len(nomes), len(set(nomes)), "fornecedor repetido")
        urls = [f["url"] for f in rec["fornecedores"] if f["url"]]
        self.assertEqual(len(urls), len(set(urls)), "link repetido")
        for url in urls:
            self.assertEqual(urlparse(url).scheme, "https")

    def test_notebook_dell_prioriza_quem_trabalha_com_a_marca(self):
        rec = F.recomendar("NOTEBOOK I7 16GB SSD 512GB TELA 14 POL", "DELL", "LATITUDE 5440")
        self.assert_selecao_valida(rec)
        self.assertEqual(rec["categoria"], "computadores")
        self.assertEqual(_nomes(rec)[:2], ["Agis", "Ingram Micro"])
        self.assertEqual(rec["termo_busca"], "DELL LATITUDE 5440")
        agis = rec["fornecedores"][0]["url"]
        self.assertIn("agis.com.br", agis)
        self.assertIn("DELL+LATITUDE+5440", agis)

    def test_coletor_zebra_vai_para_especialista_em_automacao(self):
        rec = F.recomendar("COLETOR DE DADOS ANDROID 4G", "ZEBRA", "TC21")
        self.assert_selecao_valida(rec)
        self.assertEqual(rec["categoria"], "automacao_comercial")
        self.assertEqual(_nomes(rec)[0], "ScanSource")

    def test_tv_e_tablet_vao_para_atacado_e_telefonia(self):
        tv = F.recomendar("PT | TV Led 85polegadas 4k Tela de 85", "SAMSUNG", "")
        self.assert_selecao_valida(tv)
        self.assertEqual(tv["categoria"], "tvs")
        self.assertIn("Fujioka", _nomes(tv))
        tablet = F.recomendar("13557306 || TABLET DIG;256GB;SAMSUNG/SMX736BZAHZTO - PT", "SAMSUNG", "SMX736BZAHZTO")
        self.assert_selecao_valida(tablet)
        self.assertEqual(tablet["categoria"], "smartphones_tablets")
        self.assertIn("Rcell", _nomes(tablet))

    def test_produto_vem_antes_do_acessorio_citado(self):
        self.assertEqual(F.categoria_por_texto("NOTEBOOK CORE I5 COM MOUSE E MOCHILA")[0], "computadores")
        self.assertEqual(F.categoria_por_texto("MOCHILA P/ NOTEBOOK 15.6 POL")[0], "perifericos")
        self.assertEqual(F.categoria_por_texto("TONER HP 85A P/ IMPRESSORA LASERJET")[0], "suprimentos_impressao")
        self.assertEqual(F.categoria_por_texto("IMPRESSORA DE ETIQUETAS TERMICA")[0], "automacao_comercial")

    def test_marca_suja_ou_ausente_e_reaproveitada_do_texto(self):
        self.assertEqual(F.identificar_marca("HEWLETT PACKARD"), "HP")
        self.assertEqual(F.identificar_marca("TPLINK"), "TP-LINK")
        self.assertEqual(F.identificar_marca("DELL TECHNOLOGIES"), "DELL")
        self.assertEqual(F.identificar_marca("", "MOUSE SEM FIO LOGITECH M170"), "LOGITECH")

    def test_categoria_da_ia_existente_e_marca_tipica_quando_o_texto_nao_ajuda(self):
        self.assertEqual(F.identificar_categoria("EQUIPAMENTO XYZ", "Redes")[0], "redes")
        self.assertEqual(F.identificar_categoria("EQUIPAMENTO XYZ", "", "ZEBRA")[0], "automacao_comercial")
        self.assertEqual(F.identificar_categoria("EQUIPAMENTO XYZ")[0], "geral")

    def test_falsos_positivos_industriais_nao_recebem_secao(self):
        self.assertIsNone(F.recomendar("MONITOR DE GASES PORTATIL INDUSTRIAL MULTIGAS", "MSA", "ALTAIR 4XR"))
        self.assertIsNone(F.recomendar("SWITCH DE PRESSAO 10 BAR", "DANFOSS", "KP35"))

    def test_selecao_deterministica_e_sem_rede(self):
        with patch("requests.post") as post, patch("requests.get") as get:
            a = F.recomendar("MONITOR LED 24 POL IPS", "LG", "24MK430H")
            b = F.recomendar("MONITOR LED 24 POL IPS", "LG", "24MK430H")
        self.assertEqual(a, b)
        post.assert_not_called()
        get.assert_not_called()

    def test_texto_formatado(self):
        rec = F.recomendar("SWITCH GIGABIT 24 PORTAS POE", "CISCO", "CBS350-24P")
        self.assertEqual(F.formatar_texto(rec), "ONDE ENCONTRAR? " + " | ".join(_nomes(rec)))


class TestAgrupamento(unittest.TestCase):
    def test_produtos_equivalentes_no_mesmo_evento_nao_repetem_lista(self):
        itens = [
            {"item_num": "1", "onde_encontrar_info": F.recomendar("MOUSE OPTICO USB", "LOGITECH", "M90")},
            {"item_num": "2", "onde_encontrar_info": F.recomendar("TECLADO USB ABNT2", "LOGITECH", "K120")},
            {"item_num": "3", "onde_encontrar_info": F.recomendar("MOUSE OPTICO USB PRETO", "LOGITECH", "M90")},
        ]
        self.assertEqual(F.agrupar_itens_equivalentes(itens), 1)
        self.assertNotIn("mesmo_que_item", itens[0]["onde_encontrar_info"])
        self.assertNotIn("mesmo_que_item", itens[1]["onde_encontrar_info"])
        self.assertEqual(itens[2]["onde_encontrar_info"]["mesmo_que_item"], "1")
        # idempotente: rodar de novo não acumula marcações
        self.assertEqual(F.agrupar_itens_equivalentes(itens), 1)


class TestWordRobo(unittest.TestCase):
    def setUp(self):
        _CACHE_CLASSIFICACAO_TI.clear()
        self.pasta = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.pasta, ignore_errors=True)

    def _evento(self):
        return {
            "numero_evento": "990001", "req_num": "R1", "data_vencimento": "10/12/2026 - 10:00",
            "tipo_frete": "CIF", "vencimento_tabela": "",
            "itens": [
                {"item_num": "1", "titulo": "Não", "descricao": "NOTEBOOK I7 16GB SSD 512GB DELL LATITUDE 5440",
                 "quantidade": "2", "local_entrega": "VITORIA", "texto_item": "", "texto_compra": ""},
                {"item_num": "2", "titulo": "Não", "descricao": "VALVULA ESFERA INOX 1/2 POL SWAGELOK SS-43GS4",
                 "quantidade": "1", "local_entrega": "VITORIA", "texto_item": "", "texto_compra": ""},
                {"item_num": "3", "titulo": "Não", "descricao": "NOTEBOOK I7 16GB SSD 512GB DELL LATITUDE 5440 (RESERVA)",
                 "quantidade": "1", "local_entrega": "VITORIA", "texto_item": "", "texto_compra": ""},
            ],
        }

    def _dados(self, texto):
        if "DELL" in texto:
            return {"marca": "DELL", "modelo": "LATITUDE 5440", "part_number": "LATITUDE 5440"}
        return {"marca": "SWAGELOK", "modelo": "SS-43GS4", "part_number": "SS-43GS4"}

    def _rodar(self, classe):
        bot = classe.__new__(classe)
        bot.dados_processados_lote = [self._evento()]
        bot.ARQUIVO_MARCAS_BLOQUEADAS = os.path.join(self.pasta, "nao_existe.xlsx")
        bot.PASTA_RELATORIOS = self.pasta
        bot.PASTA_BLOQUEADOS = self.pasta
        bot.extrair_dados_gemini = self._dados
        bot.encontrar_vendedor_similar = lambda it, d: None
        bot.atualizar_excel = lambda dados: False  # não grava banco/JSONL no teste
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}), \
                patch("bots.classificador_tecnologia.requests.post", return_value=_resposta_ia()) as post:
            bot.gerar_word()
        arquivos = [a for a in os.listdir(self.pasta) if a.endswith(".docx")]
        self.assertEqual(len(arquivos), 1)
        return Document(os.path.join(self.pasta, arquivos[0])), post

    def _conferir(self, doc, post):
        # Só a classificação já existente: 1 chamada por notebook (descrições diferentes), nenhuma para a
        # válvula (barrada na pré-triagem) e nenhuma chamada nova para escolher fornecedores
        self.assertEqual(post.call_count, 2)
        corpo = post.call_args.kwargs["json"]
        self.assertNotIn("fornecedores", corpo["output_config"]["format"]["schema"]["properties"])

        secoes = [p for p in doc.paragraphs if p.text.startswith("ONDE ENCONTRAR?")]
        self.assertEqual(len(secoes), 2, "item 1 com lista, item 3 com referência, válvula sem seção")
        completa, referencia = secoes
        nomes = [h.text for h in completa.hyperlinks]
        self.assertGreaterEqual(len(nomes), 3)
        self.assertLessEqual(len(nomes), 5)
        self.assertEqual(len(nomes), len(set(nomes)))
        self.assertIn(" | ".join(nomes), completa.text)
        self.assertIn("estoque e preço não confirmados", completa.text)
        for h in completa.hyperlinks:
            self.assertIn("DELL", h.address)
        self.assertEqual(referencia.text, "ONDE ENCONTRAR? mesmos fornecedores do item 1")
        self.assertNotIn("Onde encontrar?", "\n".join(p.text for p in doc.paragraphs))  # formato antigo não volta
        # ordem: a seção vem depois da linha de valor do item
        textos = [p.text for p in doc.paragraphs]
        self.assertLess(textos.index("VALOR DE VENDA: R$ __________________ CUSTO UNIT: R$__________________"),
                        textos.index(completa.text))
        rels = [r for r in doc.part.rels.values() if r.is_external]
        self.assertEqual(len(rels), len({r.target_ref for r in rels}), "relação de link duplicada")

    def test_word_coupa(self):
        from bots.coupa import CoupaScraper
        self._conferir(*self._rodar(CoupaScraper))

    def test_word_vale_base_metais(self):
        from bots.vale_bot import ValeScraper
        self._conferir(*self._rodar(ValeScraper))


class TestMapaPortal(unittest.TestCase):
    def test_mapa_da_planilha_por_item_sem_ia(self):
        linhas = [
            {"COTAÇÃO": "77001", "ITEM": "(Item 1) NOTEBOOK I5 8GB\n(Item 2) PARAFUSO SEXTAVADO M10\n(Item 3) NOTEBOOK I5 8GB",
             "MARCAS ": "(Item 1) LENOVO\n(Item 2) GERDAU\n(Item 3) LENOVO",
             "MODELOS": "(Item 1) THINKPAD E14\n(Item 2) M10X50\n(Item 3) THINKPAD E14"},
            {"COTAÇÃO": "77002", "ITEM": "MONITOR DE GASES MULTIGAS", "MARCAS ": "MSA", "MODELOS": "ALTAIR"},
            {"COTAÇÃO": "77003", "ITEM": "SMART TV 50 POL 4K", "MARCAS ": "SAMSUNG", "MODELOS": "UN50CU7700"},
            {"COTAÇÃO": "88888", "ITEM": "NOTEBOOK", "MARCAS ": "DELL", "MODELOS": ""},
        ]
        with patch("requests.post") as post:
            mapa = F.mapa_onde_encontrar(linhas, {"77001", "77002", "77003"})
        post.assert_not_called()
        self.assertEqual(set(mapa), {"77001", "77003"})  # industrial e cotação fora do Coupa/Vale ficam de fora
        itens = {r["item"]: r for r in mapa["77001"]}
        self.assertEqual(set(itens), {"1", "3"})
        self.assertEqual(itens["1"]["termo_busca"], "LENOVO THINKPAD E14")
        self.assertEqual(itens["3"]["mesmo_que_item"], "1")
        self.assertEqual(mapa["77003"][0]["termo_busca"], "SAMSUNG UN50CU7700")
        json.dumps(mapa)  # vai pelo socket


if __name__ == "__main__":
    unittest.main()
