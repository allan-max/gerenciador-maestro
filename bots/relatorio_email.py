# -*- coding: utf-8 -*-
import os
import time
import logging
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger("RelatorioEmail")


def gerar_conteudo_relatorio(nome_bot: str, stats: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Analisa as estatísticas de extração e gera assunto, corpo texto e corpo HTML.
    Retorna (None, None, None) se não houver NENHUM problema (evita envio de emails vazios).
    """
    if not stats:
        return None, None, None

    erro_critico = stats.get("erro_critico_bot")
    erros_ext = list(dict.fromkeys([str(x).strip() for x in stats.get("erros_extracao", []) if str(x).strip()]))
    eventos_eq = list(dict.fromkeys([str(x).strip() for x in stats.get("eventos_expected_quantity", []) if str(x).strip()]))
    sem_itens_raw = list(dict.fromkeys([str(x).strip() for x in stats.get("eventos_sem_itens", []) if str(x).strip()]))
    # Itens com Expected Quantity são destacados em seção própria para clareza
    sem_itens = [x for x in sem_itens_raw if x not in eventos_eq]
    sem_desc = list(dict.fromkeys([str(x).strip() for x in stats.get("sem_descricao", []) if str(x).strip()]))
    falt_info = list(dict.fromkeys([str(x).strip() for x in stats.get("faltando_info", []) if str(x).strip()]))
    erros_plan = list(dict.fromkeys([str(x).strip() for x in stats.get("erros_planilha", []) if str(x).strip()]))

    tem_problema = bool(
        erro_critico or
        erros_ext or
        eventos_eq or
        sem_itens_raw or
        sem_desc or
        falt_info or
        erros_plan
    )

    if not tem_problema:
        return None, None, None

    # Conjunto único de cotações que tiveram alguma anomalia
    cotacoes_com_problema = set(erros_ext + eventos_eq + sem_itens_raw + sem_desc + falt_info + erros_plan)
    qtd_problemas = len(cotacoes_com_problema)

    total_extraidas = int(stats.get("total_cotacoes", 0))
    # Cotações bem-sucedidas são aquelas que não tiveram nenhuma falha ou dado ausente
    problemas_pos_extracao = set(sem_desc + falt_info + sem_itens_raw + eventos_eq + erros_plan)
    cotacoes_sucesso = max(0, total_extraidas - len(problemas_pos_extracao))

    # Cálculo do tempo de execução
    hora_inicio = stats.get("hora_inicio_extracao")
    if hora_inicio:
        tempo_total = max(0, int(time.time() - hora_inicio))
        minutos, segundos = divmod(tempo_total, 60)
        tempo_str = f"{minutos}m {segundos}s"
    else:
        tempo_str = "Desconhecido"

    data_hora_str = datetime.now().strftime("%d/%m/%Y às %H:%M:%S")

    # Assunto direto e objetivo
    if erro_critico:
        assunto = f"🚨 [Erro Crítico] {nome_bot}: Falha na execução"
    elif qtd_problemas == 1:
        assunto = f"⚠️ [Atenção] {nome_bot}: 1 cotação com problemas detectados"
    else:
        assunto = f"⚠️ [Atenção] {nome_bot}: {qtd_problemas} cotações com problemas detectados"

    # Montagem do Texto Simples
    linhas_texto = [
        f"Relatório de Ocorrências - {nome_bot}",
        f"Data/Hora: {data_hora_str}",
        f"Duração da execução: {tempo_str}",
        f"Total de cotações com problemas: {qtd_problemas}",
        ""
    ]

    if cotacoes_sucesso > 0:
        linhas_texto.append(
            f"✅ SUCESSO NO RESTANTE: As demais {cotacoes_sucesso} cotação(ões) foram extraídas e salvas na planilha com 100% de sucesso."
        )
        linhas_texto.append("")
    elif total_extraidas > 0:
        linhas_texto.append(f"⚠️ Todas as {total_extraidas} cotações processadas apresentaram inconsistências.")
        linhas_texto.append("")

    linhas_texto.append("--- PROBLEMAS DETECTADOS ---")

    if erro_critico:
        linhas_texto.append(f"🚨 ERRO CRÍTICO DO ROBÔ:\n{erro_critico}\n")

    if erros_ext:
        linhas_texto.append(f"❌ Falha ao acessar ou extrair ({len(erros_ext)}): {', '.join(erros_ext)}")

    if eventos_eq:
        linhas_texto.append(f"⚠️ Cotações com 'Expected Quantity x Unit Price' sem itens ({len(eventos_eq)}): {', '.join(eventos_eq)}")

    if sem_itens:
        linhas_texto.append(f"⚠️ Cotações sem itens detectados ({len(sem_itens)}): {', '.join(sem_itens)}")

    if sem_desc:
        linhas_texto.append(f"⚠️ Cotações com itens sem descrição ({len(sem_desc)}): {', '.join(sem_desc)}")

    if falt_info:
        linhas_texto.append(f"⚠️ Cotações com informações faltantes (vencimento/local/frete) ({len(falt_info)}): {', '.join(falt_info)}")

    if erros_plan:
        linhas_texto.append(f"💾 Falha ao salvar na planilha ({len(erros_plan)}): {', '.join(erros_plan)}")

    linhas_texto.append("")
    linhas_texto.append("Atenciosamente,")
    linhas_texto.append(f"{nome_bot} (Aviso automático gerado apenas em caso de divergências)")
    corpo_texto = "\n".join(linhas_texto)

    # Montagem do HTML Visual e Agradável
    def gerar_badge_html(lista):
        return "".join([
            f'<span style="display:inline-block; background-color:#fee2e2; color:#991b1b; padding:3px 9px; '
            f'margin:3px 4px; border-radius:5px; font-weight:600; font-family:monospace; font-size:13px; '
            f'border:1px solid #fca5a5;">#{num}</span>'
            for num in lista
        ])

    secoes_problemas_html = []

    if erro_critico:
        secoes_problemas_html.append(f"""
        <div style="background-color:#fef2f2; border:1px solid #f87171; border-left:5px solid #dc2626; border-radius:6px; padding:12px 16px; margin-bottom:14px;">
            <div style="font-weight:700; color:#991b1b; margin-bottom:6px; font-size:14px;">🚨 Erro Fatal na Rotina do Robô:</div>
            <pre style="margin:0; font-family:monospace; font-size:12px; color:#7f1d1d; white-space:pre-wrap; word-break:break-word;">{erro_critico}</pre>
        </div>
        """)

    if erros_ext:
        secoes_problemas_html.append(f"""
        <div style="background-color:#ffffff; border:1px solid #e2e8f0; border-left:4px solid #ef4444; border-radius:6px; padding:12px 14px; margin-bottom:12px;">
            <div style="font-weight:700; color:#b91c1c; font-size:14px; margin-bottom:6px;">❌ Falha de Acesso ou Extração ({len(erros_ext)}):</div>
            <div style="font-size:12px; color:#64748b; margin-bottom:8px;">O robô não conseguiu acessar ou carregar as informações do evento:</div>
            <div>{gerar_badge_html(erros_ext)}</div>
        </div>
        """)

    if eventos_eq:
        secoes_problemas_html.append(f"""
        <div style="background-color:#ffffff; border:1px solid #e2e8f0; border-left:4px solid #f59e0b; border-radius:6px; padding:12px 14px; margin-bottom:12px;">
            <div style="font-weight:700; color:#b45309; font-size:14px; margin-bottom:6px;">⚠️ Cotações com 'Expected Quantity x Unit Price' ({len(eventos_eq)}):</div>
            <div style="font-size:12px; color:#64748b; margin-bottom:8px;">O evento exibiu apenas o indicador 'Expected Quantity x Unit Price' sem identificação dos itens reais após as tentativas de recuperação:</div>
            <div>{gerar_badge_html(eventos_eq)}</div>
        </div>
        """)

    if sem_itens:
        secoes_problemas_html.append(f"""
        <div style="background-color:#ffffff; border:1px solid #e2e8f0; border-left:4px solid #f59e0b; border-radius:6px; padding:12px 14px; margin-bottom:12px;">
            <div style="font-weight:700; color:#b45309; font-size:14px; margin-bottom:6px;">⚠️ Cotações Sem Itens Detectados ({len(sem_itens)}):</div>
            <div style="font-size:12px; color:#64748b; margin-bottom:8px;">O evento abriu normalmente, mas nenhuma linha de item foi encontrada:</div>
            <div>{gerar_badge_html(sem_itens)}</div>
        </div>
        """)

    if sem_desc:
        secoes_problemas_html.append(f"""
        <div style="background-color:#ffffff; border:1px solid #e2e8f0; border-left:4px solid #f59e0b; border-radius:6px; padding:12px 14px; margin-bottom:12px;">
            <div style="font-weight:700; color:#b45309; font-size:14px; margin-bottom:6px;">⚠️ Cotações Com Itens Sem Descrição ({len(sem_desc)}):</div>
            <div style="font-size:12px; color:#64748b; margin-bottom:8px;">A descrição do item veio vazia, como 'Nenhum' ou 'N/A':</div>
            <div>{gerar_badge_html(sem_desc)}</div>
        </div>
        """)

    if falt_info:
        secoes_problemas_html.append(f"""
        <div style="background-color:#ffffff; border:1px solid #e2e8f0; border-left:4px solid #f59e0b; border-radius:6px; padding:12px 14px; margin-bottom:12px;">
            <div style="font-weight:700; color:#b45309; font-size:14px; margin-bottom:6px;">⚠️ Cotações Com Informações Incompletas ({len(falt_info)}):</div>
            <div style="font-size:12px; color:#64748b; margin-bottom:8px;">Campos essenciais ausentes (vencimento, local de entrega ou modalidade de frete):</div>
            <div>{gerar_badge_html(falt_info)}</div>
        </div>
        """)

    if erros_plan:
        secoes_problemas_html.append(f"""
        <div style="background-color:#ffffff; border:1px solid #e2e8f0; border-left:4px solid #ef4444; border-radius:6px; padding:12px 14px; margin-bottom:12px;">
            <div style="font-weight:700; color:#b91c1c; font-size:14px; margin-bottom:6px;">💾 Falha ao Salvar na Planilha ({len(erros_plan)}):</div>
            <div style="font-size:12px; color:#64748b; margin-bottom:8px;">Houve erro ao registrar estas cotações no arquivo Excel:</div>
            <div>{gerar_badge_html(erros_plan)}</div>
        </div>
        """)

    # Card de sucesso das demais cotações
    sucesso_html = ""
    if cotacoes_sucesso > 0:
        sucesso_html = f"""
        <div style="background-color:#ecfdf5; border:1px solid #a7f3d0; border-left:5px solid #10b981; border-radius:6px; padding:14px 16px; margin-bottom:18px;">
            <div style="display:flex; align-items:center;">
                <span style="font-size:20px; margin-right:10px;">✅</span>
                <div>
                    <div style="font-weight:700; color:#065f46; font-size:14px;">Demais cotações concluídas com sucesso!</div>
                    <div style="font-size:13px; color:#047857; margin-top:3px;">
                        As outras <strong>{cotacoes_sucesso} cotação(ões)</strong> desta rodada foram extraídas e salvas na planilha perfeitamente, sem nenhum erro.
                    </div>
                </div>
            </div>
        </div>
        """
    elif total_extraidas > 0:
        sucesso_html = f"""
        <div style="background-color:#fffbeb; border:1px solid #fde68a; border-left:5px solid #f59e0b; border-radius:6px; padding:14px 16px; margin-bottom:18px;">
            <div style="font-weight:700; color:#92400e; font-size:14px;">Atenção: Todas as {total_extraidas} cotações processadas apresentaram inconsistências.</div>
        </div>
        """

    corpo_html = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{assunto}</title>
    </head>
    <body style="margin:0; padding:20px; background-color:#f1f5f9; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color:#334155;">
        <div style="max-width:620px; margin:0 auto; background-color:#ffffff; border-radius:10px; overflow:hidden; box-shadow:0 4px 6px -1px rgba(0,0,0,0.08), 0 2px 4px -2px rgba(0,0,0,0.06); border:1px solid #e2e8f0;">
            
            <!-- HEADER -->
            <div style="background:linear-gradient(135deg, #1e293b 0%, #0f172a 100%); padding:22px 24px; color:#ffffff;">
                <div style="display:inline-block; background-color:#ef4444; color:#ffffff; font-size:11px; font-weight:800; text-transform:uppercase; letter-spacing:0.5px; padding:4px 8px; border-radius:4px; margin-bottom:8px;">
                    {nome_bot} • Ocorrências Detectadas
                </div>
                <h1 style="margin:0; font-size:20px; font-weight:700; color:#ffffff; line-height:1.3;">
                    Relatório de Inconsistências na Extração
                </h1>
                <div style="margin-top:6px; font-size:12px; color:#94a3b8;">
                    Finalizado em {data_hora_str} • Duração: <strong>{tempo_str}</strong>
                </div>
            </div>

            <!-- CORPO -->
            <div style="padding:24px;">
                
                <!-- CARD DE SUCESSO DO RESTO -->
                {sucesso_html}

                <!-- LISTA DIRETA DE PROBLEMAS -->
                <div style="margin-top:6px;">
                    <div style="font-size:13px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; color:#64748b; margin-bottom:12px;">
                        Detalhamento das Inconsistências ({qtd_problemas} cotação(ões) afetada(s))
                    </div>
                    {''.join(secoes_problemas_html)}
                </div>

            </div>

            <!-- FOOTER -->
            <div style="background-color:#f8fafc; border-top:1px solid #e2e8f0; padding:14px 24px; font-size:11px; color:#64748b; text-align:center; line-height:1.5;">
                Este e-mail é gerado automaticamente pelo robô <strong>{nome_bot}</strong>.<br>
                <span style="color:#059669; font-weight:600;">Regra de disparo:</span> Você só recebe este alerta quando forem identificadas falhas ou dados faltantes.
            </div>

        </div>
    </body>
    </html>
    """

    return assunto, corpo_texto, corpo_html


def enviar_email_relatorio(nome_bot: str, stats: Optional[Dict[str, Any]], config: Dict[str, Any], logger_inst: Optional[logging.Logger] = None) -> bool:
    """
    Envia o e-mail de relatório APENAS se houver problemas detectados.
    Se não houver nenhum problema, suprime o envio e registra log informativo.
    """
    log = logger_inst or logger

    assunto, corpo_texto, corpo_html = gerar_conteudo_relatorio(nome_bot, stats)

    if not assunto:
        log.info(f"✅ [{nome_bot}] Nenhum problema detectado na extração. Envio de e-mail suprimido (tudo correu bem).")
        return False

    remetente = config.get("SMTP_USER", os.environ.get("SMTP_USER", ""))
    senha = config.get("SMTP_PASSWORD", os.environ.get("SMTP_PASSWORD", ""))

    emails_destino_raw = config.get("emails_relatorio", os.environ.get("EMAILS_RELATORIO", ""))
    destinatarios = [e.strip() for e in emails_destino_raw.split(',') if e.strip()]

    if not destinatarios:
        log.warning(f"[{nome_bot}] Nenhum e-mail de destino configurado em emails_relatorio.")
        return False

    if not remetente or not senha:
        log.warning(f"[{nome_bot}] Credenciais de SMTP (SMTP_USER / SMTP_PASSWORD) não configuradas.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = remetente
        msg["To"] = ", ".join(destinatarios)
        msg["Subject"] = assunto
        msg.attach(MIMEText(corpo_texto, "plain", "utf-8"))
        msg.attach(MIMEText(corpo_html, "html", "utf-8"))

        enviado = False
        try:
            server = smtplib.SMTP_SSL("email-ssl.com.br", 465, timeout=15)
            server.login(remetente, senha)
            server.sendmail(remetente, destinatarios, msg.as_string())
            server.quit()
            enviado = True
        except Exception as e1:
            log.warning(f"[{nome_bot}] Erro no SMTP_SSL (465): {e1}. Tentando STARTTLS em smtp.locaweb.com.br...")
            try:
                server = smtplib.SMTP("smtp.locaweb.com.br", 587, timeout=15)
                server.starttls()
                server.login(remetente, senha)
                server.sendmail(remetente, destinatarios, msg.as_string())
                server.quit()
                enviado = True
            except Exception as e2:
                log.warning(f"[{nome_bot}] Erro no STARTTLS smtp.locaweb.com.br: {e2}. Tentando email-ssl.com.br na porta 587...")
                server = smtplib.SMTP("email-ssl.com.br", 587, timeout=15)
                server.starttls()
                server.login(remetente, senha)
                server.sendmail(remetente, destinatarios, msg.as_string())
                server.quit()
                enviado = True

        if enviado:
            log.info(f"🚨 [{nome_bot}] E-mail de ocorrências enviado com sucesso para: {', '.join(destinatarios)}")
            return True

    except Exception as e:
        log.error(f"❌ [{nome_bot}] Erro ao enviar e-mail de ocorrências: {e}")
        return False

    return False
