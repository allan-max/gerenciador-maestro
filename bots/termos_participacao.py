# -*- coding: utf-8 -*-
"""Termos e condições do evento (Coupa / Vale Base Metals).

Alguns eventos pedem "Aceita os Termos e condições?" (div.s-term, rádios Sim/Não) antes do "Pretendo".
Sem o "Sim" marcado o portal não aceita a participação. Chamar antes de selecionar o #participation.
"""
import logging

logger = logging.getLogger(__name__)

_JS_ACEITAR_TERMOS = """
const vistos = new Set();
const radios = Array.from(document.querySelectorAll(
    "div.s-term input[type='radio'][value='true'], input[type='radio'].s-termAccept"
)).filter(r => !vistos.has(r) && vistos.add(r) && String(r.value).toLowerCase() === 'true');
let jaMarcados = 0, marcados = 0, falhas = 0;
radios.forEach(r => {
    if (r.checked) { jaMarcados++; return; }
    r.scrollIntoView({block: 'center'});
    r.click();
    if (!r.checked) { const lb = r.closest('label') || document.querySelector("label[for='" + r.id + "']"); if (lb) lb.click(); }
    if (!r.checked && !r.disabled) {
        r.checked = true;
        r.dispatchEvent(new Event('input', {bubbles: true}));
        r.dispatchEvent(new Event('change', {bubbles: true}));
    }
    if (r.checked) marcados++; else falhas++;
});
return [radios.length, jaMarcados, marcados, falhas];
"""


def aceitar_termos(driver):
    """Marca "Sim" em todo termo do evento. Devolve quantos termos foram marcados agora (0 se nenhum)."""
    try:
        total, ja, marcados, falhas = driver.execute_script(_JS_ACEITAR_TERMOS)
    except Exception as e:
        logger.warning(f"    Erro ao aceitar termos e condições: {e}")
        return 0
    if total:
        logger.info(f"    Termos e condições: {total} encontrado(s), {marcados} marcado(s) 'Sim' agora, {ja} já aceito(s)"
                    + (f", {falhas} sem conseguir marcar" if falhas else "") + ".")
    return marcados
