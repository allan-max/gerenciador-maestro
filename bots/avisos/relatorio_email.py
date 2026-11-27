# -*- coding: utf-8 -*-
import os
import time
import logging
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from typing import Dict, Any, List, Tuple, Optional

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




def _conectar_e_enviar_smtp(remetente: str, senha: str, destinatarios: List[str], msg: MIMEMultipart, nome_bot: str, log: logging.Logger) -> bool:
    """
    Função auxiliar para transporte SMTP seguro (465 SSL com fallback para 587 STARTTLS).
    """
    try:
        server = smtplib.SMTP_SSL("email-ssl.com.br", 465, timeout=15)
        server.login(remetente, senha)
        server.sendmail(remetente, destinatarios, msg.as_string())
        server.quit()
        return True
    except Exception as e1:
        log.warning(f"[{nome_bot}] Erro no SMTP_SSL (465): {e1}. Tentando STARTTLS em smtp.locaweb.com.br...")
        try:
            server = smtplib.SMTP("smtp.locaweb.com.br", 587, timeout=15)
            server.starttls()
            server.login(remetente, senha)
            server.sendmail(remetente, destinatarios, msg.as_string())
            server.quit()
            return True
        except Exception as e2:
            log.warning(f"[{nome_bot}] Erro no STARTTLS smtp.locaweb.com.br: {e2}. Tentando email-ssl.com.br na porta 587...")
            try:
                server = smtplib.SMTP("email-ssl.com.br", 587, timeout=15)
                server.starttls()
                server.login(remetente, senha)
                server.sendmail(remetente, destinatarios, msg.as_string())
                server.quit()
                return True
            except Exception as e3:
                log.error(f"[{nome_bot}] Falha definitiva em todos os servidores SMTP: {e3}")
                return False


def gerar_conteudo_email_erro_salvar_item(
    nome_bot: str,
    evento: str,
    item_num: int,
    erro_portal: str,
    campos_vazios: List[str],
    itens_sucesso: Optional[List[int]] = None,
    total_itens: int = 1,
    tem_print: bool = False
) -> Tuple[str, str, str]:
    """
    Gera o assunto, corpo em texto e HTML visual e direto para alertar sobre falha
    ao salvar a resposta de um item no portal ("Resolva todos os erros e tente outra vez").
    """
    ev_str = str(evento).replace("Evento ", "").strip()
    secao_print_texto = f"\n📸 Captura de tela (print) da tela anexada: print_erro_evento_{ev_str}.png\n" if tem_print else ""
    secao_print_html = f"""
    <div style="background-color:#ffffff; border:1px solid #cbd5e1; border-radius:8px; overflow:hidden; margin-bottom:14px;">
        <div style="background-color:#1e293b; color:#f8fafc; font-size:12px; font-weight:700; padding:8px 12px; display:flex; justify-content:space-between; align-items:center;">
            <span>📸 Captura de Tela no Momento do Erro (em anexo):</span>
            <span style="font-size:11px; color:#94a3b8; font-family:monospace;">print_erro_evento_{ev_str}.png</span>
        </div>
        <div style="padding:10px; text-align:center; background-color:#0f172a;">
            <img src="cid:print_erro_screenshot" alt="Captura de tela do erro no portal" style="max-width:100%; height:auto; border-radius:4px; border:1px solid #334155; display:block; margin:0 auto;" />
        </div>
    </div>
    """ if tem_print else ""

    data_hora_str = datetime.now().strftime("%d/%m/%Y às %H:%M")
    itens_sucesso = itens_sucesso or []
    campos_vazios_limpos = [str(c).strip() for c in campos_vazios if str(c).strip()] or ["Campos obrigatórios do formulário"]

    assunto = f"🚨 [{nome_bot}] Erro ao Salvar Resposta - Evento {ev_str} (Item {item_num})"

    # Texto Puro
    linhas_texto = [
        f"Alerta de Inconsistência no Salvamento - {nome_bot}",
        f"Data/Hora: {data_hora_str}",
        "",
        f"❌ O portal impediu o salvamento da resposta do Item {item_num} no Evento #{ev_str}:",
        f"• Erro do Portal: {erro_portal or 'Resolva todos os erros e tente outra vez.'}",
        f"• Campo(s) que ficou(ram) vazio(s) e impediram o salvamento: {', '.join(campos_vazios_limpos)}",
        "",
        "Ação do Robô:",
        "O robô detectou a mensagem de erro de validação do portal, inspecionou todos os campos do item,",
        "e tentou repreencher automaticamente os campos em branco. Como o salvamento continuou sendo",
        "recusado pelo portal por falta dessas informações, o alerta foi disparado para complementação manual.",
        ""
    ]

    if itens_sucesso:
        linhas_texto.append(f"✅ Demais itens concluídos com sucesso: Item(ns) [{', '.join(str(i) for i in itens_sucesso)}] foram salvos corretamente.")
    else:
        linhas_texto.append("⚠️ Nenhum outro item foi salvo para esta cotação até o momento.")

    if secao_print_texto:
        linhas_texto.append(secao_print_texto)
    linhas_texto.append("")
    linhas_texto.append("Atenciosamente,")
    linhas_texto.append(f"{nome_bot} (Aviso automático gerado apenas em caso de erro)")
    corpo_texto = "\n".join(linhas_texto)

    # Badges HTML para os campos vazios
    badges_campos = "".join([
        f'<span style="display:inline-block; background-color:#fee2e2; color:#991b1b; padding:4px 10px; '
        f'margin:3px 4px; border-radius:5px; font-weight:700; font-family:monospace; font-size:13px; '
        f'border:1px solid #fca5a5;">{campo}</span>'
        for campo in campos_vazios_limpos
    ])

    # Status dos outros itens
    if itens_sucesso:
        sucesso_html = f"""
        <div style="background-color:#ecfdf5; border:1px solid #a7f3d0; border-left:5px solid #10b981; border-radius:6px; padding:14px 16px; margin-bottom:18px;">
            <div style="display:flex; align-items:center;">
                <span style="font-size:20px; margin-right:10px;">✅</span>
                <div>
                    <div style="font-weight:700; color:#065f46; font-size:14px;">Demais itens processados com sucesso!</div>
                    <div style="font-size:13px; color:#047857; margin-top:3px;">
                        O(s) item(ns) <strong>{', '.join(str(i) for i in itens_sucesso)}</strong> desta cotação foi(ram) preenchido(s) e salvo(s) perfeitamente no portal.
                    </div>
                </div>
            </div>
        </div>
        """
    else:
        sucesso_html = """
        <div style="background-color:#fffbeb; border:1px solid #fde68a; border-left:5px solid #f59e0b; border-radius:6px; padding:14px 16px; margin-bottom:18px;">
            <div style="font-weight:700; color:#92400e; font-size:14px;">Atenção: Nenhum outro item foi salvo para esta cotação até o momento.</div>
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
                    {nome_bot} • Resposta de Cotação
                </div>
                <h1 style="margin:0; font-size:20px; font-weight:700; color:#ffffff; line-height:1.3;">
                    Falha ao Salvar Resposta no Portal
                </h1>
                <div style="margin-top:6px; font-size:12px; color:#94a3b8;">
                    Evento <strong>#{ev_str}</strong> • Item <strong>{item_num}</strong> de {total_itens} • Registrado em {data_hora_str}
                </div>
            </div>

            <!-- CORPO -->
            <div style="padding:24px;">
                
                <!-- CARD DE SUCESSO DO RESTO -->
                {sucesso_html}

                <!-- ALERTA DO ERRO -->
                <div style="background-color:#fef2f2; border:1px solid #f87171; border-left:5px solid #dc2626; border-radius:6px; padding:12px 16px; margin-bottom:14px;">
                    <div style="font-weight:700; color:#991b1b; margin-bottom:4px; font-size:14px;">❌ Mensagem Retornada pelo Portal ao Salvar:</div>
                    <div style="font-family:monospace; font-size:13px; color:#7f1d1d; background-color:#fee2e2; padding:6px 10px; border-radius:4px; display:inline-block;">
                        {erro_portal or 'Resolva todos os erros e tente outra vez.'}
                    </div>
                </div>

                <!-- CAMPOS EM BRANCO -->
                <div style="background-color:#ffffff; border:1px solid #e2e8f0; border-left:4px solid #ef4444; border-radius:6px; padding:14px; margin-bottom:14px;">
                    <div style="font-weight:700; color:#b91c1c; font-size:14px; margin-bottom:6px;">⚠️ Campo(s) que ficou(ram) vazio(s) e impediram o salvamento:</div>
                    <div style="font-size:12px; color:#64748b; margin-bottom:10px;">
                        O robô identificou que os seguintes campos não possuem valor ou foram rejeitados pela validação do portal:
                    </div>
                    <div>{badges_campos}</div>
                </div>

                <!-- PRINT ANEXADA SE HOUVER -->
                {secao_print_html}

                <!-- AÇÃO AUTOMÁTICA REALIZADA -->
                <div style="background-color:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:12px 14px; margin-bottom:14px; font-size:12px; color:#475569; line-height:1.5;">
                    <strong>ℹ️ Tentativa de Recuperação Automática:</strong> O robô detectou a mensagem de inconsistência, inspecionou todos os campos do item e tentou repreencher automaticamente os campos em branco. Como o portal continuou recusando o salvamento por falta de preenchimento dessas informações, o evento requer atenção manual no portal.
                </div>

            </div>

            <!-- FOOTER -->
            <div style="background-color:#f8fafc; border-top:1px solid #e2e8f0; padding:14px 24px; font-size:11px; color:#64748b; text-align:center; line-height:1.5;">
                Este e-mail é gerado automaticamente pelo robô <strong>{nome_bot}</strong> ao identificar recusa de salvamento de item.<br>
                <span style="color:#059669; font-weight:600;">Regra de disparo:</span> Você só recebe este alerta quando houver erro real impedindo a gravação no portal.
            </div>

        </div>
    </body>
    </html>
    """

    return assunto, corpo_texto, corpo_html


def enviar_email_erro_salvar_item(
    nome_bot: str,
    evento: str,
    item_num: int,
    erro_portal: str,
    campos_vazios: List[str],
    config: Dict[str, Any],
    logger_inst: Optional[logging.Logger] = None,
    itens_sucesso: Optional[List[int]] = None,
    total_itens: int = 1,
    print_bytes: Optional[bytes] = None,
    destinatario_obrigatorio: Optional[str] = None
) -> bool:
    """
    Envia e-mail de alerta quando o portal exibe 'Resolva todos os erros e tente outra vez'
    e campos em branco persistem mesmo após a tentativa automática de repreenchimento.
    """
    log = logger_inst or logger

    assunto, corpo_texto, corpo_html = gerar_conteudo_email_erro_salvar_item(
        nome_bot=nome_bot,
        evento=evento,
        item_num=item_num,
        erro_portal=erro_portal,
        campos_vazios=campos_vazios,
        itens_sucesso=itens_sucesso,
        total_itens=total_itens,
        tem_print=bool(print_bytes)
    )

    remetente = (config.get("SMTP_USER") or os.environ.get("SMTP_USER", "")).strip()
    senha = (config.get("SMTP_PASSWORD") or os.environ.get("SMTP_PASSWORD", "")).strip()

    emails_destino_raw = (config.get("emails_relatorio") or os.environ.get("EMAILS_RELATORIO", "")).strip()
    destinatarios_set = set()
    if destinatario_obrigatorio:
        destinatarios_set.add(destinatario_obrigatorio.strip().lower())
    for e in emails_destino_raw.split(','):
        if e.strip():
            destinatarios_set.add(e.strip().lower())
    destinatarios = sorted(list(destinatarios_set))

    if not destinatarios:
        log.warning(f"[{nome_bot}] Nenhum e-mail de destino configurado em emails_relatorio para envio de erro de salvamento de item.")
        return False

    if not remetente or not senha:
        log.warning(f"[{nome_bot}] Credenciais de SMTP (SMTP_USER / SMTP_PASSWORD) não configuradas.")
        return False

    try:
        ev_str = str(evento).replace("Evento ", "").strip()
        if print_bytes:
            msg = MIMEMultipart("mixed")
            corpo_alt = MIMEMultipart("alternative")
            corpo_alt.attach(MIMEText(corpo_texto, "plain", "utf-8"))
            corpo_alt.attach(MIMEText(corpo_html, "html", "utf-8"))
            msg.attach(corpo_alt)
            try:
                nome_anexo = f"print_erro_evento_{ev_str}.png"
                img_part = MIMEImage(print_bytes, name=nome_anexo)
                img_part.add_header('Content-Disposition', 'attachment', filename=nome_anexo)
                img_part.add_header('Content-ID', '<print_erro_screenshot>')
                msg.attach(img_part)
            except Exception as e_att:
                log.warning(f"[{nome_bot}] Erro ao anexar print do erro: {e_att}")
        else:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(corpo_texto, "plain", "utf-8"))
            msg.attach(MIMEText(corpo_html, "html", "utf-8"))

        msg["From"] = remetente
        msg["To"] = ", ".join(destinatarios)
        msg["Subject"] = assunto

        if _conectar_e_enviar_smtp(remetente, senha, destinatarios, msg, nome_bot, log):
            log.info(f"🚨 [{nome_bot}] E-mail de alerta de erro ao salvar Item {item_num} (Evento {evento}) enviado com sucesso para: {', '.join(destinatarios)}")
            return True
    except Exception as e:
        log.error(f"❌ [{nome_bot}] Erro ao enviar e-mail de erro ao salvar item: {e}")

    return False


def gerar_conteudo_email_erro_resposta(
    nome_bot: str,
    evento: str,
    erro_detalhado: str,
    etapa: str = "Processamento da Resposta",
    campos_vazios: Optional[List[str]] = None,
    item_num: Optional[int] = None,
    itens_sucesso: Optional[List[int]] = None,
    total_itens: int = 1,
    tem_print: bool = False
) -> Tuple[str, str, str]:
    """
    Gera assunto, texto puro e HTML rico detalhado para qualquer erro que ocorra
    durante o processo de resposta a uma cotação/evento.
    """
    ev_str = str(evento).replace("Evento ", "").strip()
    data_hora_str = datetime.now().strftime("%d/%m/%Y às %H:%M")
    itens_sucesso = itens_sucesso or []
    campos_vazios_limpos = [str(c).strip() for c in (campos_vazios or []) if str(c).strip()]

    item_sufixo = f" (Item {item_num})" if item_num else ""
    assunto = f"🚨 [{nome_bot}] Erro ao Responder Evento #{ev_str}{item_sufixo}"

    # Texto Puro
    linhas_texto = [
        f"Alerta de Falha ao Responder Cotação - {nome_bot}",
        f"Data/Hora: {data_hora_str}",
        f"Evento/Cotação: #{ev_str}",
        f"Etapa da Falha: {etapa}",
    ]
    if item_num:
        linhas_texto.append(f"Item Afetado: Item {item_num} de {total_itens}")
    linhas_texto.extend([
        "",
        "❌ Descrição do Problema:",
        f"{erro_detalhado}",
        ""
    ])
    if campos_vazios_limpos:
        linhas_texto.append(f"⚠️ Campo(s) inconsistente(s) ou não preenchido(s): {', '.join(campos_vazios_limpos)}")
        linhas_texto.append("")
    if itens_sucesso:
        linhas_texto.append(f"✅ Itens salvos com sucesso antes da ocorrência: [{', '.join(str(i) for i in itens_sucesso)}]")
        linhas_texto.append("")
    if tem_print:
        linhas_texto.append(f"📸 Captura de tela (print) da tela anexada a este e-mail: print_erro_evento_{ev_str}.png")
        linhas_texto.append("")

    linhas_texto.extend([
        "Ação Recomendada:",
        f"Por favor, acesse o portal da {nome_bot.replace(' Bot', '')} para verificar o status do evento #{ev_str} e concluir a resposta manualmente.",
        "",
        "Atenciosamente,",
        f"{nome_bot} (Sistema Maestro - Notificação Automática de Falha)"
    ])
    corpo_texto = "\n".join(linhas_texto)

    # HTML
    badges_campos_html = ""
    if campos_vazios_limpos:
        badges_spans = "".join([
            f'<span style="display:inline-block; background-color:#fee2e2; color:#991b1b; padding:4px 10px; '
            f'margin:3px 4px; border-radius:5px; font-weight:700; font-family:monospace; font-size:13px; '
            f'border:1px solid #fca5a5;">{campo}</span>'
            for campo in campos_vazios_limpos
        ])
        badges_campos_html = f"""
        <div style="background-color:#ffffff; border:1px solid #e2e8f0; border-left:4px solid #ef4444; border-radius:6px; padding:14px; margin-bottom:14px;">
            <div style="font-weight:700; color:#b91c1c; font-size:14px; margin-bottom:6px;">⚠️ Campo(s) que ficaram em branco ou impediram a resposta:</div>
            <div>{badges_spans}</div>
        </div>
        """

    sucesso_html = ""
    if itens_sucesso:
        sucesso_html = f"""
        <div style="background-color:#ecfdf5; border:1px solid #a7f3d0; border-left:5px solid #10b981; border-radius:6px; padding:14px 16px; margin-bottom:14px;">
            <div style="font-weight:700; color:#065f46; font-size:14px;">✅ Itens salvos com sucesso anteriormente:</div>
            <div style="font-size:13px; color:#047857; margin-top:3px;">
                O(s) item(ns) <strong>{', '.join(str(i) for i in itens_sucesso)}</strong> foram gravados com êxito no rascunho do portal antes desta falha.
            </div>
        </div>
        """

    secao_print_html = f"""
    <div style="background-color:#ffffff; border:1px solid #cbd5e1; border-radius:8px; overflow:hidden; margin-bottom:14px;">
        <div style="background-color:#1e293b; color:#f8fafc; font-size:12px; font-weight:700; padding:8px 12px; display:flex; justify-content:space-between; align-items:center;">
            <span>📸 Captura de Tela no Momento do Erro (em anexo):</span>
            <span style="font-size:11px; color:#94a3b8; font-family:monospace;">print_erro_evento_{ev_str}.png</span>
        </div>
        <div style="padding:10px; text-align:center; background-color:#0f172a;">
            <img src="cid:print_erro_screenshot" alt="Captura de tela do erro no portal" style="max-width:100%; height:auto; border-radius:4px; border:1px solid #334155; display:block; margin:0 auto;" />
        </div>
    </div>
    """ if tem_print else ""

    item_sub_str = f" • Item <strong>{item_num}</strong> de {total_itens}" if item_num else ""
    corpo_html = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{assunto}</title>
    </head>
    <body style="margin:0; padding:20px; background-color:#f1f5f9; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color:#334155;">
        <div style="max-width:640px; margin:0 auto; background-color:#ffffff; border-radius:10px; overflow:hidden; box-shadow:0 4px 6px -1px rgba(0,0,0,0.08); border:1px solid #e2e8f0;">
            
            <!-- HEADER -->
            <div style="background:linear-gradient(135deg, #1e293b 0%, #0f172a 100%); padding:22px 24px; color:#ffffff;">
                <div style="display:inline-block; background-color:#ef4444; color:#ffffff; font-size:11px; font-weight:800; text-transform:uppercase; letter-spacing:0.5px; padding:4px 8px; border-radius:4px; margin-bottom:8px;">
                    {nome_bot} • Resposta de Cotação
                </div>
                <h1 style="margin:0; font-size:20px; font-weight:700; color:#ffffff; line-height:1.3;">
                    Falha ao Responder Cotação
                </h1>
                <div style="margin-top:6px; font-size:12px; color:#94a3b8;">
                    Evento <strong>#{ev_str}</strong>{item_sub_str} • Etapa: <strong>{etapa}</strong> • {data_hora_str}
                </div>
            </div>

            <!-- CORPO -->
            <div style="padding:24px;">
                
                {sucesso_html}

                <!-- ALERTA DO PROBLEMA -->
                <div style="background-color:#fef2f2; border:1px solid #f87171; border-left:5px solid #dc2626; border-radius:6px; padding:14px 16px; margin-bottom:14px;">
                    <div style="font-weight:700; color:#991b1b; margin-bottom:6px; font-size:14px;">❌ Detalhes da Ocorrência no Portal:</div>
                    <div style="font-family:monospace; font-size:13px; color:#7f1d1d; background-color:#fee2e2; padding:8px 12px; border-radius:4px; line-height:1.4; word-break:break-word;">
                        {erro_detalhado}
                    </div>
                </div>

                {badges_campos_html}

                {secao_print_html}

                <!-- AÇÃO RECOMENDADA -->
                <div style="background-color:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:14px; margin-bottom:14px; font-size:12px; color:#475569; line-height:1.5;">
                    <strong>ℹ️ Próximos Passos:</strong> O robô interrompeu a operação de resposta do evento #{ev_str} para evitar envio incorreto ou corrupção de dados. Acesse o portal e valide a cotação manualmente.
                </div>

            </div>

            <!-- FOOTER -->
            <div style="background-color:#f8fafc; border-top:1px solid #e2e8f0; padding:14px 24px; font-size:11px; color:#64748b; text-align:center; line-height:1.5;">
                Este e-mail é gerado automaticamente pelo robô <strong>{nome_bot}</strong> ao identificar falha na resposta da cotação.<br>
                Destinatário prioritário: <span style="color:#0284c7; font-weight:600;">vendas7@venturainformatica.com.br</span>
            </div>

        </div>
    </body>
    </html>
    """

    return assunto, corpo_texto, corpo_html


def enviar_email_erro_resposta(
    nome_bot: str,
    evento: str,
    erro_detalhado: str,
    etapa: str = "Processamento da Resposta",
    campos_vazios: Optional[List[str]] = None,
    item_num: Optional[int] = None,
    print_bytes: Optional[bytes] = None,
    config: Optional[Dict[str, Any]] = None,
    logger_inst: Optional[logging.Logger] = None,
    itens_sucesso: Optional[List[int]] = None,
    total_itens: int = 1,
    destinatario_obrigatorio: Optional[str] = "vendas7@venturainformatica.com.br"
) -> bool:
    """
    Envia e-mail detalhado com print da tela para vendas7@venturainformatica.com.br
    (e demais destinatários configurados) sempre que ocorrer qualquer erro ao responder uma cotação.
    """
    log = logger_inst or logger
    cfg = config or {}

    assunto, corpo_texto, corpo_html = gerar_conteudo_email_erro_resposta(
        nome_bot=nome_bot,
        evento=evento,
        erro_detalhado=erro_detalhado,
        etapa=etapa,
        campos_vazios=campos_vazios,
        item_num=item_num,
        itens_sucesso=itens_sucesso,
        total_itens=total_itens,
        tem_print=bool(print_bytes)
    )

    remetente = (cfg.get("SMTP_USER") or os.environ.get("SMTP_USER", "")).strip()
    senha = (cfg.get("SMTP_PASSWORD") or os.environ.get("SMTP_PASSWORD", "")).strip()

    emails_destino_raw = (cfg.get("emails_relatorio") or os.environ.get("EMAILS_RELATORIO", "")).strip()
    destinatarios_set = set()
    if destinatario_obrigatorio:
        destinatarios_set.add(destinatario_obrigatorio.strip().lower())
    for e in emails_destino_raw.split(','):
        if e.strip():
            destinatarios_set.add(e.strip().lower())
    destinatarios = sorted(list(destinatarios_set))

    if not destinatarios:
        log.warning(f"[{nome_bot}] Nenhum e-mail de destino configurado para envio de erro de resposta.")
        return False

    if not remetente or not senha:
        log.warning(f"[{nome_bot}] Credenciais de SMTP (SMTP_USER / SMTP_PASSWORD) não configuradas.")
        return False

    try:
        ev_str = str(evento).replace("Evento ", "").strip()
        if print_bytes:
            msg = MIMEMultipart("mixed")
            corpo_alt = MIMEMultipart("alternative")
            corpo_alt.attach(MIMEText(corpo_texto, "plain", "utf-8"))
            corpo_alt.attach(MIMEText(corpo_html, "html", "utf-8"))
            msg.attach(corpo_alt)
            try:
                nome_anexo = f"print_erro_evento_{ev_str}.png"
                img_part = MIMEImage(print_bytes, name=nome_anexo)
                img_part.add_header('Content-Disposition', 'attachment', filename=nome_anexo)
                img_part.add_header('Content-ID', '<print_erro_screenshot>')
                msg.attach(img_part)
            except Exception as e_att:
                log.warning(f"[{nome_bot}] Erro ao anexar print do erro: {e_att}")
        else:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(corpo_texto, "plain", "utf-8"))
            msg.attach(MIMEText(corpo_html, "html", "utf-8"))

        msg["From"] = remetente
        msg["To"] = ", ".join(destinatarios)
        msg["Subject"] = assunto

        if _conectar_e_enviar_smtp(remetente, senha, destinatarios, msg, nome_bot, log):
            log.info(f"🚨 [{nome_bot}] E-mail detalhado de erro de resposta com print enviado para: {', '.join(destinatarios)}")
            return True
    except Exception as e:
        log.error(f"❌ [{nome_bot}] Erro ao enviar e-mail de erro de resposta: {e}")

    return False

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

    remetente = (config.get("SMTP_USER") or os.environ.get("SMTP_USER", "")).strip()
    senha = (config.get("SMTP_PASSWORD") or os.environ.get("SMTP_PASSWORD", "")).strip()

    emails_destino_raw = (config.get("emails_relatorio") or os.environ.get("EMAILS_RELATORIO", "")).strip()
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

        if _conectar_e_enviar_smtp(remetente, senha, destinatarios, msg, nome_bot, log):
            log.info(f"🚨 [{nome_bot}] E-mail de ocorrências enviado com sucesso para: {', '.join(destinatarios)}")
            return True

    except Exception as e:
        log.error(f"❌ [{nome_bot}] Erro ao enviar e-mail de ocorrências: {e}")
        return False

    return False
