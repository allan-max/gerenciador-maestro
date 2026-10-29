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
        raiz = Path(__file__).parent
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

        raiz = Path(__file__).parent
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

        raiz = Path(__file__).parent
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
            def clear(self): pass
            def send_keys(self, v):
                if self.seletor == 'input.s-custom_field_9':
                    ncms_digitados.append(v)
                elif self.seletor == 'input.s-custom_field_1':
                    origens_digitadas.append(v)
            def get_attribute(self, a):
                if a == 'aria-owns' and self.seletor == 'input.s-custom_field_1':
                    return 'custom_field_1_20_results'
                return ''
            def is_displayed(self): return True

        class LinhaItemFalsa:
            def __init__(self, idx):
                self.idx = idx
                self.screenshot_as_png = f'PNG_ITEM_{idx}'.encode('utf-8')

            def find_element(self, by, value):
                return InputFalso(value, item_idx=self.idx)

            def find_elements(self, by, value):
                if 's-classification_of_goods' in value:
                    buscas_ncm_coupa.append(value)
                if value in ('input.s-custom_field_9', 'input.s-lead_time_input', 'input.s-custom_field_1', 'button.s-save'):
                    return [InputFalso(value, item_idx=self.idx)]
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
        self.assertIn('84137090', ncms_digitados)
        self.assertIn('84842000', ncms_digitados)
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
                self.assertIsNotNone(gerenciador.bot_coupa)
                if gerenciador.bot_coupa.thread_atual:
                    gerenciador.bot_coupa.thread_atual.join(timeout=5)

            self.assertEqual(ordem_chamadas, ['iniciar_e_logar', ('iniciar_tarefa', 'responder', '301023')])
        finally:
            gerenciador.bot_coupa = bot_coupa_orig
            gerenciador.bot_vale = bot_vale_orig


if __name__ == '__main__':
    unittest.main()
