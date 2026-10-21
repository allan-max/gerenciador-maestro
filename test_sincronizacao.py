"""Sincronização manual (botões do dashboard do admin): ciclo Coupa/Vale e backup na hora, sem esperar o horário.
O piloto e o manual dividem uma trava: o manual recusa se já tem ciclo rodando; o piloto espera o manual terminar."""
import sys
import tempfile
import threading
import time as time_real
import unittest
from datetime import datetime as datetime_real
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from test_fase0 import LOGGER, carregar

GER = Path(__file__).parent / 'gerenciador.py'
FUNCOES = {'rodar_ciclo', 'loop_automacao', 'comando_sincronizacao', 'comando_sincronizacao_estado', '_ciclo_manual',
           '_backup_manual', '_responder_sincronizacao', 'estado_sincronizacao', 'rotina_backup', 'deve_verificar_vale',
           '_trava_ciclo', 'ciclo_em_andamento', 'CICLOS_MANUAIS', 'ULTIMAS_SINCRONIZACAO', 'ultimo_dia_verificacao_vale',
           'esperar_robo_desligado', '_trava_backup', '_fazer_backup'}


class ThreadNaHora:
    """Roda o alvo no start(): o teste não depende de timing."""
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def relogio_em(hora):
    class Relogio(datetime_real):
        @classmethod
        def now(cls, tz=None):
            return datetime_real(2026, 10, 21, hora, 0)
    return Relogio


def gerenciador(hora=10, **globais):
    padrao = dict(sio=SimpleNamespace(emit=MagicMock(), on=lambda e: (lambda f: f)), eventos=MagicMock(),
                  threading=SimpleNamespace(Lock=threading.Lock, Thread=ThreadNaHora), datetime=relogio_em(hora),
                  comandar=MagicMock(), esperar_robo_pronto=MagicMock(), executar_tarefa=MagicMock(),
                  extracao_ok=MagicMock(), checar_inatividade=MagicMock(), enviar_email_erro=MagicMock(),
                  time=SimpleNamespace(time=lambda: 0, sleep=lambda s: None), CONFIG_GLOBAL={}, os=__import__('os'),
                  shutil=__import__('shutil'))
    return carregar(GER, FUNCOES, **{**padrao, **globais})


def retornos(ns):
    return [c.args[1] for c in ns['sio'].emit.call_args_list if c.args[0] == 'retorno_sincronizacao']


class CicloManualTest(unittest.TestCase):
    def test_roda_o_ciclo_responde_e_solta_a_trava(self):
        ns = gerenciador()
        with patch.object(LOGGER, 'handle'):
            ns['comando_sincronizacao']({'servico': 'ciclo_coupa', 'clientId': 'c1', 'audit_id': 'a1', 'usuario': 'adm@x'})
        modos = [c.args for c in ns['executar_tarefa'].call_args_list]
        self.assertEqual(modos, [('coupa', 'extrair'), ('coupa', 'verificar')])
        ns['comandar'].assert_any_call('ligar_robo', 'coupa')
        ns['comandar'].assert_called_with('desligar_robo', 'coupa')
        r = retornos(ns)[0]
        self.assertTrue(r['sucesso'])
        self.assertEqual((r['servico'], r['clientId'], r['audit_id']), ('ciclo_coupa', 'c1', 'a1'))
        self.assertIn('manual', r['mensagem'])
        self.assertFalse(ns['_trava_ciclo'].locked())
        detalhe = ns['eventos'].registrar.call_args.kwargs['detalhe']
        self.assertEqual(detalhe, {'manual': True, 'por': 'adm@x'})
        ns['enviar_email_erro'].assert_not_called()

    def test_recusa_se_ja_tem_ciclo_rodando(self):
        ns = gerenciador()
        ns['_trava_ciclo'].acquire()
        ns['ciclo_em_andamento'] = 'vale'
        ns['comando_sincronizacao']({'servico': 'ciclo_coupa', 'clientId': 'c1'})
        r = retornos(ns)[0]
        self.assertFalse(r['sucesso'])
        self.assertIn('vale', r['erro'])
        ns['comandar'].assert_not_called()
        self.assertTrue(ns['_trava_ciclo'].locked())     # a trava continua de quem já estava rodando

    def test_recusa_se_um_robo_esta_ligado_pela_tela_mesmo_parado(self):
        ns = gerenciador()
        ns['bot_vale'] = SimpleNamespace(is_ready=True, thread_atual=None)   # logado e sem tarefa: alguém usando
        ns['comando_sincronizacao']({'servico': 'ciclo_coupa'})
        self.assertIn('vale', retornos(ns)[0]['erro'])
        ns['comandar'].assert_not_called()
        self.assertFalse(ns['_trava_ciclo'].locked())

    def test_so_solta_a_trava_depois_do_robo_desligar_de_verdade(self):
        ns = gerenciador()
        ns['bot_coupa'] = object()
        dormidas = []

        def dormir(s):   # o handler do desligar zera o bot_coupa só depois de uns segundos
            dormidas.append(s)
            if len(dormidas) == 3:
                ns['bot_coupa'] = None
        ns['time'] = SimpleNamespace(time=lambda: 0, sleep=dormir)
        with patch.object(LOGGER, 'handle'):
            ns['rodar_ciclo']('coupa', manual=True)
        self.assertEqual(len(dormidas), 3)
        self.assertIsNone(ns['ciclo_em_andamento'])

    def test_falha_ao_criar_a_thread_nao_prende_a_trava(self):
        def quebrar(*a, **k):
            raise RuntimeError("can't start new thread")
        ns = gerenciador(threading=SimpleNamespace(Lock=threading.Lock, Thread=quebrar))
        with self.assertRaises(RuntimeError):
            ns['comando_sincronizacao']({'servico': 'ciclo_coupa'})
        self.assertFalse(ns['_trava_ciclo'].locked())

    def test_servico_desconhecido(self):
        ns = gerenciador()
        ns['comando_sincronizacao']({'servico': 'formatar_disco'})
        self.assertFalse(retornos(ns)[0]['sucesso'])
        ns['comandar'].assert_not_called()

    def test_erro_no_ciclo_desliga_o_robo_e_responde_erro_sem_email(self):
        ns = gerenciador(executar_tarefa=MagicMock(side_effect=Exception('captcha')))
        with patch.object(LOGGER, 'handle'):
            ns['comando_sincronizacao']({'servico': 'ciclo_vale'})
        r = retornos(ns)[0]
        self.assertEqual((r['sucesso'], r['erro']), (False, 'captcha'))
        ns['comandar'].assert_called_with('desligar_robo', 'vale')
        ns['enviar_email_erro'].assert_not_called()
        self.assertFalse(ns['_trava_ciclo'].locked())
        self.assertIsNone(ns['ciclo_em_andamento'])


class VerificacaoValeTest(unittest.TestCase):
    def test_manual_sempre_verifica_e_conta_como_a_do_dia(self):
        ns = gerenciador(hora=8)    # antes das 09:00 o piloto não verificaria
        with patch.object(LOGGER, 'handle'):
            ok, _ = ns['rodar_ciclo']('vale', manual=True)
        self.assertTrue(ok)
        self.assertIn(('vale', 'verificar'), [c.args for c in ns['executar_tarefa'].call_args_list])
        self.assertEqual(ns['ultimo_dia_verificacao_vale'], datetime_real(2026, 10, 21).date())

    def test_piloto_nao_verifica_de_novo_no_mesmo_dia(self):
        ns = gerenciador(hora=15)
        ns['ultimo_dia_verificacao_vale'] = datetime_real(2026, 10, 21).date()
        with patch.object(LOGGER, 'handle'):
            ns['rodar_ciclo']('vale')
        self.assertEqual([c.args for c in ns['executar_tarefa'].call_args_list], [('vale', 'extrair')])

    def test_otp_nao_informado_nao_derruba_o_ciclo_mas_aparece_na_mensagem(self):
        def tarefa(portal, modo):
            if modo == 'verificar':
                raise Exception('OTP não informado')
        ns = gerenciador(executar_tarefa=MagicMock(side_effect=tarefa))
        with patch.object(LOGGER, 'handle'):
            ok, msg = ns['rodar_ciclo']('vale', manual=True)
        self.assertTrue(ok)
        self.assertIn('OTP não informado', msg)

    def test_erro_no_piloto_manda_email(self):
        ns = gerenciador(executar_tarefa=MagicMock(side_effect=Exception('travou')))
        with patch.object(LOGGER, 'handle'):
            ok, _ = ns['rodar_ciclo']('coupa')
        self.assertFalse(ok)
        ns['enviar_email_erro'].assert_called_once()


class PilotoEsperaOManualTest(unittest.TestCase):
    def test_piloto_espera_o_ciclo_manual_terminar(self):
        pausou, nunca = threading.Event(), threading.Event()

        def dormir(s):
            if s == 90 * 60:
                pausou.set()
                nunca.wait()          # para o piloto aqui (thread daemon)

        ns = gerenciador(time=SimpleNamespace(sleep=dormir))
        ns['rodar_ciclo'] = MagicMock(return_value=(True, 'ok'))
        ns['_trava_ciclo'].acquire()                  # "ciclo manual" rodando
        piloto = threading.Thread(target=ns['loop_automacao'], daemon=True)
        with patch.object(LOGGER, 'handle'):
            piloto.start()
            time_real.sleep(0.3)
            ns['rodar_ciclo'].assert_not_called()
            ns['_trava_ciclo'].release()              # manual terminou
            self.assertTrue(pausou.wait(5))
        ns['rodar_ciclo'].assert_called_once_with('coupa')
        self.assertFalse(ns['_trava_ciclo'].locked())   # a pausa de 1h30 é fora da trava


class EstadoTest(unittest.TestCase):
    def test_ultimo_resultado_de_cada_servico_e_o_que_esta_rodando(self):
        ns = gerenciador()
        ns['ciclo_em_andamento'] = 'vale'
        lista = [  # mais novos primeiro
            {'servico': 'vale', 'tipo': 'ciclo', 'nivel': 'info', 'resumo': 'começou'},
            {'servico': 'coupa', 'tipo': 'ciclo', 'nivel': 'erro', 'resumo': 'falhou'},
            {'servico': 'gerenciador', 'tipo': 'backup', 'nivel': 'ok', 'resumo': 'backup novo'},
            {'servico': 'coupa', 'tipo': 'ciclo', 'nivel': 'ok', 'resumo': 'antigo'},
            {'servico': 'gerenciador', 'tipo': 'backup', 'nivel': 'ok', 'resumo': 'backup velho'},
            {'servico': 'coupa', 'tipo': 'cotacao', 'nivel': 'ok', 'resumo': 'outra coisa'},
        ]
        estado = ns['estado_sincronizacao'](lista)
        self.assertEqual(estado['rodando'], ['ciclo_vale'])
        self.assertEqual({k: v['resumo'] for k, v in estado['ultimas'].items()},
                         {'ciclo_coupa': 'falhou', 'backup': 'backup novo'})

    def test_comando_responde_com_os_eventos_mais_novos_primeiro(self):
        ev = MagicMock()
        ev.ler_todos.return_value = [{'servico': 'gerenciador', 'tipo': 'backup', 'nivel': 'ok', 'resumo': 'velho'},
                                     {'servico': 'gerenciador', 'tipo': 'backup', 'nivel': 'erro', 'resumo': 'novo'}]
        ns = gerenciador(eventos=ev)
        ns['comando_sincronizacao_estado']({'clientId': 'c9'})
        evento, dados = ns['sio'].emit.call_args.args
        self.assertEqual((evento, dados['clientId'], dados['sucesso']), ('retorno_sincronizacao_estado', 'c9', True))
        self.assertEqual(dados['estado']['ultimas']['backup']['resumo'], 'novo')


class BackupManualTest(unittest.TestCase):
    def preparar(self, pasta, criar=True):
        cot, ped = Path(pasta) / 'COTACOES.json', Path(pasta) / 'PEDIDOS.json'
        if criar:
            cot.write_text('{}', encoding='utf-8')
            ped.write_text('{}', encoding='utf-8')
        pm = SimpleNamespace(planilha_manager=SimpleNamespace(caminho_cotacoes=str(cot), caminho_pedidos=str(ped)))
        destino = Path(pasta) / 'backups'
        return gerenciador(CONFIG_GLOBAL={'caminho_backups': str(destino)}), pm, destino

    def test_copia_os_dois_registra_e_responde(self):
        with tempfile.TemporaryDirectory() as pasta:
            ns, pm, destino = self.preparar(pasta)
            with patch.dict(sys.modules, {'planilha_manager': pm}), patch.object(LOGGER, 'handle'):
                ns['comando_sincronizacao']({'servico': 'backup', 'clientId': 'c1', 'usuario': 'adm@x'})
            self.assertEqual(len(list(destino.iterdir())), 2)
        r = retornos(ns)[0]
        self.assertTrue(r['sucesso'])
        self.assertIn('PEDIDOS', r['mensagem'])
        servico, tipo, nivel = ns['eventos'].registrar.call_args.args[:3]
        self.assertEqual((servico, tipo, nivel), ('gerenciador', 'backup', 'ok'))
        self.assertEqual(ns['eventos'].registrar.call_args.kwargs['detalhe'], {'manual': True, 'por': 'adm@x'})

    def test_sem_arquivos_e_erro_e_nao_levanta(self):
        with tempfile.TemporaryDirectory() as pasta:
            ns, pm, _ = self.preparar(pasta, criar=False)
            with patch.dict(sys.modules, {'planilha_manager': pm}), patch.object(LOGGER, 'handle'):
                self.assertIsNone(ns['rotina_backup']())          # o agendado nunca derruba o loop
                ns['comando_sincronizacao']({'servico': 'backup'})
        self.assertFalse(retornos(ns)[0]['sucesso'])
        self.assertEqual(ns['eventos'].registrar.call_args.args[2], 'erro')


if __name__ == '__main__':
    unittest.main()
