"""Fase 0 (docs/melhorias-bots): login/captcha, re local, contagem do dashboard, normalização do RESPOSTA,
reabertos em loop, forçar troca e handlers duplicados. Os robôs são lidos pela árvore (ast), sem importar
Selenium/IA nem abrir o log de produção."""
import ast
import json
import logging
import tempfile
import threading
import unicodedata
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from selenium.webdriver.common.by import By

from test_planilha_persistencia import Manager, namespace as ns_manager

RAIZ = Path(__file__).parent
ARQ_COUPA_LOGIN = RAIZ / 'bots/coupa/mixins/autenticacao.py'
ARQ_VALE = RAIZ / 'bots/vale_bot.py'
LOGGER = logging.getLogger('teste-fase0')


def arvore(caminho):
    return ast.parse(caminho.read_text(encoding='utf-8'))


def carregar(caminho, nomes, classe=None, **globais):
    """Executa só as definições pedidas (do módulo ou de dentro de `classe`) num namespace isolado."""
    corpo = arvore(caminho).body
    if classe:
        corpo = next(n for n in corpo if isinstance(n, ast.ClassDef) and n.name == classe).body
    nos = [n for n in corpo if getattr(n, 'name', None) in nomes
           or (isinstance(n, ast.Assign) and any(getattr(t, 'id', None) in nomes for t in n.targets))]
    ns = {'logger': LOGGER, **globais}
    exec(compile(ast.Module(body=nos, type_ignores=[]), str(caminho), 'exec'), ns)
    return ns


class Relogio:
    def __init__(self):
        self.agora = 1000.0

    def time(self):
        return self.agora

    def sleep(self, s):
        self.agora += s


class Iframe:
    size = {'width': 300, 'height': 80}

    def get_attribute(self, nome):
        return {'src': 'https://www.google.com/recaptcha/anchor', 'name': 'a-1'}.get(nome)

    def is_displayed(self):
        return True


class DriverCaptchaEterno:
    """Tela de login com o captcha da caixinha sempre pendente."""
    window_handles = ['principal']
    current_url = 'https://supplier.coupahost.com/sessions/new'

    def __init__(self):
        self.switch_to = SimpleNamespace(default_content=lambda: None, frame=lambda f: None, window=lambda w: None)

    def get(self, url):
        pass

    def find_elements(self, por, valor):
        return [Iframe()] if (por, valor) == (By.TAG_NAME, 'iframe') else []

    def find_element(self, por, valor):
        return SimpleNamespace(get_attribute=lambda nome: 'false')


class DriverQuebrado(DriverCaptchaEterno):
    def find_elements(self, por, valor):
        raise RuntimeError('stale element reference')


class LoginTest(unittest.TestCase):
    def rodar(self, caminho, classe, driver):
        relogio = Relogio()
        ns = carregar(caminho, {'LOGIN_TIMEOUT_S', 'CAPTCHA_TIMEOUT_S', 'LoginTimeout', 'fazer_login_hibrido'}, None,
                      time=relogio, os=__import__('os'), By=By, ActionChains=None)
        metodo = carregar(caminho, {'fazer_login_hibrido'}, classe, **{k: v for k, v in ns.items() if k != '__builtins__'})
        emitidos = []
        bot = SimpleNamespace(driver=driver, LOGIN_URL='https://login', aguardando_clique=threading.Event(), solicitacao_parada=False,
                              coordenadas_clique={}, sio=SimpleNamespace(emit=lambda e, d: emitidos.append(d['mensagem'])))
        with self.assertRaises(ns['LoginTimeout']) as erro, patch.object(LOGGER, 'handle') as log:
            metodo['fazer_login_hibrido'](bot)
        # Novo login no meio do ciclo passa por vários "except" que engolem o erro: a parada encerra o ciclo mesmo assim
        self.assertTrue(bot.solicitacao_parada)
        return str(erro.exception), emitidos, log, relogio.agora - 1000

    def test_captcha_sem_solucao_encerra_em_5_min_nos_dois_robos(self):
        for caminho, classe in ((ARQ_COUPA_LOGIN, 'AutenticacaoMixin'), (ARQ_VALE, 'ValeScraper')):
            with self.subTest(caminho.name):
                motivo, emitidos, _, decorrido = self.rodar(caminho, classe, DriverCaptchaEterno())
                self.assertEqual(motivo, 'CAPTCHA_TIMEOUT')
                self.assertIn('CAPTCHA EXPIRADO', emitidos[-1])
                self.assertLess(decorrido, 310)

    def test_login_travado_desiste_em_10_min_e_loga_o_erro_uma_vez(self):
        for caminho, classe in ((ARQ_COUPA_LOGIN, 'AutenticacaoMixin'), (ARQ_VALE, 'ValeScraper')):
            with self.subTest(caminho.name):
                motivo, _, log, decorrido = self.rodar(caminho, classe, DriverQuebrado())
                self.assertTrue(motivo.startswith('LOGIN_TIMEOUT'))
                self.assertEqual(log.call_count, 1)   # mesmo erro repetido não enche o log
                self.assertLess(decorrido, 610)


class ImportReLocalTest(unittest.TestCase):
    def test_robos_nao_importam_re_dentro_de_funcao(self):
        # "import re" dentro de função torna re local à função inteira: abortou 34 ciclos da Vale
        for caminho in [ARQ_VALE, *sorted((RAIZ / 'bots/coupa/mixins').glob('*.py'))]:
            for func in (n for n in ast.walk(arvore(caminho)) if isinstance(n, ast.FunctionDef)):
                locais = [n for n in ast.walk(func) if isinstance(n, ast.Import) and any(a.name == 're' for a in n.names)]
                self.assertEqual(locais, [], f'{caminho.name}:{func.name}')


class ContagemDashboardTest(unittest.TestCase):
    def test_classifica_resposta_por_valor_exato(self):
        ns = carregar(RAIZ / 'gerenciador.py', {'sem_acento', 'classificar_resposta'}, unicodedata=unicodedata)
        c = ns['classificar_resposta']
        self.assertEqual(c('RESPONDIDO'), 'respondida')
        self.assertEqual(c(' respondida '), 'respondida')
        self.assertEqual(c('NÃO RESPONDIDO'), 'nao_respondida')     # antes contava como respondida
        self.assertEqual(c('NÃƒO RESPONDIDO'), 'nao_respondida')    # acento quebrado que existe na base
        self.assertEqual(c('NAO RESPONDIDO'), 'nao_respondida')
        for pendente in ('', None, 'None', 'EM ANÁLISE'):
            self.assertEqual(c(pendente), '')
        self.assertEqual(ns['sem_acento']('Informações insuficientes para orçar'), 'Informacoes insuficientes para orcar')


class NormalizarRespostasTest(unittest.TestCase):
    def setUp(self):
        self.logs = patch.object(ns_manager['logger'], 'handle')
        self.logs.start()
        self.addCleanup(self.logs.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cota = Path(self.tmp.name) / 'COTAÇÕES.json'
        (Path(self.tmp.name) / 'PEDIDOS.json').write_text(json.dumps({'PEDIDOS': []}), encoding='utf-8')
        self.cota.write_text(json.dumps({'COTAÇÃO': [
            {'COTAÇÃO': '1', 'RESPOSTA': 'NÃƒO RESPONDIDO'}, {'COTAÇÃO': '2', 'RESPOSTA': 'NÃO RESPONDIDO'},
            {'COTAÇÃO': '3', 'RESPOSTA': 'RESPONDIDO'}, {'COTAÇÃO': '4', 'RESPOSTA': None}]}, ensure_ascii=False), encoding='utf-8')
        from scripts import normalizar_respostas
        self.script = normalizar_respostas

    def manager(self):
        m = Manager.__new__(Manager)
        m.lock, m.iniciado = threading.RLock(), False
        m.cotacoes, m.pedidos = {'COTAÇÃO': []}, {'PEDIDOS': []}
        m.caminho_cotacoes = str(self.cota)
        m.caminho_pedidos = str(Path(self.tmp.name) / 'PEDIDOS.json')
        return m

    def respostas(self):
        return [r['RESPOSTA'] for r in json.loads(self.cota.read_text(encoding='utf-8'))['COTAÇÃO']]

    def test_sem_gravar_so_conta(self):
        antes = self.cota.read_bytes()
        self.assertEqual(self.script.executar(self.manager(), gravar=False), 1)
        self.assertEqual(self.cota.read_bytes(), antes)

    def test_grava_com_backup_e_so_muda_a_linha_quebrada(self):
        self.assertEqual(self.script.executar(self.manager(), gravar=True), 1)
        self.assertEqual(self.respostas(), ['NÃO RESPONDIDO', 'NÃO RESPONDIDO', 'RESPONDIDO', None])
        backups = list(Path(self.tmp.name).glob('COTAÇÕES antes-normalizar *.json'))
        self.assertEqual(len(backups), 1)
        self.assertIn('NÃƒO', backups[0].read_text(encoding='utf-8'))
        self.assertEqual(self.script.executar(self.manager(), gravar=True), 0)   # segunda vez: nada a fazer


class ReabertosTest(unittest.TestCase):
    CHAMADA = "res = self.extrair_dados_do_evento(t['url'], t['num']"

    def test_so_o_reaberto_encerrado_guarda_a_data_nova_nos_dois_robos(self):
        # Oops e 0 itens são passageiros e continuam devolvendo SKIPPED (tentam de novo); só "encerrado" é definitivo
        for arquivo, classe in (('bots/coupa/mixins/extracao.py', 'ExtracaoMixin'), ('bots/vale_bot.py', 'ValeScraper')):
            with self.subTest(arquivo):
                metodos = next(n for n in arvore(RAIZ / arquivo).body if isinstance(n, ast.ClassDef) and n.name == classe).body
                extrair = next(n for n in metodos if getattr(n, 'name', '') == 'extrair_dados_do_evento')
                retornos = [ast.literal_eval(n.value) for n in ast.walk(extrair)
                            if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)]
                self.assertEqual(retornos.count('ENCERRADO'), 1)
                self.assertIn('SKIPPED', retornos)
                fonte = (RAIZ / arquivo).read_text(encoding='utf-8')
                depois = fonte[fonte.index(self.CHAMADA):][:900]
                self.assertIn('if res == "ENCERRADO" and t.get(\'re_extrair\'):', depois)
                self.assertIn("self.salvar_evento_jsonl(t['num'], anterior.get('respondido', True), t.get('venc_tabela', ''))", depois)
                self.assertIn('if res and res not in ("SKIPPED", "ENCERRADO"):', depois)   # encerrado não vira dado extraído

    def test_jsonl_com_a_data_nova_nao_e_mais_reaberto(self):
        # O que o guarda acima faz: acrescenta a linha nova e, ao recarregar, a última linha do evento vale
        for caminho, classe in ((RAIZ / 'bots/coupa/mixins/banco_dados.py', 'Banco_dadosMixin'), (ARQ_VALE, 'ValeScraper')):
            with self.subTest(caminho.name), tempfile.TemporaryDirectory() as tmp:
                from datetime import datetime
                m = carregar(caminho, {'salvar_evento_jsonl', 'carregar_banco_dados'}, classe,
                             json=json, os=__import__('os'), datetime=datetime, ambiente=__import__('ambiente'))
                arq = Path(tmp) / 'eventos.jsonl'
                arq.write_text(json.dumps({'titulo': '287782', 'respondido': True, 'vencimento_tabela': '21/10/2026'}) + '\n', encoding='utf-8')
                bot = SimpleNamespace(ARQUIVO_JSONL=str(arq), db_eventos={})
                with patch.object(LOGGER, 'handle'):
                    m['carregar_banco_dados'](bot)
                    m['salvar_evento_jsonl'](bot, '287782', True, '16/10/2026')
                    m['carregar_banco_dados'](bot)
                self.assertEqual(bot.db_eventos['287782']['vencimento_tabela'], '16/10/2026')
                self.assertTrue(bot.db_eventos['287782']['respondido'])


class GerenciadorTest(unittest.TestCase):
    def test_forcar_troca_desliga_o_outro_e_liga_este(self):
        relogio, emitidos = Relogio(), []

        def emit(evento, dados):
            emitidos.append((evento, dados['modo'], dados['portal']))
            if dados['modo'] == 'desligar_robo':
                ns['bot_vale'] = None   # o desligar chega pelo Node e zera o robô

        ns = carregar(RAIZ / 'gerenciador.py', {'forcar_troca'}, time=relogio,
                      sio=SimpleNamespace(emit=emit), bot_vale=object(), bot_coupa=None)
        with patch.object(LOGGER, 'handle'):
            ns['forcar_troca']('coupa')
        self.assertEqual(emitidos, [('comando_direto', 'desligar_robo', 'vale'), ('comando_direto', 'ligar_robo', 'coupa')])
        self.assertLess(relogio.agora - 1000, 5)

    def test_cada_evento_do_socket_tem_um_handler_so(self):
        # Dois handlers para o mesmo evento: o segundo vence calado (admin_alterar_acesso_dashboard ignorava o campo)
        eventos = [ast.literal_eval(d.args[0]) for f in arvore(RAIZ / 'gerenciador.py').body if isinstance(f, ast.FunctionDef)
                   for d in f.decorator_list if isinstance(d, ast.Call) and ast.unparse(d.func) == 'sio.on']
        self.assertEqual(sorted({e for e in eventos if eventos.count(e) > 1}), [])
        self.assertIn('admin_alterar_acesso_dashboard', eventos)


if __name__ == '__main__':
    unittest.main()
