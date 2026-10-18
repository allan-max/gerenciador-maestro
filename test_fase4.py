"""Fase 4 (docs/melhorias-bots): piloto que sabe se a tarefa deu certo, um robô por portal, tarefas em fila,
alerta de inatividade, pausa por 'Oops' e nenhuma thread ao importar o gerenciador."""
import ast
import threading
import time as time_real
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from test_fase0 import LOGGER, Relogio, carregar

RAIZ = Path(__file__).parent
GER = RAIZ / 'gerenciador.py'
FUNCOES = {'SioDoRobo', 'comandar', 'esperar_robo_pronto', 'executar_tarefa', 'precisa_alertar_inatividade',
           'checar_inatividade', 'extracao_ok', 'INATIVIDADE_ALERTA_S', 'DURACAO_MAX_TAREFA_S', '_trava_inatividade'}


def gerenciador(**globais):
    padrao = dict(threading=threading, time=Relogio(), sio=SimpleNamespace(connected=True, emit=MagicMock(), on=lambda e: (lambda f: f)),
                  eventos=MagicMock(), enviar_email_erro=MagicMock(), comando_para_robo=MagicMock())
    return carregar(GER, FUNCOES | set(globais.pop('extras', ())), **{**padrao, **globais})


class SioDoRoboTest(unittest.TestCase):
    def test_repassa_tudo_e_guarda_a_ultima_conclusao(self):
        ns = gerenciador()
        real = SimpleNamespace(emit=MagicMock(return_value='ok'), on='handler-on')
        proxy = ns['SioDoRobo'](real)
        proxy.emit('relatar_progresso', {'mensagem': 'x'})
        self.assertIsNone(proxy.ultima_conclusao)
        proxy.emit('tarefa_concluida', {'evento': 'Lote', 'sucesso': False, 'erro': 'Oops'})
        proxy.emit('pedir_otp_usuario')
        self.assertEqual(proxy.ultima_conclusao['erro'], 'Oops')
        self.assertEqual(real.emit.call_count, 3)
        self.assertEqual(proxy.on, 'handler-on')       # sio.on (OTP da Vale) continua funcionando


class ComandarTest(unittest.TestCase):
    def test_conectado_vai_pelo_node_e_desconectado_executa_aqui(self):
        ns = gerenciador()
        with patch.object(LOGGER, 'handle'):
            ns['comandar']('extrair', 'coupa')
            ns['sio'].emit.assert_called_once_with('comando_direto', {'modo': 'extrair', 'portal': 'coupa'})
            ns['comando_para_robo'].assert_not_called()
            ns['sio'].connected = False
            ns['comandar']('extrair', 'vale')
        ns['comando_para_robo'].assert_called_once_with({'modo': 'extrair', 'portal': 'vale'})


class ExecutarTarefaTest(unittest.TestCase):
    def preparar(self, chega=True, conclusao=None):
        ns = gerenciador()
        bot = SimpleNamespace(thread_atual=None)
        bot.sio = ns['SioDoRobo'](SimpleNamespace(emit=lambda *a, **k: None))

        def comandar(modo, portal):
            if not chega:
                return
            th = threading.Thread(target=lambda: bot.sio.emit('tarefa_concluida', conclusao) if conclusao else None)
            bot.thread_atual = th
            th.start()
            th.join()
        ns.update(comandar=comandar, bot_coupa=bot)
        return ns

    def test_tarefa_travada_nao_prende_o_piloto(self):
        ns = gerenciador()
        ns['DURACAO_MAX_TAREFA_S'] = 0.2   # depois do carregar: a definição do módulo sobrescreveria
        bot = SimpleNamespace(thread_atual=None, sio=ns['SioDoRobo'](SimpleNamespace(emit=lambda *a, **k: None)))
        travar = threading.Event()

        def comandar(modo, portal):
            th = threading.Thread(target=travar.wait, daemon=True)
            th.start()
            bot.thread_atual = th
        ns.update(comandar=comandar, bot_coupa=bot)
        with self.assertRaisesRegex(Exception, 'travado'):
            ns['executar_tarefa']('coupa', 'extrair')
        travar.set()

    def test_sucesso_confirmado_pelo_robo(self):
        self.preparar(conclusao={'sucesso': True})['executar_tarefa']('coupa', 'extrair')

    def test_comando_perdido_e_erro_e_nao_ok(self):
        with self.assertRaisesRegex(Exception, 'não chegou ao robô'):
            self.preparar(chega=False)['executar_tarefa']('coupa', 'extrair')

    def test_tarefa_com_erro_ou_sem_confirmacao_e_erro(self):
        with self.assertRaisesRegex(Exception, 'Oops'):
            self.preparar(conclusao={'sucesso': False, 'erro': 'Oops'})['executar_tarefa']('coupa', 'extrair')
        with self.assertRaisesRegex(Exception, 'sem confirmação'):
            self.preparar(conclusao=None)['executar_tarefa']('coupa', 'extrair')

    def test_login_que_falha_nao_espera_os_120s(self):
        ns = gerenciador()
        bot = SimpleNamespace(is_ready=False, sio=SimpleNamespace(ultima_conclusao={'sucesso': False, 'erro': 'captcha'}))
        ns['bot_vale'] = bot
        with self.assertRaisesRegex(Exception, 'captcha'):
            ns['esperar_robo_pronto']('vale')
        self.assertLess(ns['time'].agora - 1000, 5)


class InatividadeTest(unittest.TestCase):
    def test_alerta_uma_vez_e_volta_a_valer_depois_de_uma_extracao(self):
        ns = gerenciador()
        ns.update(ultima_extracao_ok={'coupa': 1000 - 7 * 3600}, inatividade_alertada={'coupa': False})
        with patch.object(LOGGER, 'handle'):
            ns['checar_inatividade']('coupa')
            ns['checar_inatividade']('coupa')
            self.assertEqual(ns['enviar_email_erro'].call_count, 1)
            self.assertEqual(ns['eventos'].registrar.call_args[0][:3], ('coupa', 'robo_inativo', 'erro'))
            ns['extracao_ok']('coupa')
            self.assertFalse(ns['inatividade_alertada']['coupa'])
            ns['checar_inatividade']('coupa')
        self.assertEqual(ns['enviar_email_erro'].call_count, 1)


class ComandoParaRoboTest(unittest.TestCase):
    def ns(self, **globais):
        base = dict(threading=threading, logger=LOGGER, bot_coupa=None, bot_vale=None)
        return gerenciador(extras={'comando_para_robo'}, **{**base, **globais})

    def test_segundo_ligar_nao_cria_outro_navegador(self):
        existente = SimpleNamespace(is_ready=True)
        ns = self.ns(bot_coupa=existente)
        with patch.object(LOGGER, 'handle'):
            ns['comando_para_robo']({'modo': 'ligar_robo', 'portal': 'coupa'})
        self.assertIs(ns['bot_coupa'], existente)
        ns['sio'].emit.assert_any_call('tarefa_concluida', {'evento': 'Login do Robo', 'sucesso': True})

    def test_tarefas_do_mesmo_robo_rodam_uma_por_vez(self):
        ordem, liberar = [], threading.Event()

        def iniciar_tarefa(modo, dados):
            ordem.append(f'início {modo}')
            if modo == 'extrair':
                liberar.wait(5)
            ordem.append(f'fim {modo}')
        bot = SimpleNamespace(iniciar_tarefa=iniciar_tarefa, thread_atual=None)
        ns = self.ns(bot_coupa=bot)
        with patch.object(LOGGER, 'handle'):
            ns['comando_para_robo']({'modo': 'extrair', 'portal': 'coupa'})
            primeira = bot.thread_atual
            ns['comando_para_robo']({'modo': 'responder', 'portal': 'coupa'})
            time_real.sleep(0.2)
            self.assertEqual(ordem, ['início extrair'])        # o responder esperou
            liberar.set()
            bot.thread_atual.join(5)
        self.assertIsNot(bot.thread_atual, primeira)
        self.assertEqual(ordem, ['início extrair', 'fim extrair', 'início responder', 'fim responder'])

    def test_tarefa_na_fila_e_descartada_se_o_robo_foi_desligado(self):
        rodou, liberar = [], threading.Event()
        bot = SimpleNamespace(thread_atual=None, iniciar_tarefa=lambda modo, d: (rodou.append(modo), modo == 'extrair' and liberar.wait(5)))
        ns = self.ns(bot_coupa=bot)
        with patch.object(LOGGER, 'handle'):
            ns['comando_para_robo']({'modo': 'extrair', 'portal': 'coupa'})
            ns['comando_para_robo']({'modo': 'verificar', 'portal': 'coupa'})
            ns['bot_coupa'] = None              # desligado enquanto o verificar esperava
            liberar.set()
            bot.thread_atual.join(5)
        self.assertEqual(rodou, ['extrair'])


class OopsTest(unittest.TestCase):
    def test_pausa_a_cada_3_e_abandona_depois_de_3_pausas(self):
        for caminho, classe in ((RAIZ / 'bots/coupa/mixins/extracao.py', 'ExtracaoMixin'), (RAIZ / 'bots/vale_bot.py', 'ValeScraper')):
            with self.subTest(caminho.name):
                relogio = Relogio()
                const = carregar(caminho, {'OOPS_POR_PAUSA', 'PAUSA_OOPS_S', 'MAX_PAUSAS_OOPS'})
                pausa = carregar(caminho, {'_pausa_por_oops'}, classe, time=relogio,
                                 **{k: const[k] for k in ('OOPS_POR_PAUSA', 'PAUSA_OOPS_S', 'MAX_PAUSAS_OOPS')})['_pausa_por_oops']
                bot = SimpleNamespace(solicitacao_parada=False)
                with patch.object(LOGGER, 'handle'):
                    resultados = [pausa(bot) for _ in range(12)]
                self.assertEqual(resultados, [False] * 11 + [True])
                self.assertAlmostEqual(relogio.agora - 1000, 3 * 600, delta=10)   # 3 pausas de 10 min
                self.assertTrue(bot.solicitacao_parada)
                self.assertIn('ciclo abandonado', bot.motivo_abandono)

    def test_lote_abandonado_nao_e_sucesso(self):
        for arquivo in ('bots/coupa/mixins/extracao.py', 'bots/vale_bot.py'):
            fonte = (RAIZ / arquivo).read_text(encoding='utf-8')
            self.assertIn("'sucesso': not motivo", fonte, arquivo)


class ImportarNaoLigaThreadsTest(unittest.TestCase):
    def test_nenhuma_thread_no_nivel_do_modulo(self):
        soltas = [ast.unparse(n) for n in ast.parse(GER.read_text(encoding='utf-8')).body
                  if isinstance(n, ast.Expr) and 'Thread(' in ast.unparse(n)]
        self.assertEqual(soltas, [])


if __name__ == '__main__':
    unittest.main()
