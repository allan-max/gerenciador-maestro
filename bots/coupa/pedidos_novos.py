# -*- coding: utf-8 -*-
"""Pedidos novos do Coupa: o que o robô já leu no portal, o que falta na planilha e o e-mail de aviso.

A planilha (PEDIDOS.json) é só lida: quem grava nela é o planilha_manager.
"""
import html
import json
import logging
import os
import tempfile
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from bots.relatorio_email import _conectar_e_enviar_smtp

DESTINO_PADRAO = "vendas7@venturainformatica.com.br"
TENTATIVAS_LEITURA = 5      # outro processo pode estar trocando o PEDIDOS.json no meio da leitura
ESPERA_LEITURA_S = 0.5


class ErroLeituraPlanilha(Exception):
    """Não deu para ler a planilha: sem ela todo pedido pareceria novo, então a extração não segue."""


def numeros_planilha(caminho):
    ultimo_erro = None
    for tentativa in range(TENTATIVAS_LEITURA):
        try:
            with open(caminho, 'r', encoding='utf-8') as f:
                dados = json.load(f)
            return {str(p.get('PEDIDO')).strip() for p in dados.get('PEDIDOS', [])
                    if isinstance(p, dict) and str(p.get('PEDIDO') or '').strip()}
        except (OSError, ValueError, AttributeError) as e:
            ultimo_erro = e
            if tentativa < TENTATIVAS_LEITURA - 1:
                time.sleep(ESPERA_LEITURA_S)
    raise ErroLeituraPlanilha(f"Não foi possível ler {caminho}: {ultimo_erro}")


def carregar_lidos(caminho):
    if not os.path.exists(caminho):
        return []
    with open(caminho, 'r', encoding='utf-8') as f:
        return json.load(f)


def _gravar_json(caminho, dados):
    pasta = os.path.dirname(caminho) or '.'
    os.makedirs(pasta, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.tmp-', suffix='.json', dir=pasta)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(dados, f, indent=4, ensure_ascii=False)
        os.replace(tmp, caminho)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def registrar_lidos(caminho, pedidos):
    lidos = carregar_lidos(caminho)
    posicao = {p.get('pedido'): i for i, p in enumerate(lidos)}
    for pedido in pedidos:
        i = posicao.get(pedido.get('pedido'))
        if i is None:
            posicao[pedido.get('pedido')] = len(lidos)
            lidos.append(pedido)
        else:
            lidos[i] = pedido
    _gravar_json(caminho, lidos)


def registrar_lido(caminho, pedido):
    registrar_lidos(caminho, [pedido])


def pendentes_aviso(lidos, planilha):
    """Lidos que ainda não foram avisados por e-mail e continuam fora da planilha (sem a chave = já avisado)."""
    return [p for p in lidos if p.get('avisado', True) is False and str(p.get('pedido', '')).strip() not in planilha]


def marcar_avisados(caminho, numeros):
    lidos = carregar_lidos(caminho)
    for p in lidos:
        if p.get('pedido') in numeros:
            p['avisado'] = True
    _gravar_json(caminho, lidos)


def gravar_fora_planilha(caminho, lidos, planilha):
    fora = [p for p in lidos if str(p.get('pedido', '')).strip() not in planilha]
    _gravar_json(caminho, fora)
    return fora


def gerar_email_pedidos_novos(novos):
    n = len(novos)
    assunto = f"Coupa: {n} pedido(s) novo(s) fora da planilha"
    linhas, blocos = [f"{n} pedido(s) novo(s) no portal Coupa que não estão na planilha:", ""], []
    for p in novos:
        cab = [('Pedido', p.get('pedido', '')), ('RFQ', p.get('evento', '')), ('Cidade', p.get('cidade_estado', '')),
               ('Frete', p.get('frete', '')), ('Valor', p.get('total', '')), ('Vendedor', p.get('vendedor', ''))]
        itens = p.get('itens') or []
        alertas = p.get('alertas') or [a for it in itens for a in (it.get('alertas') or [])]
        linhas.append(" | ".join(f"{k}: {v}" for k, v in cab))
        linhas += [f"   - {it.get('texto', '')} (prazo {it.get('prazo', '')})" for it in itens]
        linhas += [f"   ALERTA: {a}" for a in alertas]
        linhas.append("")
        esc = lambda v: html.escape(str(v))
        blocos.append(
            '<table cellpadding="4" style="border-collapse:collapse;margin-bottom:16px;border:1px solid #ccc">'
            + ''.join(f'<tr><th align="left">{esc(k)}</th><td>{esc(v)}</td></tr>' for k, v in cab)
            + ''.join(f'<tr><th align="left">Item</th><td>{esc(it.get("texto", ""))} (prazo {esc(it.get("prazo", ""))})</td></tr>'
                      for it in itens)
            + ''.join(f'<tr><th align="left" style="color:#c00">Alerta</th><td style="color:#c00">{esc(a)}</td></tr>'
                      for a in alertas)
            + '</table>')
    corpo_html = (f'<p>{n} pedido(s) novo(s) no portal Coupa que não estão na planilha:</p>' + ''.join(blocos))
    return assunto, "\n".join(linhas), corpo_html


def enviar_email_pedidos_novos(novos, config, log=None):
    log = log or logging.getLogger("CoupaApp")
    if not novos:
        log.info("Nenhum pedido novo: e-mail não enviado.")
        return False
    remetente = (config.get("SMTP_USER") or os.environ.get("SMTP_USER", "")).strip()
    senha = (config.get("SMTP_PASSWORD") or os.environ.get("SMTP_PASSWORD", "")).strip()
    if not remetente or not senha:
        log.warning("Credenciais de SMTP (SMTP_USER / SMTP_PASSWORD) não configuradas: e-mail de pedidos novos não enviado.")
        return False
    destinos = [e.strip() for e in str(config.get("emails_pedidos_novos") or DESTINO_PADRAO).split(',') if e.strip()]
    assunto, texto, corpo_html = gerar_email_pedidos_novos(novos)
    msg = MIMEMultipart("alternative")
    msg["From"], msg["To"], msg["Subject"] = remetente, ", ".join(destinos), assunto
    msg.attach(MIMEText(texto, "plain", "utf-8"))
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))
    try:
        if _conectar_e_enviar_smtp(remetente, senha, destinos, msg, "Coupa Pedidos", log):
            log.info(f"E-mail de pedidos novos enviado para: {', '.join(destinos)}")
            return True
    except Exception as e:
        log.error(f"Erro ao enviar e-mail de pedidos novos: {e}")
    return False
