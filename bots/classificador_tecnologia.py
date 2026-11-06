# -*- coding: utf-8 -*-
"""
Classificador de Itens de Informática, Hardware, TVs e Tecnologia.

Fluxo em 2 etapas (executado APÓS a identificação de Marca/Modelo):
  1. Pré-identificação leve (heurística de raízes/indícios + marcas de TI):
     Filtra apenas itens suspeitos (0 tokens para itens industriais comuns).
  2. Confirmação via IA Cloud (Claude Haiku 4.5 com JSON Schema):
     Analisa a descrição completa do item e confirma se realmente pertence ao segmento
     (descartando falsos positivos como refletores industriais, peças mecânicas, etc.).
  3. Seleção determinística de 3 a 5 fornecedores (bots/fornecedores_ti.py + fornecedores_ti.json),
     com marca/modelo/categoria já extraídos: sem IA, sem rede, com cache.
"""
import os
import re
import json
import hashlib
import logging
import unicodedata
import requests
from typing import Dict, List, Optional, Any

logger = logging.getLogger("CoupaApp")

try:
    from . import fornecedores_ti
except ImportError:  # bots/ no sys.path (robô rodando de dentro da pasta)
    import fornecedores_ti

# Lista oficial de fornecedores de tecnologia: vem da configuração única bots/fornecedores_ti.json
FORNECEDORES_TECNOLOGIA: List[str] = fornecedores_ti.nomes_fornecedores()

# Raízes enxutas de palavras-chave e abreviações comuns em cotações (tolerante a truncamentos e abreviações)
_PADROES_SUSPEITOS_TI = re.compile(
    r'\b('
    # Computadores, notebooks, tablets e mobile
    r'NOTEBOOK\w*|NOTE\s+I[3579]|LAPTOP\w*|ULTRABOOK\w*|CHROMEBOOK\w*|MACBOOK\w*|'
    r'COMPUTAD\w*|MICROCOMPUT\w*|DESKTOP\w*|WORKSTATION\w*|ALL[\s\-]*IN[\s\-]*ONE|'
    r'MINI[\s\-]*PC|NUC\b|SERVIDOR\w*|STORAGE\w*|NAS\b|'
    r'TABLET\w*|IPAD\w*|SMARTPHONE\w*|CELULAR\w*|IPHONE\w*|GALAXY\s+TAB\w*|'
    # TVs, monitores e projeção de vídeo
    r'TELEVIS\w*|SMART\s*TV|TV\s+(?:LED|QLED|OLED|UHD|4K|FULL\s*HD|\d{2}\s*POL)|'
    r'MONITOR\s+(?:LED|LCD|IPS|CURVO|GAMER|PROFISSIONAL|WIDE|\d{2})|MONITOR\b|'
    r'DATASHOW|PROJETOR\s+(?:MULTIM\w*|HDMI|ANSI|EPSON|BENQ)|VIDEOWALL|'
    # Periféricos, acessórios de informática e impressão
    r'MOUSE\w*|TECLAD\w*|WEBCAM\w*|HEADSET\w*|HEADPHONE\w*|FONE\s+DE\s+OUVIDO|'
    r'MOCHILA\s+P/\s*NOTE\w*|MALETA\s+P/\s*NOTE\w*|'
    r'DOCK\s*STATION|DOCKING|HUB\s+USB|PEN\s*DRIVE|PENDRIVE|CARTAO\s+(?:SD|MICRO\s*SD|MEMORIA)|'
    r'IMPRESSORA\w*|MULTIFUNCION\w*|SCANNER\w*|PLOTTER\w*|IMPRESSAO\s+3D|'
    r'TONER\w*|CARTUCHO\s+(?:DE\s+)?(?:TINTA|TONER|HP|EPSON|BROTHER)|'
    r'LEITOR\s+(?:DE\s+)?(?:COD\w*|BARRAS|BIOMETR\w*|RFID|SMART\s*CARD|QRCODE)|'
    r'COLETOR\s+(?:DE\s+)?DADOS|PDA\b|'
    # Energia para TI
    r'NO[\s\-]*BREAK\w*|NOBREAK\w*|UPS\s+\d+\s*(?:VA|KVA)|ESTABILIZADOR\s+\d+\s*VA|'
    # Componentes de hardware e armazenamento
    r'PLACA[\s\-]*MAE|MOTHERBOARD|PLACA\s+DE\s+VIDEO|GPU\b|GEFORCE|RADEON|RTX\s*\d{4}|'
    r'PROCESSADOR\s+(?:INTEL|AMD|RYZEN|CORE|XEON)|'
    r'MEMORIA\s+(?:RAM|DDR[345]|SODIMM|DIMM)|DDR[345]\s+\d+\s*GB|'
    r'SSD\b|NVME\b|M\.2\s+\d+\s*(?:GB|TB)|HD\s+(?:EXTERNO|SATA|SAS|\d+\s*TB)|DISCO\s+RIGIDO|'
    # Redes, conectividade e telecom de TI
    r'SWITCH\s+(?:GIGABIT|GERENCIAVEL|POE|\d+\s*PORTAS|ETHERNET|CISCO|ARUBA|TP[\s\-]*LINK|INTELBRAS)|'
    r'ROTEADOR\w*|ACCESS\s+POINT|WIFI\s*6|WI[\s\-]*FI\b|BLUETOOTH\b|WIF/BLUETO\w*|MODEM\w*|STARLINK|'
    r'PATCH\s+(?:PANEL|CORD)|CAT6\w*|TRANSCEIVER\s+SFP|GBIC\b|FIREWALL\w*|'
    r'CABO\s+(?:HDMI|DISPLAYPORT|USB|UTP|REDE\s+CAT)'
    r')\b',
    re.IGNORECASE,
)

# Marcas fortemente associadas a Informática, Hardware, TVs e Tecnologia
_MARCAS_TI = {
    "DELL", "LENOVO", "HP", "HEWLETT PACKARD", "HPE", "ASUS", "ACER", "APPLE",
    "LOGITECH", "MICROSOFT", "SAMSUNG", "LG", "AOC", "BENQ", "TCL",
    "HISENSE", "SONY", "EPSON", "BROTHER", "CANON", "LEXMARK", "KYOCERA", "RICOH",
    "ZEBRA", "HONEYWELL", "DATALOGIC", "ELGIN", "BEMATECH", "CISCO", "ARUBA",
    "UBIQUITI", "MIKROTIK", "TP-LINK", "TPLINK", "D-LINK", "DLINK", "INTELBRAS",
    "FURUKAWA", "FORTINET", "SONICWALL", "KINGSTON", "SANDISK", "SEAGATE",
    "WESTERN DIGITAL", "WD", "CRUCIAL", "CORSAIR", "ADATA", "GIGABYTE", "MSI",
    "ASROCK", "INTEL", "AMD", "NVIDIA", "APC", "SMS", "NHS", "TS SHARA", "RAGTECH",
    "POSITIVO", "VAIO", "MULTILASER", "POLY", "PLANTRONICS", "JABRA",
    "WACOM", "TARGUS", "BAMBU LAB", "CREALITY", "STARLINK"
}

# Padrões que desqualificam falsos positivos óbvios da indústria pesada na pré-triagem
_FALSOS_POSITIVOS_INDUSTRIAIS = re.compile(
    r'\b('
    r'PROJETOR\s+LUMINOSO|REFLETOR\s+LED|ILUMINACAO\s+PUBLICA|'
    r'ANALISADOR\s+QUIMICO|SPECTRAFLOW|'
    r'VALVULA\s+MANIFOLD|HIDRAULIC\w*|PNEUMATIC\w*|'
    r'PARAFUSO\s+CAB|ROTOR\s*;\s*SUBAPLICACAO|ROLAMENTO\s+RIGIDO'
    r')\b',
    re.IGNORECASE,
)

# Cache em memória para não consultar a IA duas vezes para o mesmo item
_CACHE_CLASSIFICACAO_TI: Dict[str, Optional[Dict[str, Any]]] = {}

# Schema JSON estrito para o Claude Haiku 4.5: a IA só confirma o segmento e a categoria.
# A escolha dos fornecedores é determinística (fornecedores_ti), sem tokens.
_ROTULOS_CATEGORIA: List[str] = fornecedores_ti.rotulos_categorias()

_SCHEMA_CLASSIFICACAO_TI = {
    "type": "object",
    "properties": {
        "pertence_segmento": {"type": "boolean"},
        "categoria": {"type": "string"},
        "incerteza": {"type": "boolean"},
    },
    "required": ["pertence_segmento", "categoria", "incerteza"],
    "additionalProperties": False,
}

_SYSTEM_CLASSIFICADOR_TI = (
    "Classifique se o item de cotação pertence ao segmento de Informática, Hardware, TVs/Monitores, Eletrônicos ou Tecnologia de TI/Telecom. "
    "Rejeite (pertence_segmento=false) materiais elétricos/mecânicos industriais (refletores/projetores de iluminação LED, disjuntores, "
    "contatores, sensores industriais, analisadores químicos, monitores de gases, CLPs, inversores, válvulas, motores, ferramentas, EPIs, móveis). "
    "Se pertencer, em 'categoria' use exatamente um destes rótulos: " + "; ".join(_ROTULOS_CATEGORIA) + ". "
    "Se não pertencer, categoria=\"\". Marque incerteza=true quando a descrição não permitir ter certeza."
)


def _remover_acentos(txt: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", str(txt or ""))
        if unicodedata.category(c) != "Mn"
    )


def pre_identificar_suspeito_ti(texto_item: str, dados_ia: Optional[Dict[str, str]] = None) -> bool:
    """
    Etapa leve de pré-identificação (0 tokens):
    Verifica se a descrição ou a marca extraída apresenta indícios de pertencer
    ao segmento de Informática, Hardware, TVs ou Tecnologia.
    """
    if not texto_item or not str(texto_item).strip():
        return False

    texto_norm = _remover_acentos(texto_item).upper()

    # Descarta falsos positivos industriais óbvios (ex.: "PROJETOR LUMINOSO MONTADO", "PROCESSADOR ... ANALISADOR QUIMICO")
    if _FALSOS_POSITIVOS_INDUSTRIAIS.search(texto_norm):
        return False

    # 1. Verifica se a Marca já extraída pertence ao ecossistema de TI/TV/Hardware
    if dados_ia and isinstance(dados_ia, dict):
        marca = _remover_acentos(dados_ia.get("marca", "")).strip().upper()
        if marca and marca in _MARCAS_TI:
            return True

    # 2. Verifica palavras-chave e raízes de tecnologia no texto
    if _PADROES_SUSPEITOS_TI.search(texto_norm):
        return True

    return False


def confirmar_tecnologia_ia(texto_item: str, dados_ia: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """
    Envia apenas os itens suspeitos à IA Cloud (Claude Haiku 4.5 com JSON Schema estrito)
    para confirmar se pertencem ao segmento de tecnologia e devolver a categoria (sem escolher fornecedores).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        # Fallback seguro caso a chave da IA não esteja configurada no ambiente
        return {
            "pertence_segmento": True,
            "categoria": "",
            "incerteza": True,
        }

    marca = (dados_ia or {}).get("marca", "")
    modelo = (dados_ia or {}).get("modelo", "") or (dados_ia or {}).get("part_number", "")
    contexto_item = str(texto_item or "").strip()
    if marca or modelo:
        contexto_item = f"{contexto_item} | Marca identificada: {marca} | Modelo: {modelo}"
    contexto_item = re.sub(r'\s+', ' ', contexto_item)[:700]

    chave_cache = hashlib.sha1(contexto_item.upper().encode("utf-8")).hexdigest()
    if chave_cache in _CACHE_CLASSIFICACAO_TI:
        return _CACHE_CLASSIFICACAO_TI[chave_cache]

    modelo_claude = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001").strip()

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": modelo_claude,
                "max_tokens": 80,
                "temperature": 0,
                "system": _SYSTEM_CLASSIFICADOR_TI,
                "output_config": {
                    "format": {
                        "type": "json_schema",
                        "schema": _SCHEMA_CLASSIFICACAO_TI,
                    }
                },
                "messages": [
                    {"role": "user", "content": contexto_item}
                ],
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"   ⚠️ Claude Classificador TI retornou status {resp.status_code}: {resp.text[:160]}")
            return None

        corpo = resp.json()
        blocos = corpo.get("content") or []
        texto_json = blocos[0].get("text", "{}") if blocos else "{}"
        dados = json.loads(texto_json)

        pertence = bool(dados.get("pertence_segmento"))
        if not pertence:
            _CACHE_CLASSIFICACAO_TI[chave_cache] = None
            return None

        resultado = {
            "pertence_segmento": True,
            "categoria": str(dados.get("categoria") or "").strip(),
            "incerteza": bool(dados.get("incerteza")),
        }
        _CACHE_CLASSIFICACAO_TI[chave_cache] = resultado
        return resultado
    except Exception as e:
        logger.warning(f"   ⚠️ Falha ao classificar item de tecnologia na IA: {e}")
        return None


def formatar_onde_encontrar(recomendacao: Optional[Dict[str, Any]]) -> str:
    """Linha compacta 'ONDE ENCONTRAR? Agis | Mazer | SND' (sugestões de busca, sem afirmar estoque)."""
    return fornecedores_ti.formatar_texto(recomendacao)


def analisar_item_tecnologia(item_dict: Dict[str, Any], texto_completo: str, dados_ia: Optional[Dict[str, str]] = None) -> Optional[str]:
    """
    Ponto único de entrada chamado após as etapas de identificação (Banco, Regex, IA).
    Analisa um item individualmente:
      1. Pré-identifica se é suspeito de TI/Hardware/TV/Tecnologia (0 tokens se não for).
      2. Se suspeito, confirma o segmento com a IA Cloud já existente (com cache).
      3. Seleciona de 3 a 5 fornecedores pela configuração local, com marca/modelo/categoria
         já extraídos (fornecedores_ti: determinístico, sem IA e sem rede).
    """
    if not pre_identificar_suspeito_ti(texto_completo, dados_ia):
        return None

    logger.info("   💻 Indício de TI/Tecnologia detectado. Confirmando classificação na IA...")
    resultado_ti = confirmar_tecnologia_ia(texto_completo, dados_ia)
    if not resultado_ti or not resultado_ti.get("pertence_segmento"):
        logger.info("   ℹ️ Item descartado pela IA (não pertence ao segmento de Informática/TV/Tecnologia).")
        return None

    dados = dados_ia or {}
    recomendacao = fornecedores_ti.recomendar(
        texto_completo,
        marca=dados.get("marca", ""),
        modelo=dados.get("modelo", ""),
        part_number=dados.get("part_number", ""),
        categoria_ia=resultado_ti.get("categoria", ""),
    )
    texto_onde = formatar_onde_encontrar(recomendacao)
    if not texto_onde:
        return None
    if isinstance(item_dict, dict):
        item_dict["onde_encontrar"] = texto_onde
        item_dict["onde_encontrar_info"] = recomendacao
        item_dict["classificacao_ti"] = resultado_ti
        logger.info(f"   ✅ [TI Confirmado - {recomendacao.get('rotulo')}] {texto_onde}")
    return texto_onde


def agrupar_itens_equivalentes(itens) -> int:
    """No mesmo evento, itens com o mesmo produto apontam para o primeiro em vez de repetir a lista."""
    return fornecedores_ti.agrupar_itens_equivalentes(itens)


def adicionar_paragrafo_onde_encontrar(doc, item: Dict[str, Any]) -> None:
    """
    Renderiza a seção discreta 'ONDE ENCONTRAR?' no documento Word (.docx), logo abaixo do item
    confirmado: até 5 fornecedores com link de busca, separados por ' | '.
    """
    if not isinstance(item, dict) or not item.get("onde_encontrar"):
        return
    fornecedores_ti.adicionar_secao_word(doc, item.get("onde_encontrar_info"))
