# -*- coding: utf-8 -*-
import os
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from bots.extrator_item import (
    extrair_por_banco_dados,
    extrair_por_regex,
    extrair_por_claude_structured,
    extrair_dados_item_hibrido,
    _CACHE_IA_MEMORIA,
)


class TestExtratorItemHibrido(unittest.TestCase):
    def setUp(self):
        _CACHE_IA_MEMORIA.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.json_path = os.path.join(self.temp_dir.name, "COTAÇÕES.json")
        dados_mock = {
            "COTAÇÃO": [
                {
                    "COTAÇÃO": "1001",
                    "ITEM": "POLTRONA PRESIDENTE",
                    "MARCAS": "CAVALETTI",
                    "MODELOS": "28001",
                    "RESPOSTA": "RESPONDIDO",
                },
                {
                    "COTAÇÃO": "1002",
                    "ITEM": "VALVULA MANIFOLD",
                    "MARCAS ": "(Item 1) HYDAC\n(Item 2) SIEMENS",
                    "MODELOS": "(Item 1) 6061220\n(Item 2) 3RT2016",
                    "RESPOSTA": "RESPONDIDO",
                },
            ]
        }
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(dados_mock, f, ensure_ascii=False)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_nivel1_banco_de_dados_encontra_marca_e_modelo_sem_ia(self):
        texto = "13553994 || POLTRONA ACOLCHOADA PRETA CAVALETTI CODIGO 28001"
        res = extrair_por_banco_dados(texto, caminho_json=self.json_path)
        self.assertEqual(res["marca"], "CAVALETTI")
        self.assertEqual(res["modelo"], "28001")
        self.assertEqual(res["part_number"], "28001")

    def test_nivel2_regex_padrao_fabricante_referencia(self):
        texto = "13556081 || ROTOR || ROTOR;SUBAPLICACAO:MOTOR; FABRICANTE/REFERENCIA: SUBRA / RTBHC 0307;"
        res = extrair_por_regex(texto)
        self.assertEqual(res["marca"], "SUBRA")
        self.assertEqual(res["modelo"], "RTBHC 0307")
        self.assertEqual(res["part_number"], "RTBHC 0307")

    def test_nivel2_regex_padrao_titulo_vale_barra(self):
        texto = "13557306 || TABLET DIG;256GB;SAMSUNG/SMX736BZAHZTO - PT || TABLET DIGITAL"
        res = extrair_por_regex(texto)
        self.assertEqual(res["marca"], "SAMSUNG")
        self.assertEqual(res["modelo"], "SMX736BZAHZTO")

    def test_nivel2_regex_padrao_sufixo_descricao(self):
        texto = "15734399 || SENSOR PROX INDUT 10-30VCC - PT || SENSOR PROXIMIDADE; CONEXAO ELETRICA: C/CABO - IB-10M-30SA-NF/XL TECNOTRON"
        res = extrair_por_regex(texto)
        self.assertEqual(res["marca"], "TECNOTRON")
        self.assertEqual(res["modelo"], "IB-10M-30SA-NF/XL")

    def test_nivel2_regex_ignora_diversos_com_espaco_sap(self):
        texto = "13543615 || TUBO ESP || FABRICANTE/REFERENCIA: DIV ERSOS / 1571517503;"
        res = extrair_por_regex(texto)
        self.assertEqual(res["marca"], "")
        self.assertEqual(res["modelo"], "1571517503")

    @patch("bots.extrator_item.requests.post")
    def test_nivel3_claude_structured_outputs_acionado_apenas_quando_necessario(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "content": [
                {
                    "type": "text",
                    "text": '{"marca": "YOKOGAWA", "modelo": "EJX110A", "part_number": "EJX110A-DMS5J"}'
                }
            ]
        }
        mock_post.return_value = mock_resp

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test-key"}):
            # Caso 1: Já tem no Banco (Nível 1) -> NÃO deve chamar Claude
            r1 = extrair_dados_item_hibrido(
                "VALVULA MANIFOLD HYDAC 6061220",
                caminho_json=self.json_path
            )
            self.assertEqual(r1["marca"], "HYDAC")
            self.assertEqual(r1["modelo"], "6061220")
            mock_post.assert_not_called()

            # Caso 2: Tem no Regex (Nível 2) -> NÃO deve chamar Claude
            r2 = extrair_dados_item_hibrido(
                "BUCHA; FABRICANTE: RIO TECH; REFERENCIA: 4.26.3593",
                caminho_json=self.json_path
            )
            self.assertEqual(r2["marca"], "RIO TECH")
            self.assertEqual(r2["modelo"], "4.26.3593")
            mock_post.assert_not_called()

            # Caso 3: Texto livre inédito -> Chama Claude com JSON Schema (Nível 3)
            r3 = extrair_dados_item_hibrido(
                "TRANSMISSOR DE PRESSAO DIFERENCIAL SERIE EJX110A-DMS5J FORNECIDO PELA YOKOGAWA",
                caminho_json=self.json_path
            )
            self.assertEqual(r3["marca"], "YOKOGAWA")
            self.assertEqual(r3["modelo"], "EJX110A")
            self.assertEqual(r3["part_number"], "EJX110A-DMS5J")
            self.assertEqual(mock_post.call_count, 1)
            payload_enviado = mock_post.call_args.kwargs["json"]
            self.assertEqual(payload_enviado["output_config"]["format"]["type"], "json_schema")


if __name__ == "__main__":
    unittest.main()
