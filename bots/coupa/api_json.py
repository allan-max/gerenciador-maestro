# -*- coding: utf-8 -*-
"""Leitura do Coupa pelos endpoints JSON que a própria tela usa (lista de eventos e itens do evento).

Não é a API oficial (essa exige chave do comprador): são chamadas internas, autenticadas pelo cookie da
sessão do navegador. Por isso o fetch roda DENTRO do navegador do robô, e por isso tudo aqui devolve
None quando algo foge do esperado: quem chama volta para a leitura de tela, que continua existindo.
"""
import html
import json
import logging
import re
from urllib.parse import urlencode

logger = logging.getLogger("CoupaApp")

ORIGEM = "https://vale.coupahost.com"
URL_LISTA = (ORIGEM + "/quote_supplier_land/search_quote_request_table?data_table_name=quote_request&filter=0"
             "&key=QuoteSupplierLandController%3Aquote_request&page={pagina}&pglink=true&as_json=true")
URL_ITENS = ("/quotes/responses_lots/lots_with_lines?quote_request_id={evento}&quote_response_id={resposta}"
             "&page={pagina}&format=json")
PAGINA_LEVE = ORIGEM + "/robots.txt"   # só para o navegador ficar na origem do Coupa da Vale antes do fetch

TIMEOUT_LISTA_S = 90   # a lista é lenta no servidor (passa de 20 s)
TIMEOUT_ITENS_S = 40
MAX_PAGINAS_ITENS = 50
VAZIOS = ('', 'nenhum', 'none', 'n/a')

_JS_FETCH = """
var url = arguments[0], ms = arguments[1], fim = arguments[arguments.length - 1];
var c = new AbortController();
var t = setTimeout(function () { c.abort(); }, ms);
fetch(url, {headers: {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
            credentials: 'same-origin', signal: c.signal})
  .then(function (r) { return r.text().then(function (s) { fim({status: r.status, url: r.url, corpo: s}); }); })
  .catch(function (e) { fim({erro: String(e)}); })
  .finally(function () { clearTimeout(t); });
"""


_JS_REQUISICAO = """
var url = arguments[0], ms = arguments[1], metodo = arguments[2], corpo = arguments[3], cab = arguments[4] || {};
var fim = arguments[arguments.length - 1];
var c = new AbortController();
var t = setTimeout(function () { c.abort(); }, ms);
var op = {method: metodo, headers: cab, credentials: 'same-origin', signal: c.signal, redirect: 'follow'};
if (corpo !== null && corpo !== undefined) { op.body = corpo; }
fetch(url, op)
  .then(function (r) { return r.text().then(function (s) { fim({status: r.status, url: r.url, corpo: s}); }); })
  .catch(function (e) { fim({erro: String(e)}); })
  .finally(function () { clearTimeout(t); });
"""


def _sessao_caida(r):
    u = str(r.get('url') or '')
    return 'login' in u or '/sessions/' in u


def requisitar(driver, url, timeout_s, metodo='GET', corpo=None, cabecalhos=None):
    """Pedido HTTP pelo navegador do robô (mesma sessão). {status, url, corpo} ou None (erro de rede/timeout)."""
    try:
        driver.set_script_timeout(timeout_s + 10)
        r = driver.execute_async_script(_JS_REQUISICAO, url, int(timeout_s * 1000), metodo, corpo, cabecalhos or {}) or {}
    except Exception as e:
        logger.warning(f"    API JSON: falha no {metodo} ({type(e).__name__}).")
        return None
    if r.get('erro'):
        logger.warning(f"    API JSON: {metodo} falhou ({r.get('erro')}).")
        return None
    return r


def buscar_json(driver, url, timeout_s):
    """GET pelo navegador do robô (mesma sessão). Devolve o JSON ou None (sessão caída, erro, não-JSON)."""
    try:
        driver.set_script_timeout(timeout_s + 10)
        r = driver.execute_async_script(_JS_FETCH, url, int(timeout_s * 1000)) or {}
    except Exception as e:
        logger.warning(f"    API JSON: falha no fetch ({type(e).__name__}).")
        return None
    if r.get('erro') or r.get('status') != 200:
        logger.warning(f"    API JSON: resposta inválida ({r.get('erro') or r.get('status')}).")
        return None
    if _sessao_caida(r):
        logger.warning("    API JSON: sessão expirada (redirecionou para o login).")
        return None
    try:
        dados = json.loads(r.get('corpo') or '')
    except ValueError:
        logger.warning("    API JSON: o portal não devolveu JSON.")
        return None
    return dados if isinstance(dados, dict) else None


def ir_para_origem(driver):
    """O fetch só leva o cookie se a página atual for do Coupa da Vale; fora dela, abre uma página leve."""
    if not str(driver.current_url or '').startswith(ORIGEM):
        driver.get(PAGINA_LEVE)


# ---------------------------------------------------------------- lista de eventos

def linhas_da_lista(dados):
    """Linhas da lista de eventos no formato que a varredura e a verificação usam."""
    linhas = []
    for r in (dados or {}).get('rows') or []:
        celula = str(r.get('id') or '')
        num = str(r.get('id_num') or '').strip() or re.sub(r'<[^>]+>', '', celula).strip()
        href = re.search(r'href="([^"]+)"', celula)
        if not num or not href:
            continue
        try:
            respostas = int(str(r.get('num_responses')).strip())
        except (TypeError, ValueError):
            respostas = None   # sem número não dá para afirmar que é 0
        url = html.unescape(href.group(1))
        linhas.append({
            'num': num,
            'url': url if url.startswith('http') else ORIGEM + url,
            'descricao': _texto(r.get('description')),
            'venc_tabela': _texto(r.get('end_time')),
            'respostas': respostas,
        })
    return linhas


def tem_proxima(dados):
    """Há outra página depois desta? Pelo total quando o portal informa; senão, página cheia = pode haver."""
    criterio = (dados or {}).get('filterCriteria') or {}
    linhas = len((dados or {}).get('rows') or [])
    try:
        por_pagina = int(criterio.get('rowsPerPage'))
        pagina = int(criterio.get('currentPage'))
    except (TypeError, ValueError):
        return False
    try:
        return pagina * por_pagina < int(dados.get('totalNumRows'))
    except (TypeError, ValueError):
        return linhas >= por_pagina > 0


def ler_pagina_lista(driver, pagina):
    """(linhas, tem_proxima) de uma página da lista, ou None se a API não respondeu como esperado."""
    ir_para_origem(driver)
    dados = buscar_json(driver, URL_LISTA.format(pagina=pagina), TIMEOUT_LISTA_S)
    if dados is None or not isinstance(dados.get('rows'), list):
        return None
    try:
        if int((dados.get('filterCriteria') or {}).get('currentPage')) != pagina:
            return [], False   # o portal devolveu outra página: passou do fim da lista
    except (TypeError, ValueError):
        return None
    return linhas_da_lista(dados), tem_proxima(dados)


# ---------------------------------------------------------------- itens do evento

def _texto(valor):
    """Texto limpo de um campo do JSON (que às vezes vem com HTML)."""
    if valor is None:
        return ''
    t = re.sub(r'<br\s*/?>', '\n', str(valor))
    t = html.unescape(re.sub(r'<[^>]+>', '', t))
    return t.strip()


def _valido(t):
    return bool(t) and t.strip().lower() not in VAZIOS


def _valor(atributos, campo, chave='value'):
    v = (atributos.get(campo) or {}).get(chave)
    return '' if v is None else v


def quantidade_tela(valor, unidade):
    """'1.0' + 'PC-peca' -> '1 PC-peca', como a tela mostra (decimal com vírgula, sem zeros à direita)."""
    try:
        n = float(str(valor).strip())
    except (TypeError, ValueError):
        return '0'
    numero = (f"{n:.6f}".rstrip('0').rstrip('.') or '0').replace('.', ',')
    return f"{numero} {unidade}".strip()


def local_do_endereco(endereco):
    """Cidade/UF do endereço de entrega: o que vem depois do CEP; sem CEP, o endereço inteiro numa linha."""
    texto = _texto(endereco)
    for linha in texto.split('\n'):
        if re.search(r'\d{5}-\d{3}', linha):
            cidade = re.sub(r'.*?\d{5}-\d{3}\s*', '', linha).strip()
            if cidade:
                return re.sub(r'\s+', ' ', cidade)
    return re.sub(r'\s+', ' ', texto).strip() or 'N/A'


def _textos_da_requisicao(atributos):
    """(texto do item, texto de compra) pelos nomes dos campos: a coluna (custom_field_N) pode mudar."""
    campos = atributos.get('request_line_custom_fields') or {}
    valores = campos.get('custom_field_values') or {}
    por_nome = {str(c.get('field_name') or ''): str(c.get('col_name') or '')
                for c in campos.get('custom_field_attributes') or [] if isinstance(c, dict)}
    ler = lambda nome: _texto(valores.get(por_nome.get(nome, '')))
    return ler('item_text'), ler('material_purchase_text')


def item_da_linha(atributos, item_num, filtrar_desc=lambda t: t):
    """Uma linha do JSON no formato de item do robô (o mesmo de _extrair_itens_linhas)."""
    texto_item, texto_compra_bruto = _textos_da_requisicao(atributos)
    texto_compra = filtrar_desc(texto_compra_bruto)
    desc = filtrar_desc(_texto(_valor(atributos, 'extended_description')))
    if not _valido(desc):
        desc = texto_compra if _valido(texto_compra) else texto_item
    frete = str(_valor(atributos, 'shipping_term', 'code')).strip().upper()
    return {
        'item_num': item_num,
        'titulo': _texto(_valor(atributos, 'description')) or 'Item',
        'descricao': desc if _valido(desc) else '',
        'quantidade': quantidade_tela(_valor(atributos, 'quantity'), str(_valor(atributos, 'quantity', 'unit')).strip()),
        'local_entrega': local_do_endereco(_valor(atributos, 'ship_to_address', 'address')),
        'texto_item': texto_item if _valido(texto_item) else '',
        'texto_compra': texto_compra if _valido(texto_compra) else '',
        'frete': frete if frete.lower() not in VAZIOS else '',
    }


def ids_em_ordem(dados):
    """Ids das linhas na ordem da tela: lotes pela ordem, linhas na ordem de cada lote."""
    lotes = sorted(((dados or {}).get('lots') or {}).values(), key=lambda l: (l or {}).get('order') or 0)
    ids = [str(i) for lote in lotes for i in (lote or {}).get('lines') or []]
    return ids or [str(i) for i in ((dados or {}).get('lines') or {})]


def itens_de_lots(paginas, filtrar_desc=lambda t: t):
    """(itens, tem_anexo) a partir das páginas do lots_with_lines."""
    itens, tem_anexo = [], False
    for dados in paginas:
        linhas = (dados or {}).get('lines') or {}
        for id_linha in ids_em_ordem(dados):
            atributos = (linhas.get(id_linha) or {}).get('attributes')
            if not isinstance(atributos, dict):
                continue
            if _valor(atributos, 'attachments'):
                tem_anexo = True
            itens.append(item_da_linha(atributos, len(itens) + 1, filtrar_desc))
    return itens, tem_anexo


def item_completo(item):
    """O mesmo critério da leitura de tela: local de entrega e algum texto descritivo."""
    local = str(item.get('local_entrega') or '').strip()
    return bool(local) and local != 'N/A' and any(_valido(item.get(c) or '') for c in ('descricao', 'texto_item', 'texto_compra'))


def ler_itens(driver, evento, filtrar_desc=lambda t: t):
    """(itens, tem_anexo) do rascunho de resposta aberto no navegador, ou None se a API não servir.

    Precisa estar na tela de edição da resposta (a URL traz o response_id), no frame do Coupa da Vale."""
    try:
        endereco = str(driver.execute_script("return location.href") or '')
    except Exception:
        return None
    resposta = re.search(r'[?&]response_id=(\d+)', endereco)
    if not endereco.startswith(ORIGEM) or not resposta:
        return None

    paginas = []
    for pagina in range(1, MAX_PAGINAS_ITENS + 1):
        dados = buscar_json(driver, URL_ITENS.format(evento=evento, resposta=resposta.group(1), pagina=pagina), TIMEOUT_ITENS_S)
        if dados is None or not isinstance(dados.get('lines'), dict):
            return None
        paginas.append(dados)
        if not (dados.get('pagination') or {}).get('has_more_pages'):
            break
    else:
        return None   # paginação sem fim: não confia

    itens, tem_anexo = itens_de_lots(paginas, filtrar_desc)
    try:
        esperado = int((paginas[0].get('pagination') or {}).get('rows'))
    except (TypeError, ValueError):
        esperado = len(itens)
    if not itens or len(itens) != esperado or not all(item_completo(i) for i in itens):
        return None
    return itens, tem_anexo


# ---------------------------------------------------------------- intenção e rascunho (sem cliques)
# Os mesmos pedidos que a tela de termos faz (JS quote_external_responce, conferido em 10/11/2026):
#   "Pretendo" + Enviar   -> PATCH set_participation?id=X  (participation=true)
#   Termos (div.s-term)   -> PATCH set_terms_responses     (terms_responses[id][accepted]=true)
#   Inserir resposta      -> POST  /quotes/external_responses (formulário; redireciona para edit?response_id=N)

URL_TERMOS = ORIGEM + "/quotes/external_responses/{evento}/terms"
URL_EDITAR = ORIGEM + "/quotes/external_responses/{evento}/edit?response_id={resposta}"
URL_TERMOS_RESPOSTAS = ORIGEM + "/quotes/external_responses/set_terms_responses"
URL_CRIAR_RESPOSTA = ORIGEM + "/quotes/external_responses"
TIMEOUT_RASCUNHO_S = 40

_CAB_HTML = {'Accept': 'text/html,application/xhtml+xml'}
_FORM = 'application/x-www-form-urlencoded; charset=UTF-8'


def _resposta_existente(html_ou_url, evento):
    m = re.search(rf'/quotes/external_responses/{re.escape(str(evento))}/edit\?response_id=(\d+)', html_ou_url or '')
    return m.group(1) if m else None


def _atributo(tag, nome):
    m = re.search(rf'\b{nome}="([^"]*)"', tag or '')
    return html.unescape(m.group(1)) if m else None


def _input(html_txt, nome):
    """value do <input name=nome> (o atributo pode vir antes ou depois do name)."""
    for tag in re.findall(r'<input\b[^>]*>', html_txt or ''):
        if _atributo(tag, 'name') == nome:
            return _atributo(tag, 'value') or ''
    return None


def ler_tela_termos(html_txt):
    """O que a tela de termos traz para os pedidos: tokens, intenção atual, termos e o formulário da resposta."""
    t = html_txt or ''
    meta = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', t)
    form = None
    for m in re.finditer(r'(<form\b[^>]*>)([\s\S]*?)</form>', t):
        if re.search(r'action="[^"]*/quotes/external_responses"', m.group(1)) and (_atributo(m.group(1), 'method') or '').lower() == 'post':
            form = m.group(2)
            break
    participacao = None
    for tag in re.findall(r'<input\b[^>]*>', t):
        if _atributo(tag, 'id') == 'existing_participation':
            participacao = {'estado': _atributo(tag, 'value') or '', 'url': _atributo(tag, 'data-url') or ''}
            break
    termos = set()
    for tag in re.findall(r'<div\b[^>]*>', t):
        if re.search(r'\bs-term\b', _atributo(tag, 'class') or '') and _atributo(tag, 'data-id'):
            termos.add(_atributo(tag, 'data-id'))
    return {
        'csrf': html.unescape(meta.group(1)) if meta else None,
        'form': form,
        'token_form': _input(form, 'authenticity_token') if form else None,
        'termos_valor': (_input(form, 'terms') or '') if form else '',
        'participacao': participacao,
        'termos': sorted(termos),
        'encerrado': 'já terminou' in t,
    }


def _ok(r):
    return r is not None and 200 <= int(r.get('status') or 0) < 300 and not _sessao_caida(r)


def criar_rascunho(driver, evento, reaproveitar=True):
    """URL de edição do rascunho de resposta do evento, criando-o pelos pedidos da tela (sem cliques).

    Precisa do go_to_csp já aberto (a sessão do Coupa fica presa no evento) e do navegador no frame do Coupa
    da Vale. reaproveitar=False (rodada de negociação) não usa a resposta que já existe.
    None = algo fugiu do esperado: quem chama segue pela tela, como antes."""
    try:
        endereco = str(driver.execute_script("return location.href") or '')
    except Exception:
        return None
    if not endereco.startswith(ORIGEM):
        return None
    existente = _resposta_existente(endereco, evento)
    if existente and reaproveitar:
        return URL_EDITAR.format(evento=evento, resposta=existente)

    r = requisitar(driver, URL_TERMOS.format(evento=evento), TIMEOUT_RASCUNHO_S, cabecalhos=_CAB_HTML)
    if not _ok(r):
        logger.info("    Rascunho pela API: tela de termos não respondeu.")
        return None
    existente = _resposta_existente(r.get('url'), evento) or _resposta_existente(r.get('corpo'), evento)
    if existente and reaproveitar:
        logger.info(f"    Rascunho pela API: resposta {existente} já existia.")
        return URL_EDITAR.format(evento=evento, resposta=existente)

    tela = ler_tela_termos(r.get('corpo'))
    if tela['encerrado'] or not tela['form'] or not tela['token_form'] or not tela['csrf']:
        logger.info("    Rascunho pela API: tela de termos sem o formulário da resposta.")
        return None
    cab = {'X-CSRF-Token': tela['csrf'], 'X-Requested-With': 'XMLHttpRequest', 'Content-Type': _FORM,
           'Accept': 'application/json, text/javascript, */*'}

    # 1. "Pretendo" (o mesmo PATCH do botão Enviar)
    part = tela['participacao']
    if part and part['estado'] != 'intended':
        if not part['url'].startswith(ORIGEM):
            return None
        r = requisitar(driver, part['url'], TIMEOUT_RASCUNHO_S, 'PATCH',
                       urlencode([('decline_reason', ''), ('participation', 'true')]), cab)
        if not _ok(r):
            logger.warning(f"    Rascunho pela API: 'Pretendo' recusado ({r and r.get('status')}).")
            return None
        logger.info("    'Pretendo' enviado pela API.")

    # 2. Termos e condições: aceita todos (a tela marca 'Sim' em cada um)
    if tela['termos']:
        pares = []
        for t in tela['termos']:
            pares += [(f'terms_responses[{t}][accepted]', 'true'), (f'terms_responses[{t}][comment]', '')]
        r = requisitar(driver, URL_TERMOS_RESPOSTAS, TIMEOUT_RASCUNHO_S, 'PATCH', urlencode(pares), cab)
        try:
            aceitos = _ok(r) and json.loads(r.get('corpo') or '{}').get('all_terms_accepted') is True
        except ValueError:
            aceitos = False
        if not aceitos:
            logger.warning("    Rascunho pela API: termos não aceitos.")
            return None
        logger.info(f"    {len(tela['termos'])} termo(s) aceito(s) pela API.")

    # 3. "Inserir resposta": o POST do formulário; o portal redireciona para a edição do rascunho
    corpo = urlencode([('utf8', '✓'), ('authenticity_token', tela['token_form']), ('participation', 'true'),
                       ('quote_supplier[decline_reason]', ''), ('id', str(evento)),
                       ('terms', 'accepted' if tela['termos'] else tela['termos_valor']), ('commit', 'Inserir resposta')])
    r = requisitar(driver, URL_CRIAR_RESPOSTA, TIMEOUT_RASCUNHO_S, 'POST', corpo,
                   {'Content-Type': 'application/x-www-form-urlencoded', **_CAB_HTML})
    nova = _resposta_existente(r.get('url'), evento) if _ok(r) else None
    if not nova:
        logger.warning(f"    Rascunho pela API: 'Inserir resposta' não criou o rascunho ({r and r.get('status')}).")
        return None
    logger.info(f"    Rascunho {nova} criado pela API.")
    return URL_EDITAR.format(evento=evento, resposta=nova)
