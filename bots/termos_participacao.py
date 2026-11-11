# -*- coding: utf-8 -*-
"""Termos e condições do evento (Coupa / Vale Base Metals).

Alguns eventos pedem "Aceita os Termos e condições?" (div.s-term, rádios Sim/Não) antes do "Pretendo".
Sem o "Sim" marcado o portal não aceita a participação. Chamar antes de selecionar o #participation.

O rádio do "Sim" é reconhecido pela classe s-termAccept, pelo valor (true/sim/yes/s/y/1) ou pelo texto do
rótulo ("Sim"/"Yes"): o portal já usou value="true", mas um "Sim" com outro valor não pode travar o evento.
"""
import logging

logger = logging.getLogger(__name__)

# Funções JS comuns: acha o rádio de aceite de cada termo (div.s-term) e os rádios s-termAccept soltos.
_JS_BASE = """
const VALORES_SIM = ['true', 'sim', 'yes', 's', 'y', '1'];
const TEXTOS_SIM = ['sim', 'yes'];
function rotulo(r) {
    const lb = r.closest('label') || (r.id ? document.querySelector('label[for="' + CSS.escape(r.id) + '"]') : null);
    return lb ? (lb.innerText || lb.textContent || '').trim().toLowerCase() : '';
}
function ehSim(r) {
    if (r.classList.contains('s-termReject')) return false;
    if (r.classList.contains('s-termAccept')) return true;
    if (VALORES_SIM.includes(String(r.value).trim().toLowerCase())) return true;
    return TEXTOS_SIM.includes(rotulo(r));
}
function radiosSim() {
    const vistos = new Set(), lista = [];
    const add = r => { if (r && !vistos.has(r)) { vistos.add(r); lista.push(r); } };
    document.querySelectorAll('div.s-term').forEach(t => {
        add(Array.from(t.querySelectorAll("input[type='radio']")).find(ehSim));
    });
    document.querySelectorAll("input[type='radio'].s-termAccept").forEach(add);
    return lista;
}
"""

_JS_ACEITAR_TERMOS = _JS_BASE + """
const termos = document.querySelectorAll('div.s-term').length;
const radios = radiosSim();
let jaMarcados = 0, marcados = 0, falhas = 0;
radios.forEach(r => {
    if (r.checked) { jaMarcados++; return; }
    r.scrollIntoView({block: 'center'});
    r.click();
    if (!r.checked) { const lb = r.closest('label') || (r.id ? document.querySelector('label[for="' + CSS.escape(r.id) + '"]') : null); if (lb) lb.click(); }
    if (!r.checked && !r.disabled) {
        r.checked = true;
        r.dispatchEvent(new Event('input', {bubbles: true}));
        r.dispatchEvent(new Event('change', {bubbles: true}));
    }
    if (r.checked) marcados++; else falhas++;
});
// termo cujo "Sim" não foi reconhecido de nenhum jeito
const semSim = Array.from(document.querySelectorAll('div.s-term')).filter(t =>
    !Array.from(t.querySelectorAll("input[type='radio']")).some(ehSim)).length;
return [radios.length, jaMarcados, marcados, falhas + semSim, termos];
"""

_JS_TERMOS_PENDENTES = _JS_BASE + """
return Array.from(document.querySelectorAll('div.s-term')).filter(t => {
    const sim = Array.from(t.querySelectorAll("input[type='radio']")).find(ehSim);
    return !(sim && sim.checked);
}).length;
"""


def aceitar_termos(driver):
    """Marca "Sim" em todo termo do evento. Devolve quantos termos foram marcados agora (0 se nenhum)."""
    try:
        total, ja, marcados, falhas, termos = driver.execute_script(_JS_ACEITAR_TERMOS)
    except Exception as e:
        logger.warning(f"    Erro ao aceitar termos e condições: {e}")
        return 0
    if total or termos:
        logger.info(f"    Termos e condições: {max(total, termos)} encontrado(s), {marcados} marcado(s) 'Sim' agora, {ja} já aceito(s)"
                    + (f", {falhas} sem conseguir marcar" if falhas else "") + ".")
    if falhas:
        logger.warning(f"    ⚠️ {falhas} termo(s) e condições ficaram SEM o 'Sim': o portal pode recusar o 'Pretendo'.")
    return marcados


def termos_pendentes(driver):
    """Quantos termos (div.s-term) da página estão sem o "Sim" marcado. 0 se não houver termo ou der erro."""
    try:
        return int(driver.execute_script(_JS_TERMOS_PENDENTES) or 0)
    except Exception:
        return 0
