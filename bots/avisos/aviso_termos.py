# -*- coding: utf-8 -*-
"""Aviso por e-mail dos eventos com "Aceita os Termos e condições?" (Coupa e Vale Base Metals).

Durante a extração o robô anota todo evento que tem termos (mesmo quando consegue aceitar) e, no fim da extração,
manda UM e-mail com a lista: número, título, se é negociação, o texto de cada termo e o que aconteceu.
Destinatários: vendas7@ (fixo, como nos e-mails de erro) + emails_relatorio do CONFIG.json.

Uso no robô:
    aviso_termos.anotar(self, num, termos_lidos, titulo, negociacao)   # logo que a página do evento carrega
    aviso_termos.concluir(self, num, res)                               # depois do extrair_dados_do_evento
    aviso_termos.enviar(self, "Vale Base Metals")                       # no fim da extração
"""
import html
import logging
import os
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger('bots.aviso_termos')   # nome de antes da pasta avisos/

DESTINATARIO_FIXO = "vendas7@venturainformatica.com.br"

SITUACAO_JA_ACEITOS = "Termos já estavam aceitos"
SITUACAO_ACEITOS = "Robô aceitou os termos e extraiu os itens"
SITUACAO_NAO_ACEITOS = "Evento NÃO foi extraído (termos não aceitos ou itens não lidos): conferir no portal"
SITUACAO_ENCERRADO = "Evento encerrado"
SITUACAO_SEM_RESULTADO = "Extração não terminou (sem resultado)"


def _lista(robo):
    if not isinstance(getattr(robo, 'eventos_com_termos', None), dict):
        robo.eventos_com_termos = {}
    return robo.eventos_com_termos


def anotar(robo, numero, termos, titulo="", negociacao=False):
    """Guarda o evento se a página tem termos. Devolve True se anotou."""
    if not termos:
        return False
    _lista(robo)[str(numero)] = {
        "numero": str(numero), "titulo": str(titulo or "").strip(), "negociacao": bool(negociacao),
        "termos": [{"texto": str(t.get("texto") or "").strip(), "aceito": bool(t.get("aceito"))} for t in termos],
        "ja_aceitos": all(t.get("aceito") for t in termos),
        "situacao": SITUACAO_SEM_RESULTADO, "hora": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }
    logger.info(f"    📝 Evento {numero}: {len(termos)} termo(s) e condições na página (vai no e-mail de termos).")
    return True


def concluir(robo, numero, resultado):
    """Situação final do evento anotado, a partir do retorno do extrair_dados_do_evento."""
    ev = _lista(robo).get(str(numero))
    if not ev:
        return
    if isinstance(resultado, dict):
        ev["situacao"] = SITUACAO_JA_ACEITOS if ev["ja_aceitos"] else SITUACAO_ACEITOS
    elif resultado == "ENCERRADO":
        ev["situacao"] = SITUACAO_ENCERRADO
    elif resultado == "SKIPPED":
        ev["situacao"] = SITUACAO_NAO_ACEITOS
    else:
        ev["situacao"] = SITUACAO_SEM_RESULTADO


def gerar_conteudo(nome_bot, eventos):
    """(assunto, texto, html) do e-mail. eventos: lista de dicts de anotar()."""
    problemas = sum(1 for e in eventos if e["situacao"] not in (SITUACAO_ACEITOS, SITUACAO_JA_ACEITOS))
    assunto = f"[{nome_bot}] {len(eventos)} evento(s) com Termos e condições" + (f" — {problemas} precisa(m) de atenção" if problemas else "")
    linhas_txt = [f"{nome_bot}: eventos da extração com \"Aceita os Termos e condições?\"", ""]
    blocos_html = []
    for e in eventos:
        cab = f"Evento {e['numero']}" + (" (NEGOCIAÇÃO)" if e["negociacao"] else "")
        linhas_txt += [cab, f"  Título: {e['titulo'] or '-'}", f"  Situação: {e['situacao']}"]
        for i, t in enumerate(e["termos"], 1):
            linhas_txt.append(f"  Termo {i}: {t['texto'] or '(sem texto)'}")
        linhas_txt.append("")
        cor = "#1e7e34" if e["situacao"] in (SITUACAO_ACEITOS, SITUACAO_JA_ACEITOS) else "#c0392b"
        termos_html = "".join(f"<li>{html.escape(t['texto'] or '(sem texto)')}</li>" for t in e["termos"])
        blocos_html.append(
            f"<tr><td style='padding:6px;border:1px solid #ccc;vertical-align:top'><b>{html.escape(cab)}</b><br>"
            f"{html.escape(e['titulo'] or '')}</td>"
            f"<td style='padding:6px;border:1px solid #ccc;vertical-align:top'><ol style='margin:0;padding-left:18px'>{termos_html}</ol></td>"
            f"<td style='padding:6px;border:1px solid #ccc;vertical-align:top;color:{cor}'><b>{html.escape(e['situacao'])}</b></td></tr>")
    corpo_html = (
        f"<p><b>{html.escape(nome_bot)}</b>: eventos da extração com <i>\"Aceita os Termos e condições?\"</i>. "
        f"O robô tenta aceitar sozinho; confira os termos abaixo (frete, endereço de entrega, ficha técnica...).</p>"
        "<table style='border-collapse:collapse;font-family:Arial;font-size:13px'>"
        "<tr style='background:#eee'><th style='padding:6px;border:1px solid #ccc'>Evento</th>"
        "<th style='padding:6px;border:1px solid #ccc'>Termos e condições</th>"
        "<th style='padding:6px;border:1px solid #ccc'>Situação</th></tr>" + "".join(blocos_html) + "</table>")
    return assunto, "\n".join(linhas_txt), corpo_html


def enviar(robo, nome_bot, logger_inst=None):
    """Manda o e-mail com os eventos anotados nesta extração e limpa a lista. True se enviou."""
    log = logger_inst or logger
    eventos = list(_lista(robo).values())
    if not eventos:
        return False
    robo.eventos_com_termos = {}
    cfg = getattr(robo, 'config', None) or {}
    assunto, texto, corpo_html = gerar_conteudo(nome_bot, eventos)

    remetente = (cfg.get("SMTP_USER") or os.environ.get("SMTP_USER", "")).strip()
    senha = (cfg.get("SMTP_PASSWORD") or os.environ.get("SMTP_PASSWORD", "")).strip()
    destinos = {DESTINATARIO_FIXO}
    for e in str(cfg.get("emails_relatorio") or os.environ.get("EMAILS_RELATORIO", "")).split(','):
        if e.strip():
            destinos.add(e.strip().lower())
    destinatarios = sorted(destinos)
    if not remetente or not senha:
        log.warning(f"[{nome_bot}] E-mail de termos NÃO enviado: SMTP_USER / SMTP_PASSWORD não configurados. "
                    f"Eventos com termos: {', '.join(e['numero'] for e in eventos)}")
        return False
    try:
        try:
            from bots.avisos.relatorio_email import _conectar_e_enviar_smtp
        except ImportError:
            from relatorio_email import _conectar_e_enviar_smtp
        msg = MIMEMultipart("alternative")
        msg["From"] = remetente
        msg["To"] = ", ".join(destinatarios)
        msg["Subject"] = assunto
        msg.attach(MIMEText(texto, "plain", "utf-8"))
        msg.attach(MIMEText(corpo_html, "html", "utf-8"))
        if _conectar_e_enviar_smtp(remetente, senha, destinatarios, msg, nome_bot, log):
            log.info(f"📧 [{nome_bot}] E-mail de termos e condições ({len(eventos)} evento(s)) enviado para: {', '.join(destinatarios)}")
            return True
    except Exception as e:
        log.error(f"❌ [{nome_bot}] Erro ao enviar e-mail de termos e condições: {e}")
    return False
