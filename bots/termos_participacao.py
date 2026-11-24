# -*- coding: utf-8 -*-
"""Termos e condições do evento (Coupa / Vale Base Metals).

Alguns eventos (muitas vezes rodadas de NEGOCIAÇÃO) pedem "Aceita os Termos e condições?" com rádios Sim/Não
antes do "Pretendo". Sem o "Sim" marcado o portal não aceita a participação. Chamar antes do #participation.

Como a tela é (mapeada em 24/11/2026 no evento 312674, /quotes/external_responses/<n>/event_info):
  <h2>Aceitar os termos e condições</h2>
  <div class="flexColumns">
    <div class="flexColumns__column"><h4>Termos e condições</h4> O frete será de responsabilidade do fornecedor...</div>
    <div class="flexColumns__column s-term" data-id="127846"><h4>Aceita os Termos e condições?</h4>
      <input type="radio" value="true" class="s-termAccept"> Sim   <input type="radio" value="false" class="s-termReject"> Não
  </div>

Cada termo é achado pela classe s-term OU pelo texto "Aceita os termos e condições?" (se a classe mudar, o texto
ainda acha). O "Sim" é reconhecido pela classe s-termAccept, pelo valor (true/sim/yes/s/y/1) ou pelo rótulo.
"""
import logging

logger = logging.getLogger(__name__)

# Funções JS comuns: blocos de termo (classe ou texto da pergunta), rádio "Sim" de cada bloco e texto do termo.
_JS_BASE = r"""
const VALORES_SIM = ['true', 'sim', 'yes', 's', 'y', '1'];
const TEXTOS_SIM = ['sim', 'yes'];
const PERGUNTA = /aceita\s+(os\s+)?termos\s+e\s+condi/i;
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
function blocos() {
    const lista = [];
    const add = b => { if (b && !lista.some(x => x === b || x.contains(b) || b.contains(x))) lista.push(b); };
    document.querySelectorAll('div.s-term').forEach(add);
    // pela pergunta (texto do próprio elemento, não dos filhos): sobe até o primeiro elemento que tenha rádios
    document.querySelectorAll('body *').forEach(el => {
        const proprio = Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join(' ');
        if (!PERGUNTA.test(proprio)) return;
        let b = el;
        for (let i = 0; i < 5 && b && !b.querySelector("input[type='radio']"); i++) b = b.parentElement;
        if (b && b.querySelector("input[type='radio']")) add(b);
    });
    return lista;
}
function simDo(bloco) { return Array.from(bloco.querySelectorAll("input[type='radio']")).find(ehSim) || null; }
function textoDo(bloco) {
    // o texto do termo fica na coluna ao lado (flexColumns); sem ela, no irmão anterior
    const linha = bloco.closest('.flexColumns');
    let partes = linha ? Array.from(linha.children).filter(c => c !== bloco && !c.contains(bloco)).map(c => c.innerText) : [];
    if (!partes.length && bloco.previousElementSibling) partes = [bloco.previousElementSibling.innerText];
    return partes.join(' ').replace(/^\s*termos e condi[cç][oõ]es\s*/i, '').replace(/\s+/g, ' ').trim().slice(0, 600);
}
function radiosSim() {
    const lista = [];
    blocos().forEach(b => { const s = simDo(b); if (s && !lista.includes(s)) lista.push(s); });
    document.querySelectorAll("input[type='radio'].s-termAccept").forEach(r => { if (!lista.includes(r)) lista.push(r); });
    return lista;
}
"""

_JS_ACEITAR_TERMOS = _JS_BASE + """
const termos = blocos().length;
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
const semSim = blocos().filter(b => !simDo(b)).length;
return [radios.length, jaMarcados, marcados, falhas + semSim, termos];
"""

_JS_TERMOS_PENDENTES = _JS_BASE + """
return blocos().filter(b => { const s = simDo(b); return !(s && s.checked); }).length;
"""

_JS_LER_TERMOS = _JS_BASE + """
return blocos().map(b => { const s = simDo(b);
    return {id: b.getAttribute('data-id') || '', texto: textoDo(b), aceito: !!(s && s.checked), tem_sim: !!s}; });
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
    """Quantos termos da página estão sem o "Sim" marcado. 0 se não houver termo ou der erro."""
    try:
        return int(driver.execute_script(_JS_TERMOS_PENDENTES) or 0)
    except Exception:
        return 0


def ler_termos(driver):
    """Termos da página: [{'id', 'texto', 'aceito', 'tem_sim'}]. [] se não houver termo ou der erro."""
    try:
        termos = driver.execute_script(_JS_LER_TERMOS) or []
    except Exception as e:
        logger.warning(f"    Erro ao ler termos e condições: {e}")
        return []
    return [t for t in termos if isinstance(t, dict)]
