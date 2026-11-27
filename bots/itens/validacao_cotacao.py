# -*- coding: utf-8 -*-
import re
from typing import Dict, Any, List, Tuple, Optional

# Expressão regular para capturar "Expected Quantity x Unit Price" e suas variações
# Aceita: 'x', 'X', '*', '×', quebras de linha, espaços múltiplos, e versão em português
PADRAO_EXPECTED_QUANTITY = re.compile(
    r'(?:expected\s+quantity\s*[\*xX×\n\r\t ]+\s*unit\s+price|'
    r'quantidade\s+esperada\s*[\*xX×\n\r\t ]+\s*pre[cç]o\s+unit[aá]rio)',
    re.IGNORECASE
)


def contem_expected_quantity(texto: Optional[str]) -> bool:
    """Verifica se o texto contém 'Expected Quantity x Unit Price' ou variações."""
    if not texto:
        return False
    return bool(PADRAO_EXPECTED_QUANTITY.search(str(texto)))


def validar_campo_individual(campo_nome: str, valor: Any) -> Tuple[bool, Optional[str]]:
    """Valida um campo individual de um item extraído."""
    val_str = str(valor or "").strip()
    if contem_expected_quantity(val_str):
        return False, f"Campo '{campo_nome}' contém 'Expected Quantity x Unit Price'"
    return True, None


def validar_item_individual(item: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Valida individualmente os campos de um item extraído:
    - Garante que nenhum campo contenha 'Expected Quantity x Unit Price'
    - Garante que o item tenha identificação real (título ou descrição válidos)
    - Garante que a quantidade seja um dado válido e não placeholder
    """
    if not isinstance(item, dict):
        return False, "Item não é um dicionário válido"

    # 1. Verifica se qualquer campo contém a expressão 'Expected Quantity x Unit Price'
    for campo in ["titulo", "descricao", "quantidade", "local_entrega", "texto_item", "texto_compra"]:
        valido, motivo = validar_campo_individual(campo, item.get(campo))
        if not valido:
            return False, motivo

    tit = str(item.get("titulo") or "").strip()
    desc = str(item.get("descricao") or "").strip()
    qtd = str(item.get("quantidade") or "").strip()

    # 2. Validação de identificação do produto
    # Não pode ser vazio nem apenas "Item" sem nenhuma descrição real
    tit_eh_placeholder = tit.lower() in ["", "item", "n/a", "nenhum"]
    desc_eh_placeholder = desc.lower() in ["", "n/a", "nenhum"]

    if tit_eh_placeholder and desc_eh_placeholder:
        return False, "Item sem identificação real (título e descrição vazios ou genéricos)"

    # 3. Validação de quantidade contra fórmulas/placeholders
    if contem_expected_quantity(qtd):
        return False, "Quantidade contém 'Expected Quantity x Unit Price'"

    return True, "Item válido"


def validar_itens_evento(itens: Optional[List[Dict[str, Any]]], texto_contexto: str = "") -> Tuple[bool, str, bool]:
    """
    Valida o conjunto de itens e o contexto da página do evento.
    Retorna: (itens_validos: bool, mensagem: str, tem_expected_quantity: bool)
    """
    tem_eq_contexto = contem_expected_quantity(texto_contexto)

    # Verifica também se algum item contém o padrão
    tem_eq_itens = any(
        contem_expected_quantity(str(item.get(k) or ""))
        for item in (itens or [])
        for k in ["titulo", "descricao", "quantidade", "local_entrega", "texto_item", "texto_compra"]
    )

    tem_expected_quantity = tem_eq_contexto or tem_eq_itens

    # Se a tela ou os itens exibem 'Expected Quantity x Unit Price'
    if tem_expected_quantity:
        # Se os itens forem vazios, é considerado sem itens válidos
        if not itens:
            return False, "Evento exibe 'Expected Quantity x Unit Price' com 0 itens identificados", True

        # Valida individualmente cada item
        for i, item in enumerate(itens):
            valido, motivo = validar_item_individual(item)
            if not valido:
                return False, f"Evento exibe 'Expected Quantity x Unit Price' e item {i+1} é inválido ({motivo})", True

        # Se houver itens mas qualquer um contém "Expected Quantity x Unit Price", é inválido
        if tem_eq_itens:
            return False, "Itens extraídos contêm 'Expected Quantity x Unit Price' em seus campos", True

    if not itens:
        motivo = "Evento com 0 itens identificados"
        if tem_expected_quantity:
            motivo = "Evento exibe 'Expected Quantity x Unit Price' sem itens reais"
        return False, motivo, tem_expected_quantity

    # Valida cada item individualmente
    for i, item in enumerate(itens):
        valido, motivo = validar_item_individual(item)
        if not valido:
            return False, f"Item {i+1} inválido: {motivo}", tem_expected_quantity

    return True, "Todos os itens foram corretamente identificados e validados", tem_expected_quantity
