# -*- coding: utf-8 -*-
"""
Extrator Híbrido de Marca, Modelo e Part Number (Coupa / Vale Base Metals / Vale)
Pipeline em 3 Níveis:
  1. Banco de Dados (COTAÇÕES.json) -> 0 tokens
  2. Estrutura de REGEX (Padrões Vale / Coupa) -> 0 tokens
  3. IA com Structured Outputs (Claude Haiku via JSON Schema nativo, com fallback) -> mínimo de tokens
"""
import os
import re
import json
import hashlib
import logging
import requests
from typing import Dict, Set, Tuple, Optional

logger = logging.getLogger("CoupaApp")

# Termos que NUNCA são marcas ou modelos reais
TERMOS_IGNORADOS = {
    "", "NONE", "NAN", "N/A", "NA", "NAO", "NÃO", "SIM", "NULL",
    "DIVERSOS", "DIVERSAS", "VARIOS", "VÁRIOS", "SEM MARCA", "S/MARCA", "S/M",
    "SEM MODELO", "S/MODELO", "NACIONAL", "IMPORTADO", "OUTROS", "OUTRAS",
    "PADRAO", "PADRÃO", "CONFORME", "SIMILAR", "EQUIVALENTE", "QUALQUER",
    "A DEFINIR", "NAO APLICAVEL", "NÃO APLICÁVEL", "NAO INFORMADO", "NÃO INFORMADO",
    "FABRICANTE", "MODELO", "MARCA", "REFERENCIA", "REFERÊNCIA", "VALE",
    "PT", "UN", "PC", "PÇ", "CJ", "KG", "MT", "LT", "PR", "JG", "RL", "CX", "FD"
}

# Substantivos comuns de materiais/cores que já apareceram sujos na coluna MARCAS antiga
SUBSTANTIVOS_COMUNS_IGNORADOS = {
    "PARAFUSO", "PORCA", "ARRUELA", "ELETROCALHA", "QUADRO", "CABO", "TUBO",
    "CHAPA", "VALVULA", "VÁLVULA", "CONECTOR", "DISJUNTOR", "CONTATOR",
    "ROLAMENTO", "ROTOR", "BUCHA", "TAMPA", "LUVA", "BOTA", "CAIXA", "PAINEL",
    "FITA", "ETIQUETA", "MOCHILA", "POLTRONA", "MESA", "CADEIRA", "ARMARIO",
    "ARMÁRIO", "FILTRO", "SENSOR", "LAMPADA", "LÂMPADA", "MOTOR", "BOMBA",
    "MANGUEIRA", "ANEL", "ABRACADEIRA", "ABRAÇADEIRA", "MOUSE", "TECLADO",
    "MONITOR", "IMPRESSORA", "TABLET", "NOTEBOOK", "COMPUTADOR", "PROJETOR",
    "LUMINARIA", "LUMINÁRIA", "TOMADA", "PLUGUE", "FUSIVEL", "FUSÍVEL",
    "RELE", "RELÉ", "FONTE", "BATERIA", "PILHA", "CARREGADOR", "XICARA",
    "XÍCARA", "COPO", "GARRAFA", "CANETA", "MARCADOR", "PAPEL", "MDF",
    "ACO", "AÇO", "PLASTICO", "PLÁSTICO", "PLAST", "BORRACHA", "MADEIRA",
    "PRETO", "PRETA", "BRANCO", "BRANCA", "AZUL", "VERDE", "AMARELO",
    "VERMELHO", "CINZA", "MARROM", "INCOLOR", "TRANSPARENTE", "GALVANIZADO",
    "INOX", "CARBONO", "LATÃO", "LATAO", "COBRE", "ALUMINIO", "ALUMÍNIO",
    "INTERNACIONAL", "ARTEF", "PROFI", "COGUMELO", "BLINDA", "B", "DIGITAL",
    "PARTES E PECAS", "PARTES E PEÇAS", "PECAS", "PEÇAS", "ACESSORIOS", "ACESSÓRIOS"
}

MARCAS_CURTAS_PERMITIDAS = {
    "3M", "GE", "HP", "LG", "SKF", "WEG", "ABB", "SMC", "NSK", "FAG",
    "SEW", "IFM", "P&G", "JBL", "AOC", "AMD", "MSI", "ZTE", "CAT", "JCB"
}

ABREVIACOES_BARRA_IGNORADAS = {
    "S/FIO", "C/FIO", "C/SCROLL", "S/SCROLL", "WIFI/BLUETOOTH", "WIF/BLUETO",
    "C/CABO", "S/CABO", "C/ASA", "S/ASA", "C/PIRES", "S/PIRES", "S/LOG",
    "C/LOGO", "S/LOGOMARCA", "C/LOGOMARCA", "C/TAMPA", "S/TAMPA", "C/RODA",
    "C/RODIZIO", "C/AJUSTE", "C/BRACO", "ENCOSTO/ASSENTO", "BATERIA/PILHA",
    "AC/DC", "CA/CC", "E/S", "I/O", "ON/OFF", "NF/NA", "NA/NF", "POL/MM",
    "INTERNO/EXTERNO", "ENTRADA/SAIDA", "DIREITO/ESQUERDO", "SUPERIOR/INFERIOR",
    "AZUL/BRANCO", "PRETO/BRANCO", "VERDE/AMARELO", "CZ/MR", "MR/PT", "CZ/PT",
    "MALH/PES", "PLAST/PU", "TECID/XPE", "COURO/SINTETICO", "ACO/INOX"
}

# Cache global em memória do catálogo do banco de dados
_CATALOGO_CACHE = {
    "carregado": False,
    "caminho": "",
    "mtime": 0.0,
    "marcas": set(),          # Set[str]
    "marcas_ordenadas": [],   # List[Tuple[str, re.Pattern]]
    "modelos": {},            # Dict[str, str] -> modelo_upper: marca_associada (ou "")
    "modelos_por_marca": {},  # Dict[str, Set[str]]
}

# Cache em memória de extrações da IA para evitar chamadas repetidas no mesmo item
_CACHE_IA_MEMORIA: Dict[str, Dict[str, str]] = {}


def _limpar_prefixo_item(valor: str) -> str:
    """Remove '(Item 1)', 'ITEM 2:', 'FABRICANTE:', etc. de valores vindos do banco."""
    v = str(valor or "").strip().upper()
    v = re.sub(r'^\s*\(?\s*ITEM\s*\d+\s*\)?\s*[:\-]?\s*', '', v, flags=re.IGNORECASE)
    v = re.sub(r'^(?:FABRICANTE\s*/\s*MARCA|FABRICANTE|MARCA|MODELO|REF(?:ERENCIA)?|PN)\s*:\s*', '', v, flags=re.IGNORECASE)
    v = v.strip(" ;,.-/|")
    return v


def _eh_codigo_sap_vale(token: str) -> bool:
    """Identifica códigos numéricos de 8 dígitos de material SAP da Vale (ex: 13557306, 15395567, 35005854)."""
    t = token.strip()
    return bool(re.fullmatch(r'(?:1[0-9]|2[0-9]|3[0-9]|4[0-9]|5[0-9])\d{6}', t))


def _eh_medida_ou_tensao(token: str) -> bool:
    """Evita confundir medidas (100X50MM, 220V, 165W, 5000K, 350BAR, 240ML) com modelos."""
    t = token.strip().upper()
    if re.fullmatch(r'\d+(?:[.,]\d+)?\s*(?:MM|CM|M|POL|KG|G|MG|L|ML|V|VCA|VCC|KV|A|MA|W|KW|VA|KVA|HZ|KHZ|MHZ|BAR|PSI|RPM|K|C|GRAUS|UN|PC|DB|OHM|UF|NF|PF|GB|TB|MB|L|ML|M2|M3)\b', t):
        return True
    if re.fullmatch(r'\d+(?:[.,]\d+)?(?:X\d+(?:[.,]\d+)?)+\s*(?:MM|CM|M|POL)?', t):
        return True
    if re.fullmatch(r'\d+\s*[-A]\s*\d+\s*(?:V|VCA|VCC|A|W|MM|BAR|HZ)', t):
        return True
    return False


def _marca_valida(marca: str) -> bool:
    m = _limpar_prefixo_item(marca)
    if not m or m in TERMOS_IGNORADOS or m in SUBSTANTIVOS_COMUNS_IGNORADOS:
        return False
    if len(m) < 2 or len(m) > 40:
        return False
    if len(m) == 2 and m not in MARCAS_CURTAS_PERMITIDAS:
        return False
    if m.isdigit() or _eh_medida_ou_tensao(m):
        return False
    # Não aceitar frases longas ou descrições técnicas como marca
    if len(m.split()) > 4 or any(ch in m for ch in [":", ";", "=", "(", ")"]):
        return False
    return True


def _modelo_valido(modelo: str) -> bool:
    mo = _limpar_prefixo_item(modelo)
    if not mo or mo in TERMOS_IGNORADOS or mo in SUBSTANTIVOS_COMUNS_IGNORADOS:
        return False
    if len(mo) < 3 or len(mo) > 45:
        return False
    if _eh_codigo_sap_vale(mo) or _eh_medida_ou_tensao(mo):
        return False
    # Modelos puros de catálogo precisam ter pelo menos 1 dígito ou hífen/barra técnica
    # para não dar falso positivo em palavras comuns do português
    if not any(c.isdigit() for c in mo) and len(mo) < 5:
        return False
    if len(mo.split()) > 4 or any(ch in mo for ch in [":", ";", "("]):
        return False
    return True


def carregar_catalogo_banco(caminho_json: Optional[str] = None) -> dict:
    """
    Carrega e indexa as Marcas e Modelos já existentes no COTAÇÕES.json.
    Usa cache em memória baseado no mtime do arquivo.
    """
    if not caminho_json:
        caminho_json = r'\\SERVIDOR2\Publico\ALLAN\database\Banco-de-dados\COTAÇÕES.json'
    elif os.path.isdir(caminho_json):
        if not caminho_json.endswith('Banco-de-dados'):
            candidato = os.path.join(caminho_json, 'Banco-de-dados', 'COTAÇÕES.json')
            if os.path.exists(candidato):
                caminho_json = candidato
            else:
                caminho_json = os.path.join(caminho_json, 'COTAÇÕES.json')
        else:
            caminho_json = os.path.join(caminho_json, 'COTAÇÕES.json')

    try:
        if not os.path.exists(caminho_json):
            return _CATALOGO_CACHE
        mtime = os.path.getmtime(caminho_json)
        if _CATALOGO_CACHE["carregado"] and _CATALOGO_CACHE["caminho"] == caminho_json and _CATALOGO_CACHE["mtime"] == mtime:
            return _CATALOGO_CACHE
    except Exception:
        return _CATALOGO_CACHE

    marcas: Set[str] = set(MARCAS_CURTAS_PERMITIDAS)
    modelos: Dict[str, str] = {}
    modelos_por_marca: Dict[str, Set[str]] = {}

    try:
        with open(caminho_json, 'r', encoding='utf-8') as f:
            dados = json.load(f)
        registros = dados.get('COTAÇÃO', dados if isinstance(dados, list) else [])

        for row in registros:
            raw_marcas = str(row.get('MARCAS ', row.get('MARCAS', '')) or '').split('\n')
            raw_modelos = str(row.get('MODELOS', row.get('MODELO', '')) or '').split('\n')

            marcas_limpas = [_limpar_prefixo_item(x) for x in raw_marcas if x.strip()]
            modelos_limpos = [_limpar_prefixo_item(x) for x in raw_modelos if x.strip()]

            for idx, m in enumerate(marcas_limpas):
                if _marca_valida(m):
                    marcas.add(m)
                    if idx < len(modelos_limpos):
                        mo = modelos_limpos[idx]
                        if _modelo_valido(mo) and mo != m:
                            modelos[mo] = m
                            modelos_por_marca.setdefault(m, set()).add(mo)

            for mo in modelos_limpos:
                if _modelo_valido(mo) and mo not in marcas and mo not in modelos:
                    # Só indexa modelo órfão se tiver letras E números (código técnico inequívoco)
                    if any(c.isdigit() for c in mo) and any(c.isalpha() for c in mo) and len(mo) >= 4:
                        modelos[mo] = ""

        # Ordena marcas da maior para a menor (ex: "BAMBU LAB" antes de "LAB")
        marcas_ordenadas = [
            (m, re.compile(r'(?<![A-Z0-9])' + re.escape(m) + r'(?![A-Z0-9])'))
            for m in sorted(marcas, key=len, reverse=True)
        ]

        _CATALOGO_CACHE.update({
            "carregado": True,
            "caminho": caminho_json,
            "mtime": mtime,
            "marcas": marcas,
            "marcas_ordenadas": marcas_ordenadas,
            "modelos": modelos,
            "modelos_por_marca": modelos_por_marca,
        })
    except Exception as e:
        logger.warning(f"   ⚠️ Não foi possível indexar catálogo do banco: {e}")

    return _CATALOGO_CACHE


def _remover_codigo_sap_inicial(texto: str) -> str:
    """Remove o código SAP de 8 dígitos do início do título para não confundi-lo com modelo."""
    t = str(texto or "").strip()
    t = re.sub(r'^\s*(?:\(Item\s*\d+\)\s*)?\d{7,9}\s*(?:\|\||-)\s*', '', t, flags=re.IGNORECASE)
    return t


# ==============================================================================
# 1º NÍVEL: BUSCA NO BANCO DE DADOS DE MARCAS E MODELOS (COTAÇÕES.json)
# ==============================================================================
def extrair_por_banco_dados(texto_item: str, caminho_json: Optional[str] = None) -> Dict[str, str]:
    """
    1º Nível: Procura no texto se há Marca e Modelo já cadastrados no COTAÇÕES.json.
    """
    res = {"marca": "", "modelo": "", "part_number": ""}
    if not texto_item or not str(texto_item).strip():
        return res

    catalogo = carregar_catalogo_banco(caminho_json)
    if not catalogo.get("carregado"):
        return res

    texto_upper = _remover_codigo_sap_inicial(texto_item).upper()

    # 1. Procura Marca conhecida no texto que também tenha seu Modelo par no texto (alta confiança)
    marca_isolada = ""
    for marca, padrao_marca in catalogo["marcas_ordenadas"]:
        if marca in texto_upper and padrao_marca.search(texto_upper):
            if not marca_isolada:
                marca_isolada = marca
            modelos_da_marca = sorted(catalogo["modelos_por_marca"].get(marca, set()), key=len, reverse=True)
            for mod in modelos_da_marca:
                if mod in texto_upper and re.search(r'(?<![A-Z0-9])' + re.escape(mod) + r'(?![A-Z0-9])', texto_upper):
                    res["marca"] = marca
                    res["modelo"] = mod
                    res["part_number"] = mod
                    return res

    # 2. Procura tokens do texto que batam exatamente com um Modelo técnico do catálogo
    tokens_candidatos = re.findall(r'[A-Z0-9][A-Z0-9.\-/+]{3,35}[A-Z0-9]', texto_upper)
    modelos_cat = catalogo["modelos"]
    for tok in tokens_candidatos:
        tok_limpo = tok.strip(".,;-/")
        if tok_limpo in modelos_cat and not _eh_codigo_sap_inicial_ou_medida(tok_limpo):
            res["modelo"] = tok_limpo
            res["part_number"] = tok_limpo
            if modelos_cat[tok_limpo]:
                res["marca"] = modelos_cat[tok_limpo]
            break

    if not res["marca"] and res["modelo"] and marca_isolada:
        res["marca"] = marca_isolada

    return res


def _eh_codigo_sap_inicial_ou_medida(tok: str) -> bool:
    return _eh_codigo_sap_vale(tok) or _eh_medida_ou_tensao(tok)


# ==============================================================================
# 2º NÍVEL: ESTRUTURA DE REGEX (PADRÕES VALE / COUPA)
# ==============================================================================
def _limpar_valor_capturado(val: str) -> str:
    v = str(val or "").strip().upper()
    v = re.split(r'\s*(?:;|\|\||\n|\r)\s*', v)[0].strip()
    # Corta se tiver próxima etiqueta colada na mesma linha
    v = re.split(r'\b(?:FABRICANTE|FABR\.?|MARCA|MODELO|MOD\.?|REFER[EÊ]NCIA|REF\.?|PART\s*NUMBER|APLICACAO|APLICAÇÃO|SUBAPLICACAO|LOCAL|COR|MATERIAL|DIMENSOES|TENSAO|POTENCIA)\s*:', v)[0].strip()
    v = v.strip(" ;,.-/|")
    if v in TERMOS_IGNORADOS or v.replace(" ", "") in TERMOS_IGNORADOS or v in SUBSTANTIVOS_COMUNS_IGNORADOS:
        return ""
    return v


def extrair_por_regex(texto_item: str) -> Dict[str, str]:
    """
    2º Nível: Extrai Marca, Modelo e Part Number usando os padrões estruturados da Vale e do Coupa:
      - FABRICANTE/REFERENCIA: MARCA / MODELO
      - FABRICANTE: ... | MARCA: ... | MODELO: ... | REF: ... | PN: ...
      - Padrão de título Vale: ;MARCA/MODELO - PT
      - Padrão de sufixo Vale: - MODELO MARCA no final da descrição
    """
    res = {"marca": "", "modelo": "", "part_number": ""}
    if not texto_item or not str(texto_item).strip():
        return res

    texto = _remover_codigo_sap_inicial(texto_item).upper()

    # 1. Padrão composto: FABRICANTE/REFERENCIA: MARCA / REFERENCIA
    m_fab_ref = re.search(
        r'\b(?:FABRICANTE\s*/\s*REFER[EÊ]NCIA|FABR\.?\s*/\s*REF\.?|MARCA\s*/\s*MODELO|MARCA\s*/\s*REF(?:ER[EÊ]NCIA)?)\s*:\s*([^;/|\n\r]+?)\s*/\s*([^;|\n\r]+)',
        texto
    )
    if m_fab_ref:
        fab = _limpar_valor_capturado(m_fab_ref.group(1))
        ref = _limpar_valor_capturado(m_fab_ref.group(2))
        if fab and fab not in TERMOS_IGNORADOS:
            res["marca"] = fab
        if ref and ref not in TERMOS_IGNORADOS and not _eh_medida_ou_tensao(ref):
            res["part_number"] = ref
            res["modelo"] = ref

    # 2. Etiquetas explícitas: FABRICANTE: / FABR.: / MARCA:
    if not res["marca"]:
        for m_fab in re.finditer(r'\b(?:FABRICANTE|FABR\.?|MARCA)\s*:\s*([^;/|\n\r]+)', texto):
            cand = _limpar_valor_capturado(m_fab.group(1))
            if cand and cand not in TERMOS_IGNORADOS and not _eh_medida_ou_tensao(cand):
                res["marca"] = cand
                break

    # 3. Etiquetas explícitas: PART NUMBER / PN / REFERENCIA / REF / COD. FABR
    if not res["part_number"]:
        for m_pn in re.finditer(r'\b(?:PART\s*NUMBER|P\.?N\.?|REFER[EÊ]NCIA|REF\.?|C[OÓ]D(?:IGO)?\.?\s*(?:DO\s*)?FABR(?:ICANTE)?)\s*:\s*([^;|\n\r]+)', texto):
            cand = _limpar_valor_capturado(m_pn.group(1))
            if cand and cand not in TERMOS_IGNORADOS and not _eh_medida_ou_tensao(cand):
                res["part_number"] = cand
                break

    # 4. Etiquetas explícitas: MODELO: / MOD.:
    for m_mod in re.finditer(r'\b(?:MODELO|MOD\.?)\s*:\s*([^;|\n\r]+)', texto):
        cand = _limpar_valor_capturado(m_mod.group(1))
        if cand and cand not in TERMOS_IGNORADOS and not _eh_medida_ou_tensao(cand):
            # Na Vale, às vezes colocam "MODELO: SIEMENS; FABRICANTE/REFERENCIA: RIO TECH / 4.26.3593"
            # Se já temos part_number da referência, mantemos o part_number como principal
            if not res["modelo"]:
                res["modelo"] = cand
            break

    if res["part_number"] and not res["modelo"]:
        res["modelo"] = res["part_number"]

    # 5. Padrão de título da Vale: ;MARCA/MODELO - PT (ou no fim do segmento de título)
    # Ex: TABLET DIG;256GB;SAMSUNG/SMX736BZAHZTO - PT
    # Ex: POLTRONA;ACOLCH PT;NY;PT;CAVALETTI/28001 - PT
    # Ex: IMPRESSORA 3D;220V;BAMBU LAB/H2DPRO
    if not (res["marca"] and res["modelo"]):
        for m_tit in re.finditer(r';\s*([A-Z0-9][A-Z0-9\s.&\-]{1,24}?)\s*/\s*([A-Z0-9][A-Z0-9.\-+_]{2,30})\s*(?:-\s*PT\b|\|\||$)', texto):
            par_completo = f"{m_tit.group(1).strip()}/{m_tit.group(2).strip()}"
            if par_completo in ABREVIACOES_BARRA_IGNORADAS:
                continue
            cand_marca = m_tit.group(1).strip()
            cand_mod = m_tit.group(2).strip()
            # Na Vale, "VALE/ET1650KNE40031" é especificação técnica interna da Vale, não fabricante
            if cand_marca in TERMOS_IGNORADOS or cand_marca in {"S", "C", "WIF", "WIFI", "AC", "CA", "NY", "PT", "BR", "CZ", "MR", "AZ", "VD", "VM", "AM"}:
                continue
            # O modelo no padrão ;MARCA/MODELO deve conter pelo menos um dígito
            if not any(c.isdigit() for c in cand_mod) or _eh_medida_ou_tensao(cand_mod):
                continue
            if not res["marca"]:
                res["marca"] = cand_marca
            if not res["modelo"]:
                res["modelo"] = cand_mod
            if not res["part_number"]:
                res["part_number"] = cand_mod
            break

    # 6. Padrão de sufixo no final da descrição da Vale: "... - MODELO MARCA"
    # Ex: "... COMPRIMENTO CABO: S/CABO - M585 LOGITECH"
    # Ex: "... CONEXAO ELETRICA: C/CABO - IB-10M-30SA-NF/XL TECNOTRON"
    # Ex: "... APLICACAO: ANALISADOR QUIMICO - E01-0702-059 SPECTRAFLOW"
    if not (res["marca"] and res["modelo"]):
        m_suf = re.search(r'\s-\s+([A-Z0-9][A-Z0-9.\-/+]{2,28})\s+([A-Z][A-Z0-9&.\-]{2,20})\s*$', texto)
        if m_suf:
            cand_mod = m_suf.group(1).strip(" ;,.-")
            cand_marca = m_suf.group(2).strip(" ;,.-")
            if (
                any(c.isdigit() for c in cand_mod)
                and not _eh_medida_ou_tensao(cand_mod)
                and cand_marca not in TERMOS_IGNORADOS
                and cand_marca not in SUBSTANTIVOS_COMUNS_IGNORADOS
                and not _eh_medida_ou_tensao(cand_marca)
            ):
                if not res["marca"]:
                    res["marca"] = cand_marca
                if not res["modelo"]:
                    res["modelo"] = cand_mod
                if not res["part_number"]:
                    res["part_number"] = cand_mod

    return res


# ==============================================================================
# 3º NÍVEL: IA COM STRUCTURED OUTPUTS (CLAUDE HAIKU JSON SCHEMA NATIVO)
# ==============================================================================
_CLAUDE_SCHEMA = {
    "type": "object",
    "properties": {
        "marca": {"type": "string"},
        "modelo": {"type": "string"},
        "part_number": {"type": "string"}
    },
    "required": ["marca", "modelo", "part_number"],
    "additionalProperties": False
}


def _compactar_texto_para_ia(texto_item: str, max_chars: int = 550) -> str:
    """Remove textos repetidos, múltiplos espaços e códigos SAP iniciais para gastar o mínimo de tokens."""
    t = _remover_codigo_sap_inicial(texto_item)
    partes = [p.strip() for p in re.split(r'\|\||\n', t) if p.strip()]
    unicas = []
    for p in partes:
        p_norm = re.sub(r'\s+', ' ', p).strip()
        if p_norm and not any(p_norm.lower() in u.lower() for u in unicas):
            unicas.append(p_norm)
    limpo = " | ".join(unicas)
    limpo = re.sub(r'\s+', ' ', limpo).strip()
    return limpo[:max_chars]


def extrair_por_claude_structured(texto_item: str) -> Optional[Dict[str, str]]:
    """
    3º Nível Principal: Usa Claude Haiku 4.5 com Structured Outputs nativo (output_config.format = json_schema).
    Envia apenas o texto compacto do item, sem prompt conversacional.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    texto_compacto = _compactar_texto_para_ia(texto_item)
    if not texto_compacto:
        return {"marca": "", "modelo": "", "part_number": ""}

    chave_cache = hashlib.sha1(texto_compacto.upper().encode("utf-8")).hexdigest()
    if chave_cache in _CACHE_IA_MEMORIA:
        return dict(_CACHE_IA_MEMORIA[chave_cache])

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
                "max_tokens": 90,
                "temperature": 0,
                "system": "Extraia fabricante (marca), modelo e part_number do item industrial. Use string vazia \"\" quando ausente no texto.",
                "output_config": {
                    "format": {
                        "type": "json_schema",
                        "schema": _CLAUDE_SCHEMA,
                    }
                },
                "messages": [
                    {"role": "user", "content": texto_compacto}
                ],
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"   ⚠️ Claude API retornou status {resp.status_code}: {resp.text[:180]}")
            return None

        corpo = resp.json()
        blocos = corpo.get("content") or []
        texto_json = blocos[0].get("text", "{}") if blocos else "{}"
        dados = json.loads(texto_json)

        resultado = {
            "marca": str(dados.get("marca") or "").strip().upper(),
            "modelo": str(dados.get("modelo") or "").strip().upper(),
            "part_number": str(dados.get("part_number") or "").strip().upper(),
        }
        # Limpa caso a IA tenha devolvido N/A ou DIVERSOS
        for k in ("marca", "modelo", "part_number"):
            if resultado[k] in TERMOS_IGNORADOS:
                resultado[k] = ""

        _CACHE_IA_MEMORIA[chave_cache] = dict(resultado)
        return resultado
    except Exception as e:
        logger.warning(f"   ⚠️ Falha na chamada Structured Output do Claude: {e}")
        return None


# ==============================================================================
# PIPELINE COMPLETO EM 3 NÍVEIS (BANCO -> REGEX -> IA STRUCTURED OUTPUTS)
# ==============================================================================
def extrair_dados_item_hibrido(
    texto_item: str,
    caminho_json: Optional[str] = None,
    fallback_ia_fn=None
) -> Dict[str, str]:
    """
    Executa a esteira de 3 níveis:
      1º Banco de Dados (COTAÇÕES.json)
      2º Estrutura de REGEX
      3º IA com Structured Outputs (Claude Haiku 4.5 -> fallback se necessário)
    """
    if not texto_item or not str(texto_item).strip():
        return {"marca": "", "modelo": "", "part_number": ""}

    # 1º NÍVEL: Banco de Dados
    dados_bd = extrair_por_banco_dados(texto_item, caminho_json=caminho_json)
    marca = dados_bd.get("marca", "")
    modelo = dados_bd.get("modelo", "")
    part_number = dados_bd.get("part_number", "")

    if marca and (modelo or part_number):
        logger.info(f"   ⚡ [Nível 1 - Banco] Marca: '{marca}' | Modelo: '{modelo or part_number}' (0 tokens)")
        return {
            "marca": marca,
            "modelo": modelo or part_number,
            "part_number": part_number or modelo,
        }

    # 2º NÍVEL: Estrutura de REGEX
    dados_rx = extrair_por_regex(texto_item)
    if not marca and dados_rx.get("marca"):
        marca = dados_rx["marca"]
    if not modelo and dados_rx.get("modelo"):
        modelo = dados_rx["modelo"]
    if not part_number and dados_rx.get("part_number"):
        part_number = dados_rx["part_number"]

    # Se o Regex achou um modelo que já existe no banco vinculado a uma marca, completa a marca!
    if (modelo or part_number) and not marca:
        catalogo = carregar_catalogo_banco(caminho_json)
        chave_mod = part_number or modelo
        if chave_mod in catalogo.get("modelos", {}) and catalogo["modelos"][chave_mod]:
            marca = catalogo["modelos"][chave_mod]

    if marca and (modelo or part_number):
        logger.info(f"   ⚡ [Nível 2 - Regex/BD] Marca: '{marca}' | Modelo: '{modelo or part_number}' (0 tokens)")
        return {
            "marca": marca,
            "modelo": modelo or part_number,
            "part_number": part_number or modelo,
        }

    # 3º NÍVEL: IA com Structured Outputs (Claude Haiku 4.5)
    dados_ia = extrair_por_claude_structured(texto_item)
    if dados_ia is None and callable(fallback_ia_fn):
        dados_ia = fallback_ia_fn(texto_item)

    if dados_ia:
        if not marca and dados_ia.get("marca"):
            marca = dados_ia["marca"]
        if not modelo and dados_ia.get("modelo"):
            modelo = dados_ia["modelo"]
        if not part_number and dados_ia.get("part_number"):
            part_number = dados_ia["part_number"]
        logger.info(f"   🤖 [Nível 3 - Claude Schema] Marca: '{marca}' | Modelo: '{modelo or part_number}'")

    return {
        "marca": marca,
        "modelo": modelo or part_number,
        "part_number": part_number or modelo,
    }
