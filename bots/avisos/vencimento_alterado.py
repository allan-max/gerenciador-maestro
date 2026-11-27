# -*- coding: utf-8 -*-
"""Evento reaberto por mudança no vencimento (Vale e Coupa): aviso com o vencimento antigo e o novo, ao lado do
VENCIMENTO no Word e no campo "VENCIMENTO ALTERADO" da linha do COTAÇÕES.json. O campo VENCIMENTO continua só
com a data, porque o dashboard e a verificação de respostas leem a data dele."""
import re
from typing import Optional

CAMPO_JSON = "VENCIMENTO ALTERADO"


def parsear_data_vencimento(data_str) -> Optional[str]:
    """Vencimento em 'dd/mm/aaaa' ou 'dd/mm/aaaa - HH:MM'; None se não for data.
    Aceita a tela em português ('04/05/2026 14:30', dia/mês), o formato americano da tabela da Coupa
    ('12/02/26 02:30 pm -03', mês/dia por causa do am/pm) e ISO ('2026-12-10 14:30:00 -0300')."""
    if not data_str or str(data_str).strip() in ['N/A', '', 'None']:
        return None
    texto = str(data_str).strip()
    if re.match(r'^\d{2}/\d{2}/\d{4}(\s*-\s*\d{2}:\d{2})?$', texto):
        return texto
    m_iso = re.search(r'(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{1,2}):(\d{2}))?', texto)
    if m_iso:
        ano, mes, dia = m_iso.group(1), m_iso.group(2), m_iso.group(3)
        hora = f"{int(m_iso.group(4)):02d}:{m_iso.group(5)}" if m_iso.group(4) else ""
        return f"{dia}/{mes}/{ano} - {hora}" if hora else f"{dia}/{mes}/{ano}"
    m_coupa = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2,4})(?:\s+(\d{1,2}):(\d{2})\s*(am|pm)?)?', texto, re.IGNORECASE)
    if m_coupa:
        p1, p2, p3 = int(m_coupa.group(1)), int(m_coupa.group(2)), int(m_coupa.group(3))
        ano = p3 if p3 >= 1000 else (2000 + p3 if p3 < 100 else p3)
        # Sem AM/PM a tela está em português (dia/mês), igual a "04/05/2026" acima; com AM/PM é o formato
        # americano (mês/dia). Antes "4/5/2026" virava 05/04 e "04/05/2026" ficava 04/05.
        americano = bool(m_coupa.group(6))
        if p1 > 12: dia, mes = p1, p2
        elif p2 > 12: dia, mes = p2, p1
        elif americano: mes, dia = p1, p2
        else: dia, mes = p1, p2
        hora_str = ""
        if m_coupa.group(4) and m_coupa.group(5):
            h = int(m_coupa.group(4))
            minu = m_coupa.group(5)
            ampm = (m_coupa.group(6) or "").lower()
            if ampm == 'pm' and h < 12: h += 12
            elif ampm == 'am' and h == 12: h = 0
            hora_str = f" - {h:02d}:{minu}"
        return f"{dia:02d}/{mes:02d}/{ano}{hora_str}"
    return None


def aviso_vencimento_alterado(antigo, novo) -> str:
    """'Vencimento alterado, vencimento antigo: dd/mm/aaaa Vencimento NOVO: dd/mm/aaaa', ou '' se não der para
    ler uma das datas ou se for o mesmo vencimento (só mudou o formato). Se só a hora mudou, mostra as horas."""
    a, n = parsear_data_vencimento(antigo), parsear_data_vencimento(novo)
    if not a or not n:
        return ""
    data_a, data_n = a.split(' - ')[0], n.split(' - ')[0]
    if data_a == data_n:
        if ' - ' not in a or ' - ' not in n or a == n:
            return ""
        data_a, data_n = a, n
    return f"Vencimento alterado, vencimento antigo: {data_a} Vencimento NOVO: {data_n}"


def anotar(res, tarefa):
    """Grava em res['vencimento_alterado'] o aviso de um evento reaberto (tarefa com 're_extrair').
    O novo é o vencimento lido na tela do evento; sem ele, o da tabela."""
    if not isinstance(res, dict) or not tarefa.get('re_extrair'):
        return ""
    novo = res.get('data_vencimento')
    if not parsear_data_vencimento(novo):
        novo = tarefa.get('venc_tabela', '')
    aviso = aviso_vencimento_alterado(tarefa.get('venc_anterior', ''), novo)
    if aviso:
        res['vencimento_alterado'] = aviso
    return aviso


def extras_json(ev):
    """Campos a mais da linha do COTAÇÕES.json para este evento."""
    aviso = ev.get('vencimento_alterado') if isinstance(ev, dict) else ""
    return {CAMPO_JSON: aviso} if aviso else {}


def escrever_no_word(paragrafo, ev):
    """Aviso em vermelho, no mesmo parágrafo, logo depois da data de vencimento."""
    aviso = ev.get('vencimento_alterado') if isinstance(ev, dict) else ""
    if not aviso:
        return
    from docx.shared import RGBColor
    run = paragrafo.add_run(f"   {aviso}")
    run.bold = True
    run.font.color.rgb = RGBColor(255, 0, 0)
