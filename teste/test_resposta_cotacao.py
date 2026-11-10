import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from resposta_cotacao import CAMPOS, marcar_respondida


class RespostaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'COTAÇÕES.json'
        self.row = dict(zip(CAMPOS, ('123', 'GUSTAVO', 'Vitória', 'Câmera', '20/10/2026')))
        self.manager = SimpleNamespace(caminho_cotacoes=str(self.path), lock=threading.RLock())
        self.save([self.row, {**self.row, 'VENDEDOR': 'MICHELI'}])

    def save(self, rows):
        self.path.write_text(json.dumps({'COTAÇÃO': rows}), encoding='utf-8')

    def test_atualiza_apenas_linha_identificada_e_preserva_outros_campos(self):
        marcar_respondida(self.manager, self.row)
        rows = json.loads(self.path.read_text(encoding='utf-8'))['COTAÇÃO']
        self.assertEqual(rows[0], {**self.row, 'RESPOSTA': 'RESPONDIDO'})
        self.assertNotIn('RESPOSTA', rows[1])
        self.assertEqual(self.manager.cotacoes['COTAÇÃO'], rows)
        marcar_respondida(self.manager, self.row)  # Repetir não cria outra linha.
        self.assertEqual(len(self.manager.cotacoes['COTAÇÃO']), 2)

    def test_duplicada_ou_ausente_nao_grava(self):
        for rows in ([self.row, self.row], []):
            self.save(rows)
            original = self.path.read_bytes()
            with self.assertRaises(ValueError):
                marcar_respondida(self.manager, self.row)
            self.assertEqual(self.path.read_bytes(), original)

    def test_erro_de_gravacao_preserva_json_e_remove_temporario(self):
        original = self.path.read_bytes()
        with patch('resposta_cotacao.os.replace', side_effect=PermissionError('bloqueado')):
            with self.assertRaises(PermissionError):
                marcar_respondida(self.manager, self.row)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(len(list(self.path.parent.iterdir())), 1)

    def test_dados_invalidos_ou_json_corrompido_nao_sao_substituidos(self):
        with self.assertRaises(ValueError):
            marcar_respondida(self.manager, {})
        self.path.write_text('{incompleto', encoding='utf-8')
        with self.assertRaises(ValueError):
            marcar_respondida(self.manager, self.row)
        self.assertEqual(self.path.read_text(encoding='utf-8'), '{incompleto')


class RespostaUnificadaCoupaValeTest(unittest.TestCase):
    def test_vale_herda_resposta_mixin_do_coupa(self):
        import ast
        raiz = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == 'teste' else Path(__file__).resolve().parent
        arvore_vale = ast.parse((raiz / 'bots/vale_bot.py').read_text(encoding='utf-8'))
        classe_vale = next(n for n in arvore_vale.body if isinstance(n, ast.ClassDef) and n.name == 'ValeScraper')
        bases = [getattr(b, 'id', None) for b in classe_vale.bases]
        self.assertIn('RespostaMixin', bases)
        metodos_proprios = [n.name for n in classe_vale.body if isinstance(n, ast.FunctionDef)]
        self.assertNotIn('responder_evento', metodos_proprios)

    def test_seleciona_vale_primeiro_e_muda_para_vale_base_metals_se_nao_achar(self):
        import os
        import re
        import shutil
        import base64
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.common.exceptions import NoSuchElementException
        from test_fase0 import LOGGER, Relogio, carregar

        raiz = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == 'teste' else Path(__file__).resolve().parent
        ns = carregar(
            raiz / 'bots/coupa/mixins/resposta.py',
            {'RespostaMixin'},
            os=os, re=re, time=Relogio(), tempfile=tempfile, shutil=shutil, base64=base64,
            By=By, Keys=Keys, NoSuchElementException=NoSuchElementException,
            WebDriverWait=lambda d, t: SimpleNamespace(until=lambda cond: cond(d)),
            EC=SimpleNamespace(presence_of_element_located=lambda loc: (lambda d: d.find_element(*loc))),
        )

        historico = []
        eventos_por_cliente = {
            'Vale': {'290001'},
            'Vale Base Metals': {'55002'},
        }

        class ElementoFalso:
            def __init__(self, nome='', texto=''):
                self.nome = nome
                self.texto = texto

            def clear(self):
                pass

            def get_attribute(self, attr):
                return ''

            def send_keys(self, val):
                if self.nome == 'search' and val not in (Keys.RETURN, Keys.ENTER):
                    driver.ultimo_termo = val
                    historico.append(('pesquisar', driver.cliente_atual, val))

            def is_displayed(self):
                return True

        class DriverFalso:
            def __init__(self):
                self.cliente_atual = None
                self.ultimo_termo = None
                self.window_handles = ['principal']
                self.switch_to = SimpleNamespace(
                    window=lambda w: None,
                    frame=lambda f: None,
                    alert=SimpleNamespace(accept=lambda: None),
                )

            def get(self, url):
                historico.append(('get', url))

            def close(self):
                if len(self.window_handles) > 1:
                    self.window_handles.pop()

            def execute_script(self, script, *args):
                el = args[0] if args else None
                if isinstance(el, ElementoFalso) and 'click' in script:
                    if el.nome in ('Vale', 'Vale Base Metals'):
                        self.cliente_atual = el.nome
                        historico.append(('selecionar_cliente', el.nome))
                    elif el.nome == 'link_evento':
                        self.window_handles.append('aba_evento')
                        historico.append(('abrir_evento', el.texto))
                return True

            def find_element(self, by, value):
                if 'customersList_1' in value:
                    return ElementoFalso('customersList_1')
                if 's-toolbarSearch' in value:
                    return ElementoFalso('search')
                if 'coupaTable__cell' in value:
                    achados = eventos_por_cliente.get(self.cliente_atual, set())
                    if self.ultimo_termo in achados:
                        return ElementoFalso('link_evento', self.ultimo_termo)
                    raise NoSuchElementException('não encontrado')
                if value == 'button.s-save':
                    return ElementoFalso('salvar')
                if value == 'button.s-menuSaveResponse':
                    return ElementoFalso('rascunho')
                raise NoSuchElementException(value)

            def find_elements(self, by, value):
                if 'ComboBox__resultItem' in value:
                    if 'VALE BASE METAL' in value:
                        return [ElementoFalso('Vale Base Metals')]
                    return [ElementoFalso('Vale')]
                if 'VENTURA COMERCIO VAREJISTA' in value:
                    return [ElementoFalso('botao_ventura')]
                return []

        driver = DriverFalso()
        emitidos = []
        bot = type('Bot', (ns['RespostaMixin'],), {})()
        bot.BASE_SUPPLIER_URL = 'https://supplier.coupahost.com'
        bot.driver = driver
        bot.wait = SimpleNamespace(until=lambda cond: cond(driver))
        bot.garantir_logado = lambda url: None
        bot._marcar_rascunho = lambda ev: None
        bot.sio = SimpleNamespace(emit=lambda ev, d: emitidos.append((ev, d)))

        with patch.object(LOGGER, 'handle'):
            # 1º da fila: evento 55002 (não está na Vale normal -> muda para Vale Base Metals)
            bot.responder_evento({'evento': '55002', 'precos': [], 'prazos': []})
            # 2º da fila: evento 290001 (entra na página de pesquisa, volta para Vale primeiro e acha lá)
            bot.responder_evento({'evento': '290001', 'precos': [], 'prazos': []})

        self.assertEqual(historico, [
            ('get', 'https://supplier.coupahost.com/quotes/private_events'),
            ('selecionar_cliente', 'Vale'),
            ('pesquisar', 'Vale', '55002'),
            ('selecionar_cliente', 'Vale Base Metals'),
            ('pesquisar', 'Vale Base Metals', '55002'),
            ('abrir_evento', '55002'),
            ('get', 'https://supplier.coupahost.com/quotes/private_events'),
            ('selecionar_cliente', 'Vale'),
            ('pesquisar', 'Vale', '290001'),
            ('abrir_evento', '290001'),
        ])
        self.assertEqual(driver.window_handles, ['principal'])
        self.assertTrue(all(d.get('sucesso') for ev, d in emitidos if ev == 'tarefa_concluida'))

    def test_tira_print_de_cada_item_respondido_e_salvo(self):
        import os
        import re
        import shutil
        import base64
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.common.exceptions import NoSuchElementException
        from test_fase0 import LOGGER, Relogio, carregar

        raiz = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == 'teste' else Path(__file__).resolve().parent
        ns = carregar(
            raiz / 'bots/coupa/mixins/resposta.py',
            {'RespostaMixin'},
            os=os, re=re, time=Relogio(), tempfile=tempfile, shutil=shutil, base64=base64,
            By=By, Keys=Keys, NoSuchElementException=NoSuchElementException,
            ActionChains=lambda d: SimpleNamespace(
                move_to_element=lambda el: SimpleNamespace(
                    click=lambda: SimpleNamespace(
                        pause=lambda t: SimpleNamespace(
                            key_down=lambda k: SimpleNamespace(
                                send_keys=lambda s: SimpleNamespace(
                                    key_up=lambda k2: SimpleNamespace(
                                        send_keys=lambda b: SimpleNamespace(
                                            send_keys=lambda v: SimpleNamespace(
                                                send_keys=lambda tb: SimpleNamespace(perform=lambda: None)
                                            )
                                        )
                                    )
                                )
                            )
                        )
                    )
                )
            ),
            WebDriverWait=lambda d, t: SimpleNamespace(until=lambda cond: cond(d)),
            EC=SimpleNamespace(presence_of_element_located=lambda loc: (lambda d: d.find_element(*loc))),
        )

        ncms_digitados = []
        origens_digitadas = []
        opcoes_origem_clicadas = []
        itens_salvos = []
        buscas_ncm_coupa = []
        scrolls_print = []

        class InputFalso:
            def __init__(self, seletor='', item_idx=None, text=''):
                self.seletor = seletor
                self.item_idx = item_idx
                self.text = text
                self.valor = ''
            def clear(self): self.valor = ''
            def send_keys(self, v):
                if str(v) not in ('', '', '', '', '', 'a'):
                    self.valor = str(v)
                if self.seletor == 'input.s-custom_field_9':
                    ncms_digitados.append(v)
                elif self.seletor == 'input.s-custom_field_1':
                    origens_digitadas.append(v)
            def get_attribute(self, a):
                if a == 'aria-owns' and self.seletor == 'input.s-custom_field_1':
                    return 'custom_field_1_20_results'
                if a == 'value':
                    return self.valor
                return ''
            def is_displayed(self): return True

        class LinhaItemFalsa:
            def __init__(self, idx):
                self.idx = idx
                self.screenshot_as_png = f'PNG_ITEM_{idx}'.encode('utf-8')
                self.inputs = {}   # o mesmo campo devolve o mesmo input (guarda o valor digitado)

            def _input(self, value):
                if value not in self.inputs:
                    self.inputs[value] = InputFalso(value, item_idx=self.idx)
                return self.inputs[value]

            def find_element(self, by, value):
                return self._input(value)

            def find_elements(self, by, value):
                if 's-classification_of_goods' in value:
                    buscas_ncm_coupa.append(value)
                if value in ('input.s-custom_field_9', 'input.s-lead_time_input', 'input.s-custom_field_1', 'button.s-save'):
                    return [self._input(value)]
                return []

        class DriverComItens:
            def __init__(self):
                self.window_handles = ['principal']
                self.switch_to = SimpleNamespace(window=lambda w: None, frame=lambda f: None, default_content=lambda: None)
                self.linhas = [LinhaItemFalsa(1), LinhaItemFalsa(2), LinhaItemFalsa(3)]
                self.ultimo_item_focado = None
                self.opcoes_origem = [
                    InputFalso('li.ComboBox__resultItem', text='0 - Nacional, exceto as indicadas nos códigos 3, 4, 5 e 8'),
                    InputFalso('li.ComboBox__resultItem', text='1 - Estrangeira - Importação direta'),
                    InputFalso('li.ComboBox__resultItem', text='2 - Estrangeira - Adquirida no mercado interno'),
                ]

            def get(self, url): pass
            def close(self):
                if len(self.window_handles) > 1:
                    self.window_handles.pop()
            def execute_script(self, s, *a):
                if 's-lead_time_input' in s and len(a) >= 2 and getattr(a[0], 'idx', None):
                    self.ultimo_item_focado = a[0].idx
                    scrolls_print.append((a[0].idx, getattr(a[1], 'seletor', '')))
                elif 'click()' in s and len(a) >= 1:
                    alvo = a[0]
                    if getattr(alvo, 'seletor', '') == 'li.ComboBox__resultItem':
                        opcoes_origem_clicadas.append(alvo.text)
                    elif getattr(alvo, 'seletor', '') == 'button.s-save' and getattr(alvo, 'item_idx', None):
                        itens_salvos.append(alvo.item_idx)
                return True
            def get_screenshot_as_png(self):
                if self.ultimo_item_focado:
                    return f'PNG_ITEM_{self.ultimo_item_focado}'.encode('utf-8')
                return b'PNG_TELA'
            def find_element(self, by, value):
                if 'coupaTable__cell' in value:
                    return InputFalso(value)
                return InputFalso(value)
            def find_elements(self, by, value):
                if value == 'div.s-itemsAndServicesLine':
                    return self.linhas
                if 'custom_field_1_20_results' in value:
                    return self.opcoes_origem
                if 'VENTURA COMERCIO VAREJISTA' in value:
                    return [InputFalso(value)]
                return []

        emitidos = []
        bot = type('Bot', (ns['RespostaMixin'],), {})()
        bot.BASE_SUPPLIER_URL = 'https://supplier.coupahost.com'
        bot.driver = DriverComItens()
        bot.wait = SimpleNamespace(until=lambda cond: cond(bot.driver))
        bot.garantir_logado = lambda url: None
        bot._marcar_rascunho = lambda ev: None
        bot.sio = SimpleNamespace(emit=lambda ev, d: emitidos.append((ev, d)))

        with patch.object(LOGGER, 'handle'):
            # Responde Item 1 e Item 3, deixando Item 2 em branco
            bot.responder_evento({
                'evento': '800555',
                'precos': ['1450,00', '', '290,00'],
                'prazos': ['15', '', '15'],
                'ncms': ['84137090', '', '84842000'],
                'origens': ['0', '', '2'],
                'icms': ['12', '', '18'],
            })

        self.assertEqual(buscas_ncm_coupa, [], 'Não deve mais copiar o NCM do item no Coupa')
        self.assertIn('8413.70.90', ncms_digitados)
        self.assertIn('8484.20.00', ncms_digitados)
        self.assertEqual(origens_digitadas, ['0', '2'])
        self.assertEqual(
            opcoes_origem_clicadas,
            [
                '0 - Nacional, exceto as indicadas nos códigos 3, 4, 5 e 8',
                '2 - Estrangeira - Adquirida no mercado interno',
            ],
            'Deve selecionar a opção correspondente ao número digitado no campo Origem (s-custom_field_1)'
        )
        self.assertEqual(
            itens_salvos,
            [1, 3],
            'Deve clicar em Salvar (button.s-save) toda vez que terminar de preencher um item'
        )
        self.assertEqual(
            scrolls_print,
            [(1, 'input.s-lead_time_input'), (3, 'input.s-lead_time_input')],
            'Deve rolar para o campo de prazo de entrega / campos preenchidos de cada item respondido antes de tirar a print'
        )

        ev_prints = next((d for ev, d in emitidos if ev == 'prints_resposta_evento'), None)
        self.assertIsNotNone(ev_prints)
        self.assertEqual(ev_prints['evento'], '800555')
        self.assertEqual([p['item'] for p in ev_prints['prints']], [1, 3])
        self.assertEqual(base64.b64decode(ev_prints['prints'][0]['imagem']), b'PNG_ITEM_1')
        self.assertEqual(base64.b64decode(ev_prints['prints'][1]['imagem']), b'PNG_ITEM_3')

    def test_comando_responder_liga_robo_automaticamente_quando_desligado(self):
        import bots.coupa as mod_coupa
        ordem_chamadas = []

        class FakeCoupaBot:
            def __init__(self, sio_inst, ev_clique, coords):
                self.sio = sio_inst
                self.is_ready = False
                self.driver = SimpleNamespace(window_handles=['aba1'])
                self.thread_atual = None
                self.thread_login = None

            def iniciar_e_logar(self):
                ordem_chamadas.append('iniciar_e_logar')
                self.is_ready = True

            def iniciar_tarefa(self, modo, dados):
                ordem_chamadas.append(('iniciar_tarefa', modo, dados.get('evento')))

        import os
        os.environ['MAESTRO_TESTE'] = '1'
        os.environ['MAESTRO_SEM_PILOTO'] = '1'
        import gerenciador

        bot_coupa_orig = getattr(gerenciador, 'bot_coupa', None)
        bot_vale_orig = getattr(gerenciador, 'bot_vale', None)
        try:
            gerenciador.bot_coupa = None
            gerenciador.bot_vale = None
            with patch.object(mod_coupa, 'CoupaScraper', FakeCoupaBot), \
                 patch.object(gerenciador, 'monitorar_log_coupa', lambda: None), \
                 patch.object(gerenciador.sio, 'emit', lambda *a, **k: None):
                gerenciador.comando_para_robo({
                    'modo': 'responder',
                    'portal': 'coupa',
                    'evento': '301023',
                    'precos': ['100,00'],
                    'prazos': ['10']
                })
                # O primeiro 'responder' entra no modo resposta numa thread (para e salva o Coupa/Vale antes)
                import time
                limite = time.time() + 5
                while gerenciador.bot_coupa is None and time.time() < limite:
                    time.sleep(0.02)
                self.assertIsNotNone(gerenciador.bot_coupa)
                while getattr(gerenciador.bot_coupa, 'thread_atual', None) is None and time.time() < limite:
                    time.sleep(0.02)
                if gerenciador.bot_coupa.thread_atual:
                    gerenciador.bot_coupa.thread_atual.join(timeout=5)

            self.assertEqual(ordem_chamadas, ['iniciar_e_logar', ('iniciar_tarefa', 'responder', '301023')])
            self.assertTrue(gerenciador.modo_resposta_ativo)
        finally:
            gerenciador.cancelar_timer_inatividade_resposta()
            gerenciador.modo_resposta_ativo = gerenciador.modo_resposta_pronto = False
            gerenciador.bot_coupa = bot_coupa_orig
            gerenciador.bot_vale = bot_vale_orig

    def test_responder_evento_vale_metais_campos_e_ordem_correta(self):
        import os
        import re
        import shutil
        import base64
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.common.exceptions import NoSuchElementException
        from test_fase0 import LOGGER, Relogio, carregar

        raiz = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == 'teste' else Path(__file__).resolve().parent
        ns = carregar(
            raiz / 'bots/coupa/mixins/resposta.py',
            {'RespostaMixin'},
            os=os, re=re, time=Relogio(), tempfile=tempfile, shutil=shutil, base64=base64,
            By=By, Keys=Keys, NoSuchElementException=NoSuchElementException,
            ActionChains=lambda d: SimpleNamespace(
                move_to_element=lambda el: SimpleNamespace(
                    click=lambda: SimpleNamespace(
                        pause=lambda t: SimpleNamespace(
                            key_down=lambda k: SimpleNamespace(
                                send_keys=lambda s: SimpleNamespace(
                                    key_up=lambda k2: SimpleNamespace(
                                        send_keys=lambda b: SimpleNamespace(
                                            send_keys=lambda v: SimpleNamespace(
                                                send_keys=lambda tb: SimpleNamespace(perform=lambda: el.send_keys(v))
                                            )
                                        )
                                    )
                                )
                            )
                        )
                    )
                )
            ),
            WebDriverWait=lambda d, t: SimpleNamespace(until=lambda cond: cond(d)),
            EC=SimpleNamespace(presence_of_element_located=lambda loc: (lambda d: d.find_element(*loc))),
        )
        ordem_preenchimento = []
        opcoes_clicadas = []

        class InputValeMetais:
            def __init__(self, nome_campo, seletor, cls='', role='', aria_owns='', text=''):
                self.nome_campo = nome_campo
                self.seletor = seletor
                self.cls = cls
                self.role = role
                self.aria_owns = aria_owns
                self.text = text
                self.valor = ''

            def clear(self):
                self.valor = ''

            def send_keys(self, v):
                v_str = str(v)
                if (
                    self.nome_campo in ('NCM', 'ORIGEM', 'COFINS', 'PIS', 'IPI', 'ISS', 'ICMS_ST', 'ICMS', 'PRAZO', 'PRECO')
                    and v_str.strip()
                    and v_str not in ('\ue004', '\ue015', '\ue007', '\ue009', '\ue003', 'a')
                ):
                    self.valor = v_str
                    ordem_preenchimento.append((self.nome_campo, self.seletor, v_str))

            def get_attribute(self, attr):
                if attr == 'class':
                    return self.cls
                if attr == 'role':
                    return self.role
                if attr == 'aria-owns':
                    return self.aria_owns
                if attr == 'value':
                    return self.valor
                return ''

            def is_displayed(self):
                return True

        class LinhaValeMetais:
            def __init__(self, idx):
                self.idx = idx
                self.screenshot_as_png = f'PNG_VM_{idx}'.encode('utf-8')
                self.campos = {
                    'preco_liquido': InputValeMetais('PRECO_LIQUIDO', 'input.s-custom_field_1', cls='s-custom_field_1'),
                    'ncm': InputValeMetais(
                        'NCM',
                        'input.ComboBox__input.s-custom_field_8',
                        cls='ComboBox__input s-custom_field_8',
                        role='combobox',
                        aria_owns='custom_field_8_17_results'
                    ),
                    'origem': InputValeMetais(
                        'ORIGEM',
                        'input.ComboBox__input.s-custom_field_9',
                        cls='ComboBox__input s-custom_field_9',
                        role='combobox',
                        aria_owns='custom_field_9_18_results'
                    ),
                    'cofins': InputValeMetais('COFINS', 'input.s-custom_field_7', cls='s-custom_field_7'),
                    'pis': InputValeMetais('PIS', 'input.s-custom_field_6', cls='s-custom_field_6'),
                    'ipi': InputValeMetais('IPI', 'input.s-custom_field_5', cls='s-custom_field_5'),
                    'iss': InputValeMetais('ISS', 'input.s-custom_field_2', cls='s-custom_field_2'),
                    'icms_st': InputValeMetais('ICMS_ST', 'input.s-custom_field_4', cls='s-custom_field_4'),
                    'icms': InputValeMetais('ICMS', 'input.s-custom_field_3', cls='s-custom_field_3'),
                    'prazo': InputValeMetais('PRAZO', 'input.s-lead_time_input', cls='s-numberInput s-lead_time_input'),
                    'preco': InputValeMetais('PRECO', 'input.s-price_amount_input', cls='s-moneyInput s-price_amount_input'),
                }
                # Simula valor residual em Preço Líquido (s-custom_field_1) que deve ser limpo e nunca preenchido
                self.campos['preco_liquido'].valor = '2'

            def find_element(self, by, value):
                if 's-lead_time_input' in value:
                    return self.campos['prazo']
                if 's-price_amount_input' in value:
                    return self.campos['preco']
                return InputValeMetais('OUTRO', value)

            def find_elements(self, by, value):
                if value == 'input.s-custom_field_1':
                    return [self.campos['preco_liquido']]
                if value in ('input.ComboBox__input.s-custom_field_8', 'input.s-custom_field_8'):
                    return [self.campos['ncm']]
                if value in ('input.ComboBox__input.s-custom_field_9', 'input.s-custom_field_9'):
                    return [self.campos['origem']]
                if value == 'input.s-custom_field_7':
                    return [self.campos['cofins']]
                if value == 'input.s-custom_field_6':
                    return [self.campos['pis']]
                if value == 'input.s-custom_field_5':
                    return [self.campos['ipi']]
                if value == 'input.s-custom_field_2':
                    return [self.campos['iss']]
                if value == 'input.s-custom_field_4':
                    return [self.campos['icms_st']]
                if value == 'input.s-custom_field_3':
                    return [self.campos['icms']]
                if value == 'input.s-lead_time_input':
                    return [self.campos['prazo']]
                if value == 'input.s-price_amount_input':
                    return [self.campos['preco']]
                if value == 'button.s-save':
                    return [InputValeMetais('SALVAR', 'button.s-save')]
                return []

        class DriverValeMetais:
            def __init__(self):
                self.window_handles = ['aba1']
                self.switch_to = SimpleNamespace(
                    window=lambda h: None,
                    frame=lambda f: None,
                    default_content=lambda: None,
                    alert=SimpleNamespace(accept=lambda: None),
                )
                self.linhas = [LinhaValeMetais(1)]
                self.opcoes_ncm = [
                    InputValeMetais('OPCAO_NCM', 'li.ComboBox__resultItem', text='84818099 - Outras torneiras e válvulas'),
                ]
                self.opcoes_origem = [
                    InputValeMetais('OPCAO_ORIGEM', 'li.ComboBox__resultItem', text='0 - Nacional, exceto as indicadas nos códigos 3, 4, 5 e 8'),
                    InputValeMetais('OPCAO_ORIGEM', 'li.ComboBox__resultItem', text='1 - Estrangeira - Importação direta'),
                ]

            def get(self, url): pass
            def close(self): pass
            def execute_script(self, s, *a):
                if 'click()' in s and len(a) >= 1:
                    alvo = a[0]
                    if getattr(alvo, 'seletor', '') == 'li.ComboBox__resultItem':
                        opcoes_clicadas.append(alvo.text)
                return True
            def get_screenshot_as_png(self):
                return b'PNG_VM_1'
            def find_element(self, by, value):
                return InputValeMetais('EL', value)
            def find_elements(self, by, value):
                if value == 'div.s-itemsAndServicesLine':
                    return self.linhas
                if 'custom_field_8_17_results' in value:
                    return self.opcoes_ncm
                if 'custom_field_9_18_results' in value:
                    return self.opcoes_origem
                if 'VENTURA COMERCIO VAREJISTA' in value:
                    return [InputValeMetais('BTN', value)]
                return []

        bot = type('Bot', (ns['RespostaMixin'],), {})()
        bot.BASE_SUPPLIER_URL = 'https://supplier.coupahost.com'
        bot.driver = DriverValeMetais()
        bot.wait = SimpleNamespace(until=lambda cond: cond(bot.driver))
        bot.garantir_logado = lambda url: None
        bot._marcar_rascunho = lambda ev: None
        bot.sio = SimpleNamespace(emit=lambda ev, d: None)

        with patch.object(LOGGER, 'handle'):
            bot.responder_evento({
                'evento': '831667',
                'precos': ['250,00'],
                'prazos': ['12'],
                'ncms': ['84818099'],
                'origens': ['0'],
                'icms': ['18'],
            })

        self.assertEqual(
            ordem_preenchimento,
            [
                ('NCM', 'input.ComboBox__input.s-custom_field_8', '8481.80.99'),
                ('ORIGEM', 'input.ComboBox__input.s-custom_field_9', '0'),
                ('COFINS', 'input.s-custom_field_7', '3'),
                ('PIS', 'input.s-custom_field_6', '0,65'),
                ('IPI', 'input.s-custom_field_5', '0'),
                ('ISS', 'input.s-custom_field_2', '0'),
                ('ICMS_ST', 'input.s-custom_field_4', '0'),
                ('ICMS', 'input.s-custom_field_3', '18'),
                ('PRAZO', 'input.s-lead_time_input', '12'),
                ('PRECO', 'input.s-price_amount_input', '250,00'),
            ],
            'Para o Vale Metais, deve preencher na ordem: NCM -> ORIGEM -> COFINS -> PIS -> IPI -> ISS -> ICMS ST -> ICMS -> PRAZO -> PREÇO'
        )
        self.assertIn('84818099 - Outras torneiras e válvulas', opcoes_clicadas)
        self.assertIn('0 - Nacional, exceto as indicadas nos códigos 3, 4, 5 e 8', opcoes_clicadas)
        self.assertEqual(bot.driver.linhas[0].campos['preco_liquido'].valor, '')

        # Valida também que _inspecionar_campos_linha reconhece todos os 10 campos do Vale Metais preenchidos
        inspecao = bot._inspecionar_campos_linha(
            bot.driver.linhas[0],
            preco_alvo='250,00',
            prazo_alvo='12',
            ncm_alvo='84818099',
            origem_alvo='0',
            icms_alvo='18'
        )
        self.assertEqual(
            list(inspecao.keys()),
            ['NCM', 'Origem', 'COFINS (%)', 'PIS (%)', 'IPI (%)', 'ISS (%)', 'ICMS ST (%)', 'ICMS (%)', 'Prazo', 'Preço']
        )
        for nome_c, info_c in inspecao.items():
            self.assertFalse(info_c['vazio'], f'Campo {nome_c} do Vale Metais deveria constar como preenchido')

        # Protocolo de segurança: se o AJAX do Coupa apagar PIS e trocar o COFINS, a conferência refaz os dois
        linha_vm = bot.driver.linhas[0]
        linha_vm.campos['pis'].valor = ''
        linha_vm.campos['cofins'].valor = '9'
        del ordem_preenchimento[:]
        vazios = bot._garantir_campos_item_antes_salvar(
            linha_vm, 0,
            preco_alvo='250,00', prazo_alvo='12', ncm_alvo='84818099', origem_alvo='0', icms_alvo='18'
        )
        self.assertEqual(vazios, [])
        self.assertEqual(linha_vm.campos['pis'].valor, '0,65')
        self.assertEqual(linha_vm.campos['cofins'].valor, '3')
        self.assertEqual(sorted(n for n, _, _ in ordem_preenchimento), ['COFINS', 'PIS'])

    def test_datasheet_por_linha_anexa_arquivo_em_cada_item_via_botao_add_file(self):
        import os
        import re
        import shutil
        import base64
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.common.exceptions import NoSuchElementException
        from test_fase0 import LOGGER, Relogio, carregar

        raiz = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == 'teste' else Path(__file__).resolve().parent
        ns = carregar(
            raiz / 'bots/coupa/mixins/resposta.py',
            {'RespostaMixin'},
            os=os, re=re, time=Relogio(), tempfile=tempfile, shutil=shutil, base64=base64,
            By=By, Keys=Keys, NoSuchElementException=NoSuchElementException,
            ActionChains=lambda d: SimpleNamespace(
                move_to_element=lambda el: SimpleNamespace(
                    click=lambda: SimpleNamespace(
                        pause=lambda t: SimpleNamespace(
                            key_down=lambda k: SimpleNamespace(
                                send_keys=lambda s: SimpleNamespace(
                                    key_up=lambda k2: SimpleNamespace(
                                        send_keys=lambda b: SimpleNamespace(
                                            send_keys=lambda v: SimpleNamespace(
                                                send_keys=lambda tb: SimpleNamespace(perform=lambda: el.send_keys(v))
                                            )
                                        )
                                    )
                                )
                            )
                        )
                    )
                )
            ),
            WebDriverWait=lambda d, t: SimpleNamespace(until=lambda cond: cond(d)),
            EC=SimpleNamespace(presence_of_element_located=lambda loc: (lambda d: d.find_element(*loc))),
        )
        botoes_add_file_clicados = []
        arquivos_por_item_recebidos = {0: [], 1: []}
        arquivos_gerais_ds_recebidos = []
        arquivos_gerais_dav_recebidos = []

        class InputFileMock:
            def __init__(self, destino_lista):
                self.destino_lista = destino_lista
            def send_keys(self, path):
                self.destino_lista.append(os.path.basename(path))
            def is_displayed(self):
                return True

        class ElementoSimples:
            def __init__(self, seletor='', idx_item=None):
                self.seletor = seletor
                self.idx_item = idx_item
                self.value = '10'
            def clear(self): pass
            def send_keys(self, v): pass
            def click(self): pass
            def is_displayed(self): return True
            def get_attribute(self, name): return self.value if name == 'value' else 'false'

        class LinhaItemMock:
            def __init__(self, idx):
                self.idx = idx
                self.btn_add_file = ElementoSimples('button.s-addFile.buttonAsLink', idx_item=idx)
                self.inp_file_item = InputFileMock(arquivos_por_item_recebidos[idx])

            def find_element(self, by, value):
                return ElementoSimples(value, idx_item=self.idx)

            def find_elements(self, by, value):
                if 's-addFile' in value or 'Adicionar anexo de arquivo' in value:
                    return [self.btn_add_file]
                if "input[type='file']" in value:
                    return [self.inp_file_item]
                if value == 'button.s-save':
                    return [ElementoSimples('button.s-save', idx_item=self.idx)]
                if value.startswith('input.s-custom_field_'):
                    return [ElementoSimples(value, idx_item=self.idx)]
                return []

        class DriverMockDatasheetLinha:
            def __init__(self):
                self.window_handles = ['aba1']
                self.switch_to = SimpleNamespace(
                    window=lambda h: None,
                    frame=lambda f: None,
                    default_content=lambda: None,
                    alert=SimpleNamespace(accept=lambda: None),
                )
                self.linhas = [LinhaItemMock(0), LinhaItemMock(1)]
                self.inp_ds_geral = InputFileMock(arquivos_gerais_ds_recebidos)
                self.inp_dav_geral = InputFileMock(arquivos_gerais_dav_recebidos)

            def get(self, url): pass
            def close(self): pass
            def execute_script(self, s, *a):
                if 'click()' in s and len(a) >= 1:
                    alvo = a[0]
                    if getattr(alvo, 'seletor', '') == 'button.s-addFile.buttonAsLink':
                        botoes_add_file_clicados.append(alvo.idx_item)
                if 'return window.__ultimoInputFileClicado' in s:
                    return None
                return True
            def get_screenshot_as_png(self):
                return b'PNG_DS_LINHA'
            def find_element(self, by, value):
                return ElementoSimples(value)
            def find_elements(self, by, value):
                if value == 'div.s-itemsAndServicesLine':
                    return self.linhas
                if value == "input[type='file'][name='attachment[file]']":
                    return [self.inp_ds_geral, self.inp_dav_geral]
                if 'VENTURA COMERCIO VAREJISTA' in value:
                    return [ElementoSimples(value)]
                return []

        bot = type('Bot', (ns['RespostaMixin'],), {})()
        bot.BASE_SUPPLIER_URL = 'https://supplier.coupahost.com'
        bot.driver = DriverMockDatasheetLinha()
        bot.wait = SimpleNamespace(until=lambda cond: cond(bot.driver))
        bot.garantir_logado = lambda url: None
        bot._marcar_rascunho = lambda ev: None
        bot.sio = SimpleNamespace(emit=lambda ev, d: None)

        b64_pdf = base64.b64encode(b'%PDF-1.4 mock').decode('ascii')

        with patch.object(LOGGER, 'handle'):
            bot.responder_evento({
                'evento': '990011',
                'precos': ['100,00', '200,00'],
                'prazos': ['10', '15'],
                'datasheet_por_linha': True,
                'datasheets': [{'name': 'nao_deve_ir_no_geral.pdf', 'data': b64_pdf}],
                'datasheets_por_item': [
                    [{'name': 'datasheet_item_1.pdf', 'data': b64_pdf}],
                    [{'name': 'datasheet_item_2.pdf', 'data': b64_pdf}],
                ],
                'davs': [{'name': 'dav_geral.pdf', 'data': b64_pdf}],
            })

        self.assertEqual(
            botoes_add_file_clicados,
            [0, 1],
            'Deve clicar em button.s-addFile (Adicionar anexo de arquivo) no Item 1 e no Item 2'
        )
        self.assertEqual(arquivos_por_item_recebidos[0], ['datasheet_item_1.pdf'])
        self.assertEqual(arquivos_por_item_recebidos[1], ['datasheet_item_2.pdf'])
        self.assertEqual(
            arquivos_gerais_ds_recebidos,
            [],
            'Quando datasheet_por_linha=True, não deve anexar datasheet no campo geral do evento'
        )
        self.assertEqual(arquivos_gerais_dav_recebidos, ['dav_geral.pdf'])

    def test_motivos_curtos_minimalistas_e_email_vendas7_no_erro_do_evento(self):
        import os
        import re
        import shutil
        import base64
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.common.exceptions import NoSuchElementException
        from test_fase0 import LOGGER, Relogio, carregar

        raiz = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == 'teste' else Path(__file__).resolve().parent
        ns = carregar(
            raiz / 'bots/coupa/mixins/resposta.py',
            {'RespostaMixin'},
            os=os, re=re, time=Relogio(), tempfile=tempfile, shutil=shutil, base64=base64,
            By=By, Keys=Keys, NoSuchElementException=NoSuchElementException,
            ActionChains=lambda d: SimpleNamespace(),
            WebDriverWait=lambda d, t: SimpleNamespace(until=lambda cond: cond(d)),
            EC=SimpleNamespace(presence_of_element_located=lambda loc: (lambda d: d.find_element(*loc))),
        )
        bot = type('Bot', (ns['RespostaMixin'],), {})()

        # 1. NCM digitado mas não existe no portal
        motivos_ncm = bot._gerar_motivos_curtos_item(
            ['NCM'],
            {'NCM': {'valor': '', 'vazio': True, 'obrigatorio': True, 'ncm_inexistente': True}},
            preco_alvo='100,00', prazo_alvo='10', ncm_alvo='9999.99.99', origem_alvo='0',
        )
        self.assertEqual(motivos_ncm, ['NCM não existe'])

        # 2. Faltou a origem e Valor errado
        motivos_origem_valor = bot._gerar_motivos_curtos_item(
            ['Origem', 'Preço por unidade'],
            {
                'Origem': {'valor': '', 'vazio': True, 'obrigatorio': True},
                'Preço por unidade': {'valor': 'abc', 'vazio': False, 'obrigatorio': True, 'invalido_portal': True},
            },
            preco_alvo='abc', prazo_alvo='10', ncm_alvo='8413.70.90', origem_alvo='',
        )
        self.assertIn('Faltou a origem', motivos_origem_valor)
        self.assertIn('Valor errado', motivos_origem_valor)

        # 3. Faltou o NCM (quando o usuário nem informou NCM na planilha)
        motivos_sem_ncm = bot._gerar_motivos_curtos_item(
            ['NCM'],
            {'NCM': {'valor': '', 'vazio': True, 'obrigatorio': True, 'ncm_inexistente': False}},
            preco_alvo='100,00', prazo_alvo='10', ncm_alvo='', origem_alvo='0',
        )
        self.assertEqual(motivos_sem_ncm, ['Faltou o NCM'])

    def test_preencher_prazo_vale_base_metais_lead_time_resiliente(self):
        import os
        import re
        import shutil
        import base64
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.common.exceptions import NoSuchElementException
        from test_fase0 import LOGGER, Relogio, carregar

        raiz = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == 'teste' else Path(__file__).resolve().parent
        ns = carregar(
            raiz / 'bots/coupa/mixins/resposta.py',
            {'RespostaMixin'},
            os=os, re=re, time=Relogio(), tempfile=tempfile, shutil=shutil, base64=base64,
            By=By, Keys=Keys, NoSuchElementException=NoSuchElementException,
            ActionChains=lambda d: SimpleNamespace(
                move_to_element=lambda el: SimpleNamespace(
                    click=lambda: SimpleNamespace(
                        pause=lambda t: SimpleNamespace(
                            key_down=lambda k: SimpleNamespace(
                                send_keys=lambda s: SimpleNamespace(
                                    key_up=lambda k2: SimpleNamespace(
                                        send_keys=lambda b: SimpleNamespace(
                                            send_keys=lambda v: SimpleNamespace(
                                                send_keys=lambda tb: SimpleNamespace(perform=lambda: setattr(el, 'valor', str(v)))
                                            )
                                        )
                                    )
                                )
                            )
                        )
                    )
                )
            ),
            WebDriverWait=lambda d, t: SimpleNamespace(until=lambda cond: cond(d)),
            EC=SimpleNamespace(presence_of_element_located=lambda loc: (lambda d: d.find_element(*loc))),
        )
        bot = type('Bot', (ns['RespostaMixin'],), {})()

        # Normaliza valores vindos do Excel/usuário ("15.0", "15 dias", "20")
        self.assertEqual(bot._normalizar_prazo_dias('15.0'), '15')
        self.assertEqual(bot._normalizar_prazo_dias('15,0'), '15')
        self.assertEqual(bot._normalizar_prazo_dias('15 dias'), '15')
        self.assertEqual(bot._normalizar_prazo_dias('30'), '30')

        # Formata o NCM colocando ponto nos dois últimos pares se ainda não tiver
        self.assertEqual(bot._formatar_ncm('85411019'), '8541.10.19')
        self.assertEqual(bot._formatar_ncm('85234900'), '8523.49.00')
        self.assertEqual(bot._formatar_ncm('8523.49.00'), '8523.49.00')
        self.assertEqual(bot._formatar_ncm('8541.10.19'), '8541.10.19')
        self.assertEqual(bot._formatar_ncm('85411019.0'), '8541.10.19')

        # Simula <input type="text" id="lead_time_831667_8" class="s-numberInput s-lead_time_input" name="lead_time_831667" value="">
        scripts_executados = []

        class InputLeadTimeValeMetais:
            def __init__(self):
                self.id = 'lead_time_831667_8'
                self.name = 'lead_time_831667'
                self.cls = 's-numberInput s-lead_time_input'
                self.valor = ''
                self.chamadas_send_keys = []

            def is_displayed(self):
                return True

            def clear(self):
                self.valor = ''

            def send_keys(self, v):
                s = str(v)
                self.chamadas_send_keys.append(s)
                if s.isdigit():
                    self.valor = s

            def get_attribute(self, attr):
                if attr == 'value':
                    return self.valor
                if attr == 'id':
                    return self.id
                if attr == 'name':
                    return self.name
                if attr == 'class':
                    return self.cls
                return ''

        inp_lt = InputLeadTimeValeMetais()

        class LinhaMock:
            def find_elements(self, by, sel):
                if 's-lead_time_input' in sel or 'lead_time_' in sel:
                    return [inp_lt]
                return []

        bot.driver = SimpleNamespace(
            execute_script=lambda s, *a: scripts_executados.append((s, a)) or (a[0].valor if a and hasattr(a[0], 'valor') else ''),
            find_elements=lambda by, sel: [],
        )

        linha = LinhaMock()
        ok = bot._preencher_prazo_item(linha, '15.0', idx=0)
        self.assertTrue(ok)
        self.assertEqual(inp_lt.valor, '15')
        self.assertIn('15', inp_lt.chamadas_send_keys)
        self.assertTrue(
            any('_valueTracker' in s for s, _ in scripts_executados),
            'Deve sincronizar o _valueTracker do React/Coupa para garantir que o prazo não seja apagado ao salvar'
        )

    def test_anexo_topo_escolhe_campo_pelo_titulo_vale_e_vale_base_metais(self):
        import re
        from selenium.webdriver.common.by import By
        from test_fase0 import LOGGER, Relogio, carregar

        raiz = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == 'teste' else Path(__file__).resolve().parent
        ns = carregar(raiz / 'bots/coupa/mixins/resposta.py', {'RespostaMixin'}, re=re, time=Relogio(), By=By)

        class DriverTitulos:
            def __init__(self, tipos):
                self.tipos = tipos
            def execute_script(self, s, *a):
                return list(self.tipos) if 'data-maestro-top-anexo' in s else None

        bot = type('Bot', (ns['RespostaMixin'],), {})()
        with patch.object(LOGGER, 'handle'):
            # Vale normal: 0 = Proposta Técnica, 1 = Proposta Comercial
            bot.driver = DriverTitulos(['tecnica', 'comercial'])
            self.assertEqual(bot._indice_anexo_topo('tecnica', 0), 0)
            self.assertEqual(bot._indice_anexo_topo('comercial', 1), 1)
            # Vale Base Metais: 0 = Proposta Comercial, 1 = Proposta Técnica
            bot.driver = DriverTitulos(['comercial', 'tecnica'])
            self.assertEqual(bot._indice_anexo_topo('tecnica', 0), 1, 'Datasheet deve ir na Proposta Técnica')
            self.assertEqual(bot._indice_anexo_topo('comercial', 1), 0, 'DAV deve ir na Proposta Comercial')
            # Sem título reconhecido: mantém a posição padrão
            bot.driver = DriverTitulos([None, None])
            self.assertEqual(bot._indice_anexo_topo('tecnica', 0), 0)
            self.assertEqual(bot._indice_anexo_topo('comercial', 1), 1)

    def test_limpa_dav_e_datasheet_no_topo_somente_quando_anexados_e_permite_multiplos_datasheets(self):
        import os
        import re
        import shutil
        import base64
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.common.exceptions import NoSuchElementException
        from test_fase0 import LOGGER, Relogio, carregar

        raiz = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == 'teste' else Path(__file__).resolve().parent
        ns = carregar(
            raiz / 'bots/coupa/mixins/resposta.py',
            {'RespostaMixin'},
            os=os, re=re, time=Relogio(), tempfile=tempfile, shutil=shutil, base64=base64,
            By=By, Keys=Keys, NoSuchElementException=NoSuchElementException,
            ActionChains=lambda d: SimpleNamespace(),
            WebDriverWait=lambda d, t: SimpleNamespace(until=lambda cond: cond(d)),
            EC=SimpleNamespace(presence_of_element_located=lambda loc: (lambda d: d.find_element(*loc))),
        )

        excluidos_topo = []
        arquivos_ds_enviados = []
        arquivos_dav_enviados = []

        class BotaoDeleteMock:
            def __init__(self, campo):
                self.campo = campo

        class InputFileTopoMock:
            def __init__(self, campo, destino_lista):
                self.campo = campo
                self.destino_lista = destino_lista
                self.btn_delete = BotaoDeleteMock(campo)

            def clear(self):
                pass

            def find_elements(self, by, sel):
                if 'delete' in sel or 'Excluir' in sel:
                    return [self.btn_delete]
                return []

            def send_keys(self, path):
                self.destino_lista.append(os.path.basename(str(path)))

        inp_ds = InputFileTopoMock('datasheet', arquivos_ds_enviados)
        inp_dav = InputFileTopoMock('dav', arquivos_dav_enviados)

        bot = type('Bot', (ns['RespostaMixin'],), {})()
        bot.driver = SimpleNamespace(
            switch_to=SimpleNamespace(alert=SimpleNamespace(accept=lambda: None)),
            execute_script=lambda s, *a: (
                excluidos_topo.append(a[0].campo)
                if ('click()' in s and a and hasattr(a[0], 'campo'))
                else None
            ),
            find_elements=lambda by, sel: (
                [inp_ds, inp_dav] if sel == "input[type='file'][name='attachment[file]']" else []
            ),
        )

        # Caso 1: Usuário NÃO anexou DAV no Maestro, mas anexou 2 Datasheets -> limpa só Datasheet e anexa os 2 Datasheets
        bot._anexar_arquivos_topo_campo(0, ['/tmp/ds_parte1.pdf', '/tmp/ds_parte2.pdf'], 'Datasheet')
        bot._anexar_arquivos_topo_campo(1, [], 'DAV')

        self.assertEqual(
            excluidos_topo,
            ['datasheet'],
            'Só deve limpar o campo de Datasheet no topo (pois o usuário não anexou DAV ainda)'
        )
        self.assertEqual(
            arquivos_ds_enviados,
            ['ds_parte1.pdf', 'ds_parte2.pdf'],
            'Deve permitir anexar mais de um Datasheet no campo de Datasheet do topo'
        )
        self.assertEqual(arquivos_dav_enviados, [])

        # Caso 2: Usuário anexou um DAV -> agora limpa o campo de DAV no topo e anexa o novo DAV
        bot._anexar_arquivos_topo_campo(1, ['/tmp/dav_novo.pdf'], 'DAV')
        self.assertEqual(excluidos_topo, ['datasheet', 'dav'])
        self.assertEqual(arquivos_dav_enviados, ['dav_novo.pdf'])

    def test_perfil_vale_normal_nao_confunde_com_vale_metais_mesmo_com_custom_field_5(self):
        import os
        import re
        import shutil
        import base64
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.common.exceptions import NoSuchElementException
        from test_fase0 import LOGGER, Relogio, carregar

        raiz = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == 'teste' else Path(__file__).resolve().parent
        ns = carregar(
            raiz / 'bots/coupa/mixins/resposta.py',
            {'RespostaMixin'},
            os=os, re=re, time=Relogio(), tempfile=tempfile, shutil=shutil, base64=base64,
            By=By, Keys=Keys, NoSuchElementException=NoSuchElementException,
            ActionChains=lambda d: SimpleNamespace(),
            WebDriverWait=lambda d, t: SimpleNamespace(until=lambda cond: cond(d)),
            EC=SimpleNamespace(presence_of_element_located=lambda loc: (lambda d: d.find_element(*loc))),
        )

        preenchidos = {}

        class CampoValeNormal:
            def __init__(self, nome, cls, role='', aria_owns=''):
                self.nome = nome
                self.cls = cls
                self.role = role
                self.aria_owns = aria_owns
                self.valor = ''

            def clear(self):
                self.valor = ''

            def is_displayed(self):
                return True

            def send_keys(self, v):
                s = str(v)
                if s.strip() and s not in ('\ue004', '\ue015', '\ue007', '\ue009', '\ue003', 'a'):
                    self.valor = s
                    preenchidos[self.nome] = s

            def get_attribute(self, attr):
                if attr == 'class':
                    return self.cls
                if attr == 'role':
                    return self.role
                if attr == 'aria-owns':
                    return self.aria_owns
                if attr == 'value':
                    return self.valor
                return ''

        class LinhaValeNormalComCF5:
            def __init__(self):
                self.campos = {
                    'ncm_cf9': CampoValeNormal('ncm_cf9', 'ComboBox__input s-custom_field_9', role='combobox', aria_owns='cf9_res'),
                    'cofins_cf8': CampoValeNormal('cofins_cf8', 's-custom_field_8'),
                    'pis_cf7': CampoValeNormal('pis_cf7', 's-custom_field_7'),
                    'ipi_cf6': CampoValeNormal('ipi_cf6', 's-custom_field_6'),
                    'outro_cf5': CampoValeNormal('outro_cf5', 's-custom_field_5'),
                    'icms_cf4': CampoValeNormal('icms_cf4', 's-custom_field_4'),
                    'iss_cf3': CampoValeNormal('iss_cf3', 's-custom_field_3'),
                    'origem_cf1': CampoValeNormal('origem_cf1', 'ComboBox__input s-custom_field_1', role='combobox', aria_owns='cf1_res'),
                    'prazo': CampoValeNormal('prazo', 's-numberInput s-lead_time_input'),
                    'preco': CampoValeNormal('preco', 's-moneyInput s-price_amount_input'),
                }

            def find_element(self, by, sel):
                if 's-lead_time_input' in sel:
                    return self.campos['prazo']
                if 's-price_amount_input' in sel:
                    return self.campos['preco']
                return CampoValeNormal('outro', sel)

            def find_elements(self, by, sel):
                mapa = {
                    'input.s-custom_field_9': [self.campos['ncm_cf9']],
                    'input.ComboBox__input.s-custom_field_9': [self.campos['ncm_cf9']],
                    'input.s-custom_field_8': [self.campos['cofins_cf8']],
                    'input.ComboBox__input.s-custom_field_8': [],
                    'input.s-custom_field_7': [self.campos['pis_cf7']],
                    'input.s-custom_field_6': [self.campos['ipi_cf6']],
                    'input.s-custom_field_5': [self.campos['outro_cf5']],
                    'input.s-custom_field_4': [self.campos['icms_cf4']],
                    'input.s-custom_field_3': [self.campos['iss_cf3']],
                    'input.s-custom_field_1': [self.campos['origem_cf1']],
                    'input.s-lead_time_input': [self.campos['prazo']],
                    'input.s-price_amount_input': [self.campos['preco']],
                    'button.s-save': [CampoValeNormal('salvar', 'button.s-save')],
                }
                return mapa.get(sel, [])

        linha_vale = LinhaValeNormalComCF5()
        bot = type('Bot', (ns['RespostaMixin'],), {})()
        bot._cliente_evento_atual = 'Vale'
        self.assertFalse(
            bot._eh_linha_vale_metais(linha_vale, 'Vale'),
            'No perfil VALE normal (mesmo existindo s-custom_field_5), _eh_linha_vale_metais deve retornar False'
        )


if __name__ == '__main__':
    unittest.main()
