# -*- coding: utf-8 -*-
"""API JSON do Coupa (bots/coupa/api_json.py): lista de eventos e itens do evento lidos sem renderizar tela.
O módulo é carregado pelo arquivo (sem importar o pacote do robô, que puxa Selenium/IA); o navegador é um
objeto falso que devolve o que o fetch devolveria. Os JSON seguem o formato capturado no portal (10/11/2026)."""
import importlib.util
import json
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location('api_json_coupa', RAIZ / 'bots' / 'coupa' / 'api_json.py')
api_json = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api_json)

URL_EDICAO = 'https://vale.coupahost.com/quotes/external_responses/307802/edit?response_id=5045756'


def linha_lista(num, respostas='0', fim='18/11/2026', nome='Lance spot da req. n°246006'):
    return {'id': f'<a href="/quote_supplier_land/{num}/go_to_csp"><span class="dt_open_link">{num}</span></a>',
            'description': nome, 'start_time': '10/11/2026', 'end_time': fim, 'state': 'Produção',
            'event_type': 'RFQ', 'num_responses': respostas, 'id_num': num}


def lista(nums, pagina=1, por_pagina=90, total=271):
    return {'filterCriteria': {'rowsPerPage': por_pagina, 'currentPage': pagina}, 'totalNumRows': total,
            'rows': [linha_lista(n) for n in nums]}


def atributos(**troca):
    a = {
        'description': {'value': '35002686 || APARELHOS ELETRICO AMPLIFICACAO DE SOM'},
        'extended_description': {'value': None},
        'quantity': {'value': '1.0', 'unit': 'PC-peca', 'precision': 3},
        'shipping_term': {'value': '3', 'code': 'EXW'},
        'attachments': {'value': []},
        'ship_to_address': {'value': 10474, 'address': '4220<br/>4220 | Term.Log.São Luis-Cobre:1001  <br/>Av dos Portugueses 100        '
                                                        '<br/>33592510010117<br/>65085-582 São Luís                  MA   <br/>Brasil'},
        'request_line_custom_fields': {
            'custom_field_attributes': [{'field_name': 'item_text', 'col_name': 'custom_field_14'},
                                        {'field_name': 'material_purchase_text', 'col_name': 'custom_field_12'},
                                        {'field_name': 'short_text', 'col_name': 'custom_field_11'}],
            'custom_field_values': {'custom_field_14': '* KIT CAIXA DE SOM JBL COM 2 MICROFONES',
                                    'custom_field_12': 'PT ||  APARELHOS ELETRICOS DE AMPLIFICACAO DE SOM;  *** EN ||  ELECTRIC SOUND'}},
    }
    a.update(troca)
    return a


def lots(linhas, mais_paginas=False, total=None):
    return {'lots': {'0': {'order': 0, 'lines': list(linhas)}},
            'lines': {i: {'attributes': a} for i, a in linhas.items()},
            'pagination': {'page': 1, 'rows': len(linhas) if total is None else total, 'has_more_pages': mais_paginas}}


def filtrar_pt(t):   # o _filter_desc do robô, resumido
    return t[t.index('PT ||'):t.index('***')].strip() if 'PT ||' in t and '***' in t else t


class Navegador:
    """Driver falso: responde ao fetch com a fila de respostas; guarda as URLs pedidas e as páginas abertas."""

    def __init__(self, respostas, url='https://vale.coupahost.com/quote_supplier_land'):
        self.respostas = list(respostas)
        self.current_url = url
        self.pedidos = []
        self.abertas = []

    def set_script_timeout(self, s):
        pass

    def get(self, url):
        self.abertas.append(url)
        self.current_url = url

    def execute_script(self, script):
        return self.current_url

    def execute_async_script(self, script, url, ms):
        self.pedidos.append(url)
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        if isinstance(r, dict) and 'status' in r and 'corpo' in r:
            return r
        return {'status': 200, 'url': url, 'corpo': json.dumps(r)}


class ListaDeEventos(unittest.TestCase):
    def test_linha_vira_tarefa_com_link_absoluto_e_respostas(self):
        l = api_json.linhas_da_lista(lista([307802]))[0]
        self.assertEqual(l['num'], '307802')
        self.assertEqual(l['url'], 'https://vale.coupahost.com/quote_supplier_land/307802/go_to_csp')
        self.assertEqual(l['venc_tabela'], '18/11/2026')   # igual ao texto da tela: não pode disparar "REABERTO"
        self.assertEqual(l['respostas'], 0)

    def test_respostas_ilegivel_e_none_e_nao_zero(self):
        dados = lista([1])
        dados['rows'][0]['num_responses'] = ''
        self.assertIsNone(api_json.linhas_da_lista(dados)[0]['respostas'])

    def test_linha_sem_link_e_ignorada(self):
        dados = lista([1, 2])
        dados['rows'][0]['id'] = '1'
        self.assertEqual([l['num'] for l in api_json.linhas_da_lista(dados)], ['2'])

    def test_proxima_pagina_pelo_total(self):
        self.assertTrue(api_json.tem_proxima(lista(range(90), pagina=2)))
        self.assertFalse(api_json.tem_proxima(lista(range(1), pagina=4)))   # 4 x 90 >= 271

    def test_sem_total_pagina_cheia_pode_ter_proxima(self):
        cheia = lista(range(90)); cheia['totalNumRows'] = None
        parcial = lista(range(10)); parcial['totalNumRows'] = None
        self.assertTrue(api_json.tem_proxima(cheia))
        self.assertFalse(api_json.tem_proxima(parcial))

    def test_ler_pagina_pede_a_pagina_certa(self):
        nav = Navegador([lista([305000], pagina=2)])
        linhas, proxima = api_json.ler_pagina_lista(nav, 2)
        self.assertIn('page=2', nav.pedidos[0])
        self.assertIn('as_json=true', nav.pedidos[0])
        self.assertEqual(linhas[0]['num'], '305000')
        self.assertTrue(proxima)
        self.assertEqual(nav.abertas, [])   # já estava no Coupa da Vale: não navega

    def test_fora_do_coupa_da_vale_abre_pagina_leve_antes(self):
        nav = Navegador([lista([1])], url='https://supplier.coupahost.com/quotes/private_events/1')
        api_json.ler_pagina_lista(nav, 1)
        self.assertEqual(nav.abertas, [api_json.PAGINA_LEVE])

    def test_sessao_expirada_devolve_none(self):
        # 10/11: com a sessão caída o portal responde 200 com um script jQuery "Você precisa fazer logon."
        flash = {'status': 200, 'url': 'x', 'corpo': "jQuery('#flash_container').html('Você precisa fazer logon.')"}
        self.assertIsNone(api_json.ler_pagina_lista(Navegador([flash]), 1))
        self.assertIsNone(api_json.ler_pagina_lista(Navegador([{'status': 401, 'url': 'x', 'corpo': ''}]), 1))
        login = {'status': 200, 'url': 'https://vale.coupahost.com/sessions/supplier_login', 'corpo': '{}'}
        self.assertIsNone(api_json.ler_pagina_lista(Navegador([login]), 1))

    def test_fetch_que_estoura_devolve_none(self):
        self.assertIsNone(api_json.ler_pagina_lista(Navegador([TimeoutError('script timeout')]), 1))

    def test_portal_devolve_outra_pagina_e_fim_da_lista(self):
        self.assertEqual(api_json.ler_pagina_lista(Navegador([lista([1], pagina=3)]), 9), ([], False))


class ItensDoEvento(unittest.TestCase):
    def test_item_no_formato_da_leitura_de_tela(self):
        nav = Navegador([lots({'6919691': atributos()})], url=URL_EDICAO)
        itens, tem_anexo = api_json.ler_itens(nav, '307802', filtrar_pt)
        self.assertIn('quote_request_id=307802', nav.pedidos[0])
        self.assertIn('quote_response_id=5045756', nav.pedidos[0])
        self.assertFalse(tem_anexo)
        self.assertEqual(itens, [{
            'item_num': 1,
            'titulo': '35002686 || APARELHOS ELETRICO AMPLIFICACAO DE SOM',
            'descricao': 'PT ||  APARELHOS ELETRICOS DE AMPLIFICACAO DE SOM;',   # descrição vazia: cai no texto de compra
            'quantidade': '1 PC-peca',
            'local_entrega': 'São Luís MA',
            'texto_item': '* KIT CAIXA DE SOM JBL COM 2 MICROFONES',
            'texto_compra': 'PT ||  APARELHOS ELETRICOS DE AMPLIFICACAO DE SOM;',
            'frete': 'EXW',
        }])

    def test_descricao_propria_tem_prioridade(self):
        item = api_json.item_da_linha(atributos(extended_description={'value': 'NOTEBOOK 14 POL'}), 1)
        self.assertEqual(item['descricao'], 'NOTEBOOK 14 POL')

    def test_quantidade_como_na_tela(self):
        self.assertEqual(api_json.quantidade_tela('1.0', 'PC-peca'), '1 PC-peca')
        self.assertEqual(api_json.quantidade_tela('2.5', 'KG'), '2,5 KG')
        self.assertEqual(api_json.quantidade_tela('1200.000', 'UN'), '1200 UN')
        self.assertEqual(api_json.quantidade_tela(None, 'UN'), '0')

    def test_anexo_na_linha(self):
        nav = Navegador([lots({'1': atributos(attachments={'value': [{'id': 9}]})})], url=URL_EDICAO)
        self.assertTrue(api_json.ler_itens(nav, '307802')[1])

    def test_ordem_dos_lotes_e_paginas(self):
        p1 = {'lots': {'7': {'order': 1, 'lines': ['b']}, '0': {'order': 0, 'lines': ['a']}},
              'lines': {'a': {'attributes': atributos(description={'value': 'A'})},
                        'b': {'attributes': atributos(description={'value': 'B'})}},
              'pagination': {'rows': 3, 'has_more_pages': True}}
        p2 = lots({'c': atributos(description={'value': 'C'})}, total=3)
        nav = Navegador([p1, p2], url=URL_EDICAO)
        itens, _ = api_json.ler_itens(nav, '307802')
        self.assertEqual([(i['item_num'], i['titulo']) for i in itens], [(1, 'A'), (2, 'B'), (3, 'C')])
        self.assertIn('page=2', nav.pedidos[1])

    def test_item_incompleto_devolve_none_para_ler_pela_tela(self):
        sem_textos = atributos(request_line_custom_fields={'custom_field_attributes': [], 'custom_field_values': {}})
        self.assertIsNone(api_json.ler_itens(Navegador([lots({'1': sem_textos})], url=URL_EDICAO), '307802'))
        sem_local = atributos(ship_to_address={'value': None, 'address': None})
        self.assertIsNone(api_json.ler_itens(Navegador([lots({'1': sem_local})], url=URL_EDICAO), '307802'))

    def test_total_diferente_do_lido_devolve_none(self):
        self.assertIsNone(api_json.ler_itens(Navegador([lots({'1': atributos()}, total=4)], url=URL_EDICAO), '307802'))

    def test_sem_response_id_ou_fora_da_origem_nem_chama_a_api(self):
        for url in ('https://vale.coupahost.com/quotes/external_responses/307802/terms',
                    'https://supplier.coupahost.com/quotes/private_events/307802?response_id=1'):
            nav = Navegador([], url=url)
            self.assertIsNone(api_json.ler_itens(nav, '307802'))
            self.assertEqual(nav.pedidos, [])

    def test_sessao_expirada_nos_itens_devolve_none(self):
        self.assertIsNone(api_json.ler_itens(Navegador([{'status': 401, 'url': 'x', 'corpo': ''}], url=URL_EDICAO), '307802'))


if __name__ == '__main__':
    unittest.main()
