# -*- coding: utf-8 -*-
import re
import difflib
from typing import Dict, Any, List, Optional, Tuple


def marca_e_modelo_na_descricao(descricao: Optional[str], marca: Optional[str], modelo: Optional[str]) -> bool:
    """
    Verifica se a marca e o modelo identificados na busca prévia
    estão presentes no campo DESCRIÇÃO.
    """
    if not descricao:
        return False

    desc_clean = str(descricao).strip().lower()
    m_marca = str(marca or "").strip().lower()
    m_modelo = str(modelo or "").strip().lower()

    tem_m = bool(m_marca and len(m_marca) > 1)
    tem_mod = bool(m_modelo and len(m_modelo) > 1)

    # Se nem marca nem modelo foram identificados na etapa prévia
    if not tem_m and not tem_mod:
        return False

    marca_presente = tem_m and bool(re.search(r'\b' + re.escape(m_marca) + r'\b', desc_clean))
    modelo_presente = tem_mod and (m_modelo in desc_clean)

    if tem_m and tem_mod:
        # Ambos foram identificados na etapa prévia: ambos devem estar na descrição
        return marca_presente and modelo_presente
    elif tem_m:
        # Apenas marca foi identificada na etapa prévia
        return marca_presente
    else:
        # Apenas modelo foi identificado na etapa prévia
        return modelo_presente


def selecionar_alvos_comparacao_item(
    item_dict: Any,
    dados_ia: Optional[Dict[str, Any]] = None
) -> List[Tuple[str, str]]:
    """
    Determina quais textos e em que ordem de prioridade devem ser usados
    na etapa de comparação de descrição de itens para recomendação do último vendedor.

    Regras obrigatórias:
    1. Prioridade principal: ITEM TEXT (texto do item).
    2. Exceção DESCRIÇÃO: Usar apenas quando NÃO houver ITEM TEXT no evento/cotação.
    3. Exceção Marca e Modelo: Se a marca e o modelo forem encontrados no campo DESCRIÇÃO
       durante a etapa anterior de busca, a DESCRIÇÃO tem prioridade sobre o ITEM TEXT.
    Regra Crítica: Em todos os outros casos, a comparação deve ser baseada EXCLUSIVAMENTE
       no ITEM TEXT (texto do item), nunca usando a DESCRIÇÃO.
    """
    texto_item = ""
    descricao = ""

    if isinstance(item_dict, dict):
        texto_item = str(item_dict.get('texto_item') or "").strip()
        descricao = str(item_dict.get('descricao') or "").strip()
    else:
        texto_item = str(item_dict or "").strip()

    tem_texto_item = bool(texto_item and texto_item.lower() not in ["none", "n/a", "nenhum", ""])
    tem_descricao = bool(descricao and descricao.lower() not in ["none", "n/a", "nenhum", ""])

    marca = ""
    modelo = ""
    if dados_ia and isinstance(dados_ia, dict):
        marca = str(dados_ia.get("marca") or "").strip()
        modelo = str(dados_ia.get("modelo") or "").strip()
        pn = str(dados_ia.get("part_number") or "").strip()
        if not modelo and pn:
            modelo = pn

    # Checagem da Exceção 3: Marca e modelo encontrados na descrição durante busca prévia
    prioridade_desc = marca_e_modelo_na_descricao(descricao, marca, modelo)

    alvos_rotulados: List[Tuple[str, str]] = []

    # 3. Se marca e modelo estão na descrição, a descrição tem prioridade sobre o texto do item
    if prioridade_desc and tem_descricao:
        alvos_rotulados.append((descricao.lower(), "Descricao (Prioridade: Marca e Modelo na Descricao)"))
        if tem_texto_item:
            alvos_rotulados.append((texto_item.lower(), "Texto do Item (Secundario)"))
        return alvos_rotulados

    # 2. Se NÃO há texto do item disponível, usa a descrição
    if not tem_texto_item:
        if tem_descricao:
            alvos_rotulados.append((descricao.lower(), "Descricao (Ausencia de Texto do Item)"))
        return alvos_rotulados

    # 1. e Regra Crítica: Em todos os outros casos, comparação baseada EXCLUSIVAMENTE no ITEM TEXT
    alvos_rotulados.append((texto_item.lower(), "Texto do Item (Exclusivo)"))
    return alvos_rotulados
