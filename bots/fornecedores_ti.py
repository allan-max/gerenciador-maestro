# -*- coding: utf-8 -*-
"""
Seção "ONDE ENCONTRAR?": seleção determinística de fornecedores para itens de Informática,
Hardware, TVs e Eletrônicos das cotações do Coupa e da Vale Base Metais.

- Reaproveita a marca, o modelo e a categoria que o robô já identificou (extrator_item e
  classificador_tecnologia). Não chama IA, não acessa a rede e não visita os sites dos fornecedores.
- Fornecedores, domínios, buscas, categorias e marcas atendidas ficam num único arquivo local,
  bots/fornecedores_ti.json, lido uma vez e relido só quando muda (mtime).
- Pontuação fixa: marca atendida, portfólio (marca + categoria), especialidade na categoria e linha
  comercial; desempate pela ordem do arquivo. De 3 a 5 fornecedores por produto, sem repetição.
- Os links são buscas pelo produto no site de cada fornecedor: não afirmam estoque nem disponibilidade.
- O mesmo módulo atende o Word do robô e o Word padrão Coupa do portal (API Cloud): o robô calcula e
  envia o resultado junto com a planilha (gerenciador.comando_carregar_planilha_json).
"""
import json
import logging
import os
import re
import threading
import unicodedata
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, quote_plus

logger = logging.getLogger("CoupaApp")

CAMINHO_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fornecedores_ti.json")
ROTULO_SECAO = "ONDE ENCONTRAR?"
NOTA_SECAO = "links de busca; estoque e preço não confirmados"

_TRAVA = threading.Lock()
_ESTADO: Dict[str, Any] = {"caminho": None, "mtime": None, "config": None}


# ------------------------------------------------------------------ normalização
def normalizar(texto: Any) -> str:
    """Maiúsculas, sem acentos e com espaços simples (mesma base para texto, marcas e regras)."""
    t = unicodedata.normalize("NFD", str(texto or ""))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", t.upper()).strip()


def _compacto(texto: Any) -> str:
    """'TP-LINK', 'TP LINK' e 'TPLINK' viram a mesma chave."""
    return re.sub(r"[^A-Z0-9]", "", normalizar(texto))


def _regex_termo(fragmento: str) -> re.Pattern:
    return re.compile(r"(?<![A-Z0-9])(?:" + fragmento + r")", re.IGNORECASE)


def _regex_palavra(palavra: str) -> re.Pattern:
    return re.compile(r"(?<![A-Z0-9])" + re.escape(palavra) + r"(?![A-Z0-9])")


# ------------------------------------------------------------------ configuração
def _preparar(bruto: Dict[str, Any]) -> Dict[str, Any]:
    categorias = []
    ids = set()
    for c in bruto.get("categorias") or []:
        cid = str(c["id"]).strip()
        ids.add(cid)
        categorias.append({
            "id": cid,
            "rotulo": str(c.get("rotulo") or cid),
            "linhas": set(c.get("linhas") or []),
            "regras": [_regex_termo(t) for t in (c.get("termos") or [])],
            "ia": [normalizar(x) for x in (c.get("ia") or [])],
        })
    if "geral" not in ids:
        categorias.append({"id": "geral", "rotulo": "Informática", "linhas": set(), "regras": [], "ia": []})
        ids.add("geral")

    fornecedores = []
    nomes = set()
    for prioridade, f in enumerate(bruto.get("fornecedores") or []):
        nome = str(f["nome"]).strip()
        if not nome or nome.upper() in nomes:
            continue  # nome repetido no arquivo nunca gera fornecedor duplicado
        nomes.add(nome.upper())
        especialidades = set(f.get("especialidades") or []) & ids
        fornecedores.append({
            "nome": nome,
            "dominio": str(f.get("dominio") or "").strip().lower(),
            "busca": f.get("busca") or None,
            "busca_espaco": f.get("busca_espaco") or "+",
            "linhas": set(f.get("linhas") or []),
            "especialidades": especialidades,
            "atende": (set(f.get("atende") or []) & ids) - especialidades,
            "marcas": {_compacto(m) for m in (f.get("marcas") or []) if _compacto(m)},
            "prioridade": prioridade,
        })

    aliases = {_compacto(k): normalizar(v) for k, v in (bruto.get("aliases_marcas") or {}).items()}
    marcas_categoria = {normalizar(k): v for k, v in (bruto.get("marcas_categoria") or {}).items() if v in ids}

    # Marcas conhecidas (para achar a marca dentro de um texto sujo): nome canônico -> regex de palavra inteira
    canonicas = set(marcas_categoria) | set(aliases.values())
    for f in bruto.get("fornecedores") or []:
        canonicas.update(normalizar(m) for m in (f.get("marcas") or []))
    canonicas.discard("")
    indice_compacto = {_compacto(m): m for m in canonicas}
    indice_compacto.update({k: v for k, v in aliases.items()})
    marcas_texto = [(m, _regex_palavra(m)) for m in sorted(canonicas, key=len, reverse=True) if len(_compacto(m)) >= 2]
    aliases_texto = [(_regex_palavra(normalizar(k)), v) for k, v in (bruto.get("aliases_marcas") or {}).items()
                     if len(_compacto(k)) >= 3]

    selecao = bruto.get("selecao") or {}
    return {
        "versao": bruto.get("versao"),
        "categorias": categorias,
        "categorias_por_id": {c["id"]: c for c in categorias},
        "fornecedores": fornecedores,
        "exclusoes": [_regex_termo(x) for x in (bruto.get("exclusoes") or [])],
        "marcas_categoria": marcas_categoria,
        "indice_marcas": indice_compacto,
        "marcas_texto": marcas_texto,
        "aliases_texto": aliases_texto,
        "busca_padrao": bruto.get("busca_padrao") or "https://www.google.com/search?q=site%3A{dominio}+{q}",
        "min": int(selecao.get("min_fornecedores", 3)),
        "max": int(selecao.get("max_fornecedores", 5)),
        "corte": float(selecao.get("corte_relativo", 0.7)),
        "pesos": {"marca": 5, "portfolio": 2, "especialista": 4, "atende": 2, "linha": 1, **(selecao.get("pesos") or {})},
    }


def carregar_config(caminho: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Lê e prepara o JSON (utf-8 com ou sem BOM). Cache em memória enquanto o mtime não mudar."""
    caminho = caminho or CAMINHO_CONFIG
    try:
        mtime = os.path.getmtime(caminho)
    except OSError:
        logger.warning(f"   ⚠️ Configuração de fornecedores não encontrada: {caminho}")
        return None
    with _TRAVA:
        if _ESTADO["config"] is not None and _ESTADO["caminho"] == caminho and _ESTADO["mtime"] == mtime:
            return _ESTADO["config"]
        try:
            with open(caminho, "r", encoding="utf-8-sig") as f:
                config = _preparar(json.load(f))
        except Exception as e:
            logger.warning(f"   ⚠️ Configuração de fornecedores inválida ({caminho}): {e}")
            return _ESTADO["config"] if _ESTADO["caminho"] == caminho else None
        _ESTADO.update({"caminho": caminho, "mtime": mtime, "config": config})
        _selecionar_cache.cache_clear()
        return config


def nomes_fornecedores() -> List[str]:
    config = carregar_config()
    return [f["nome"] for f in config["fornecedores"]] if config else []


def rotulos_categorias() -> List[str]:
    config = carregar_config()
    return [c["rotulo"] for c in config["categorias"]] if config else []


# ------------------------------------------------------------------ identificação (reaproveita o já extraído)
def identificar_marca(marca: Any = "", texto: Any = "") -> str:
    """Marca canônica: primeiro a marca extraída pelo robô; se vazia ou desconhecida, a do texto do item."""
    config = carregar_config()
    if not config:
        return normalizar(marca)
    m = normalizar(marca)
    if m:
        canonica = config["indice_marcas"].get(_compacto(m))
        if canonica:
            return canonica
        for nome, regex in config["marcas_texto"]:  # 'DELL TECHNOLOGIES' -> DELL
            if regex.search(m):
                return nome
        return m  # marca fora da configuração: vale para a busca, não pontua
    t = normalizar(texto)
    for regex, canonica in config["aliases_texto"]:
        if regex.search(t):
            return canonica
    for nome, regex in config["marcas_texto"]:
        if regex.search(t):
            return nome
    return ""


def item_excluido(texto: Any) -> bool:
    """Itens industriais que só parecem de TI (monitor de gases, switch de pressão, projetor luminoso...)."""
    config = carregar_config()
    t = normalizar(texto)
    return bool(config and any(r.search(t) for r in config["exclusoes"]))


def categoria_por_texto(texto: Any) -> Tuple[str, str]:
    """(categoria, termo do produto). Vence o termo que aparece primeiro no texto: o nome do produto
    vem antes dos acessórios citados ('NOTEBOOK ... COM MOUSE'); empate: termo mais longo."""
    config = carregar_config()
    if not config:
        return "", ""
    t = normalizar(texto)
    melhor = None
    for ordem, c in enumerate(config["categorias"]):
        for regra in c["regras"]:
            achado = regra.search(t)
            if achado:
                chave = (achado.start(), -(achado.end() - achado.start()), ordem)
                if melhor is None or chave < melhor[0]:
                    melhor = (chave, c["id"], achado.group(0))
    return (melhor[1], melhor[2].strip()) if melhor else ("", "")


def categoria_por_ia(categoria_ia: Any) -> str:
    """Rótulo de categoria devolvido pela IA de classificação já existente (sem nova chamada)."""
    config = carregar_config()
    rotulo = normalizar(categoria_ia)
    if not config or not rotulo:
        return ""
    for c in config["categorias"]:
        if normalizar(c["rotulo"]) == rotulo:
            return c["id"]
    for c in config["categorias"]:
        if any(chave and chave in rotulo for chave in c["ia"]):
            return c["id"]
    return ""


def identificar_categoria(texto: Any, categoria_ia: Any = "", marca: str = "") -> Tuple[str, str]:
    """Texto do item -> categoria da IA já feita -> marca típica -> 'geral'."""
    cat, termo = categoria_por_texto(texto)
    if cat:
        return cat, termo
    config = carregar_config() or {"marcas_categoria": {}}
    cat = categoria_por_ia(categoria_ia) or config["marcas_categoria"].get(normalizar(marca), "")
    return (cat or "geral"), ""


# ------------------------------------------------------------------ seleção
def _pontuar(f: Dict[str, Any], categoria: str, chave_marca: str, linhas_cat: set, pesos: Dict[str, int]) -> int:
    serve_categoria = categoria in f["especialidades"] or categoria in f["atende"]
    if categoria != "geral" and not serve_categoria:
        return 0  # marca sozinha não basta: quem vende celular Samsung não vira opção para monitor Samsung
    pontos = 0
    if categoria in f["especialidades"]:
        pontos += pesos["especialista"]
    elif categoria in f["atende"]:
        pontos += pesos["atende"]
    if chave_marca and chave_marca in f["marcas"]:
        pontos += pesos["marca"] + (pesos["portfolio"] if serve_categoria else 0)
    if pontos and linhas_cat & f["linhas"]:
        pontos += pesos["linha"]
    return pontos


@lru_cache(maxsize=1024)
def _selecionar_cache(categoria: str, chave_marca: str) -> Tuple[int, ...]:
    config = _ESTADO["config"]
    cat = config["categorias_por_id"].get(categoria) or config["categorias_por_id"]["geral"]
    pesos = config["pesos"]
    pontuados = []
    for i, f in enumerate(config["fornecedores"]):
        p = _pontuar(f, cat["id"], chave_marca, cat["linhas"], pesos)
        if p > 0:
            pontuados.append((-p, 0 if f["dominio"] else 1, f["prioridade"], i, p))
    pontuados.sort()
    com_link = [x for x in pontuados if x[1] == 0]
    sem_link = [x for x in pontuados if x[1] == 1]
    escolhidos: List[Tuple] = []
    if com_link:
        topo = com_link[0][4]
        for x in com_link:
            if len(escolhidos) >= config["max"]:
                break
            if len(escolhidos) < config["min"] or x[4] >= topo * config["corte"]:
                escolhidos.append(x)
    # Completa o mínimo: primeiro quem atende a categoria sem domínio confirmado, depois os gerais
    for x in sem_link:
        if len(escolhidos) >= config["min"]:
            break
        escolhidos.append(x)
    if len(escolhidos) < config["min"] and cat["id"] != "geral":
        usados = {x[3] for x in escolhidos}
        for i in _selecionar_cache("geral", ""):
            if len(escolhidos) >= config["min"]:
                break
            if i not in usados:
                escolhidos.append((0, 0, 0, i, 0))
                usados.add(i)
    return tuple(x[3] for x in escolhidos)


def selecionar_fornecedores(categoria: str, marca: str = "") -> List[Dict[str, Any]]:
    """De 3 a 5 fornecedores (dicts da configuração) para a categoria e a marca; sem repetição."""
    config = carregar_config()
    if not config:
        return []
    chave = _compacto(config["indice_marcas"].get(_compacto(marca), marca)) if marca else ""
    vistos, saida = set(), []
    for i in _selecionar_cache(categoria or "geral", chave):
        f = config["fornecedores"][i]
        if f["nome"].upper() not in vistos:
            vistos.add(f["nome"].upper())
            saida.append(f)
    return saida[:config["max"]]


# ------------------------------------------------------------------ busca e links
def _limpar_modelo(modelo: Any) -> str:
    m = normalizar(modelo)
    m = re.sub(r"^\(?\s*ITEM\s*\d+\s*\)?\s*[:\-]?\s*", "", m)
    return m.strip(" ;,.-/|")


def montar_termo_busca(marca: str = "", modelo: Any = "", part_number: Any = "", termo_produto: str = "") -> str:
    """'MARCA MODELO'; sem modelo, 'PRODUTO MARCA'; sem marca, o termo do produto achado no texto."""
    marca_n = normalizar(marca)
    modelo_n = _limpar_modelo(modelo) or _limpar_modelo(part_number)
    if modelo_n and marca_n and modelo_n.startswith(marca_n):
        modelo_n = modelo_n[len(marca_n):].strip()
    produto = " ".join(normalizar(termo_produto).split()[:4])
    if modelo_n:
        partes = [marca_n, modelo_n] if marca_n else [produto, modelo_n]
    else:
        partes = [produto, marca_n]
    termo = " ".join(p for p in partes if p)
    return re.sub(r"\s+", " ", termo).strip()[:80]


def _termo_do_texto(texto: Any, palavras: int = 5) -> str:
    """Último recurso: as primeiras palavras do item, sem código SAP, unidades e separadores."""
    t = normalizar(texto).replace("||", " ")
    t = re.sub(r"^\s*(?:\(?ITEM\s*\d+\)?\s*[:\-]?\s*)?\d{6,9}\b", " ", t)
    tokens = [w.strip(" ;,.-|") for w in t.split()]
    tokens = [w for w in tokens if len(w) > 1 and w not in {"PT", "UN", "PC", "CJ", "KG"} and "/" not in w]
    return " ".join(tokens[:palavras])[:80]


def montar_link(fornecedor: Dict[str, Any], termo: str) -> str:
    """Link de busca no site do fornecedor ({q}); sem busca pública, busca restrita ao domínio."""
    if not fornecedor.get("dominio") or not termo:
        return ""
    config = carregar_config() or {}
    modelo_url = fornecedor.get("busca") or config.get("busca_padrao", "")
    espaco = (fornecedor.get("busca_espaco") or "+") if fornecedor.get("busca") else "+"
    q = quote_plus(termo) if espaco == "+" else quote(termo, safe="")
    return modelo_url.replace("{dominio}", fornecedor["dominio"]).replace("{q}", q)


# ------------------------------------------------------------------ recomendação por item
def recomendar(texto: Any, marca: Any = "", modelo: Any = "", part_number: Any = "",
               categoria_ia: Any = "", exigir_categoria_texto: bool = False) -> Optional[Dict[str, Any]]:
    """
    Recomendação para um item já identificado como TI/TV/eletrônico.
    exigir_categoria_texto=True (portal, sem a confirmação da IA): só recomenda quando o próprio
    texto do item tem um termo de produto de TI e nenhum termo industrial excluído.
    """
    config = carregar_config()
    if not config or item_excluido(texto):
        return None
    marca_c = identificar_marca(marca, texto)
    categoria, termo_produto = identificar_categoria(texto, categoria_ia, marca_c)
    if exigir_categoria_texto and not termo_produto:
        return None
    escolhidos = selecionar_fornecedores(categoria, marca_c)
    if not escolhidos:
        return None
    termo = montar_termo_busca(marca_c, modelo, part_number, termo_produto) or _termo_do_texto(texto)
    rotulo = config["categorias_por_id"].get(categoria, {}).get("rotulo", "")
    return {
        "categoria": categoria,
        "rotulo": rotulo,
        "marca": marca_c,
        "termo_busca": termo,
        "fornecedores": [{"nome": f["nome"], "url": montar_link(f, termo)} for f in escolhidos],
    }


def formatar_texto(recomendacao: Optional[Dict[str, Any]]) -> str:
    if not recomendacao or not recomendacao.get("fornecedores"):
        return ""
    return f"{ROTULO_SECAO} " + " | ".join(f["nome"] for f in recomendacao["fornecedores"])


def agrupar_itens_equivalentes(itens: List[Dict[str, Any]], chave_info: str = "onde_encontrar_info") -> int:
    """Itens do mesmo evento com o mesmo produto (mesma busca e mesmos fornecedores) não repetem a lista:
    a partir do 2º, a recomendação ganha 'mesmo_que_item'. Devolve quantos itens foram agrupados."""
    vistos: Dict[Tuple, str] = {}
    agrupados = 0
    for it in itens or []:
        info = it.get(chave_info) if isinstance(it, dict) else None
        if not info or not info.get("fornecedores"):
            continue
        info.pop("mesmo_que_item", None)
        assinatura = (normalizar(info.get("termo_busca")), tuple(f["nome"] for f in info["fornecedores"]))
        numero = str(it.get("item_num", it.get("item", "")) or "").strip()
        if assinatura in vistos:
            info["mesmo_que_item"] = vistos[assinatura]
            agrupados += 1
        else:
            vistos[assinatura] = numero
    return agrupados


# ------------------------------------------------------------------ Word (python-docx)
def _adicionar_hyperlink(paragrafo, url: str, texto: str, tamanho) -> None:
    from docx.opc.constants import RELATIONSHIP_TYPE
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    r_id = paragrafo.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    link.set(qn("w:history"), "1")
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    cor = OxmlElement("w:color")
    cor.set(qn("w:val"), "1F5A96")
    sublinhado = OxmlElement("w:u")
    sublinhado.set(qn("w:val"), "single")
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), str(int(tamanho.pt * 2)))
    rpr.extend([cor, sublinhado, sz])
    t = OxmlElement("w:t")
    t.text = texto
    t.set(qn("xml:space"), "preserve")
    run.extend([rpr, t])
    link.append(run)
    paragrafo._p.append(link)


def adicionar_secao_word(doc, recomendacao: Optional[Dict[str, Any]]) -> bool:
    """
    Seção discreta (8pt) logo abaixo do item:
        ONDE ENCONTRAR?
        Agis | Mazer | Ingram Micro | SND   (links de busca; estoque e preço não confirmados)
    Em item equivalente a um anterior do mesmo evento: 'ONDE ENCONTRAR? mesmos fornecedores do item N'.
    """
    if not recomendacao or not recomendacao.get("fornecedores"):
        return False
    from docx.shared import Pt, RGBColor

    tamanho = Pt(8)
    paragrafo = doc.add_paragraph()
    paragrafo.paragraph_format.space_before = Pt(2)
    paragrafo.paragraph_format.space_after = Pt(1)
    rotulo = paragrafo.add_run(ROTULO_SECAO)
    rotulo.bold = True
    rotulo.font.size = tamanho
    rotulo.font.color.rgb = RGBColor(35, 75, 115)

    if recomendacao.get("mesmo_que_item"):
        ref = paragrafo.add_run(f" mesmos fornecedores do item {recomendacao['mesmo_que_item']}")
        ref.italic = True
        ref.font.size = tamanho
        ref.font.color.rgb = RGBColor(115, 115, 115)
        return True

    rotulo.add_break()
    for i, f in enumerate(recomendacao["fornecedores"]):
        if i:
            sep = paragrafo.add_run(" | ")
            sep.font.size = tamanho
            sep.font.color.rgb = RGBColor(115, 115, 115)
        if f.get("url"):
            _adicionar_hyperlink(paragrafo, f["url"], f["nome"], tamanho)
        else:
            nome = paragrafo.add_run(f["nome"])
            nome.font.size = tamanho
            nome.font.color.rgb = RGBColor(55, 55, 55)
    nota = paragrafo.add_run(f"  ({NOTA_SECAO})")
    nota.italic = True
    nota.font.size = Pt(7)
    nota.font.color.rgb = RGBColor(130, 130, 130)
    return True


# ------------------------------------------------------------------ linhas da planilha (portal / API Cloud)
_RE_PREFIXO_ITEM = re.compile(r"^\s*\(?\s*ITEM\s*(\d+)\s*\)?\s*[:\-]?\s*", re.IGNORECASE)


def _por_item(valor: Any) -> Dict[str, str]:
    """'(Item 1) X\\n(Item 2) Y' -> {'1': 'X', '2': 'Y'}; valor sem prefixo -> {'1': valor}."""
    texto = str(valor or "").strip()
    if not texto or texto.lower() in ("nan", "none"):
        return {}
    blocos = re.split(r"(?=\(\s*ITEM\s*\d+\s*\))", texto, flags=re.IGNORECASE)
    saida: Dict[str, str] = {}
    for bloco in blocos:
        bloco = bloco.strip()
        if not bloco:
            continue
        m = _RE_PREFIXO_ITEM.match(bloco)
        if m:
            saida[m.group(1)] = (saida.get(m.group(1), "") + " " + bloco[m.end():]).strip()
        else:
            saida["1"] = (saida.get("1", "") + " " + bloco).strip()
    return saida


def recomendar_linha_cotacao(linha: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Recomendações de uma linha do COTAÇÕES.json (Word padrão Coupa do portal). Usa ITEM, MARCAS e
    MODELOS já gravados pelo robô; sem a IA, exige termo de produto de TI no texto do item."""
    itens = _por_item(linha.get("ITEM") or linha.get("ITENS") or linha.get("DESCRICAO"))
    marcas = _por_item(linha.get("MARCAS ") or linha.get("MARCAS") or linha.get("MARCA"))
    modelos = _por_item(linha.get("MODELOS") or linha.get("MODELO"))
    unico = len(itens) == 1
    saida = []
    for numero in sorted(itens, key=lambda n: int(n)):
        marca = marcas.get(numero) or (next(iter(marcas.values()), "") if unico and len(marcas) == 1 else "")
        modelo = modelos.get(numero) or (next(iter(modelos.values()), "") if unico and len(modelos) == 1 else "")
        rec = recomendar(itens[numero], marca, modelo, exigir_categoria_texto=True)
        if rec:
            saida.append({"item": numero, "onde_encontrar_info": rec})
    agrupar_itens_equivalentes(saida)
    return [{"item": x["item"], **x["onde_encontrar_info"]} for x in saida]


def mapa_onde_encontrar(cotacoes: List[Dict[str, Any]], numeros: Optional[set] = None) -> Dict[str, List[Dict[str, Any]]]:
    """{número da cotação: [recomendações por item]} só para as cotações pedidas (Coupa/Vale)."""
    mapa: Dict[str, List[Dict[str, Any]]] = {}
    for linha in cotacoes or []:
        numero = str(linha.get("COTAÇÃO", "")).strip()
        if not numero or (numeros is not None and numero not in numeros) or numero in mapa:
            continue
        try:
            recs = recomendar_linha_cotacao(linha)
        except Exception as e:  # nunca derruba o envio da planilha
            logger.warning(f"   ⚠️ ONDE ENCONTRAR? falhou na cotação {numero}: {e}")
            continue
        if recs:
            mapa[numero] = recs
    return mapa
