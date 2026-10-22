"""Senhas com hash, painel dev desligado e validação do que vem do portal antes de virar comando no servidor."""
import html
import json
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import seguranca
from test_fase0 import carregar

RAIZ = Path(__file__).parent
GERENCIADOR = RAIZ / 'gerenciador.py'


class SioFalso:
    def __init__(self):
        self.emitidos = []

    def on(self, _nome):
        return lambda f: f

    def emit(self, evento, dados=None):
        self.emitidos.append((evento, dados))

    def ultimo(self, evento):
        return [d for e, d in self.emitidos if e == evento][-1]


class SenhaTest(unittest.TestCase):
    def test_hash_confere_so_a_senha_certa_e_e_diferente_a_cada_vez(self):
        h = seguranca.gerar_hash('segredo1')
        self.assertTrue(seguranca.eh_hash(h))
        self.assertNotIn('segredo1', h)
        self.assertNotEqual(h, seguranca.gerar_hash('segredo1'))
        self.assertTrue(seguranca.conferir_senha(h, 'segredo1'))
        self.assertFalse(seguranca.conferir_senha(h, 'segredo2'))

    def test_texto_puro_antigo_ainda_entra_e_vazios_nunca(self):
        self.assertTrue(seguranca.conferir_senha('antiga', 'antiga'))
        self.assertFalse(seguranca.conferir_senha('antiga', 'Antiga'))
        for guardada, digitada in (('', ''), (None, 'x'), ('x', None), ('x', 123), ('pbkdf2_sha256$quebrado', 'x')):
            self.assertFalse(seguranca.conferir_senha(guardada, digitada))

    def test_email_so_do_dominio_e_um_endereco_so(self):
        self.assertTrue(seguranca.email_ventura('ana.silva@venturainformatica.com.br'))
        for ruim in ('ana@fora.com', 'x@fora.com@venturainformatica.com.br', 'a b@venturainformatica.com.br',
                     '@venturainformatica.com.br', 'ana@venturainformatica.com.br.evil.com', None):
            self.assertFalse(seguranca.email_ventura(ruim), ruim)


class EntradaDoPortalTest(unittest.TestCase):
    def test_prioridades_do_ariba_so_numeros(self):
        self.assertEqual(seguranca.prioridades_ariba(' 1  2 3 '), '1 2 3')
        self.assertEqual(seguranca.prioridades_ariba(None), '1')
        for ruim in ('1"\nCreateObject("WScript.Shell").Run "calc', '1 & calc', '1{ENTER}', '9' * 61):
            with self.assertRaises(ValueError):
                seguranca.prioridades_ariba(ruim)

    def test_eventos_do_findes(self):
        self.assertEqual(seguranca.eventos_findes('123/2026, PE-45 ,, a_b.1, PE 045, Licitação 7'),
                         ['123/2026', 'PE-45', 'a_b.1', 'PE 045', 'Licitação 7'])
        self.assertEqual(seguranca.eventos_findes(''), [])
        for ruim in ('1"', '1\nsair', '{ENTER}', '1+2', '%{F4}', '(x)'):
            with self.assertRaises(ValueError):
                seguranca.eventos_findes(ruim)

    def test_texto_vbs_dobra_aspas_e_recusa_quebra_de_linha(self):
        self.assertEqual(seguranca.texto_vbs(r'C:\a "b"'), r'C:\a ""b""')
        with self.assertRaises(ValueError):
            seguranca.texto_vbs('a\r\nb')

    def test_nome_de_impressora(self):
        for bom in ('SAMSUNGCOM', 'HP LaserJet P205X series PCL6 Class Driver', r'\\SERVIDOR2\HP (1)', 'Impressora Térreo #2'):
            self.assertEqual(seguranca.nome_impressora(bom), bom)
        for ruim in ('HP" & calc & "', 'a|b', 'a\nb', 'HP\n', '', None, 'x' * 101):
            with self.assertRaises(ValueError):
                seguranca.nome_impressora(ruim)


def contas(**globais):
    sio = SioFalso()
    salvos = []
    ns = carregar(GERENCIADOR, {'validar_login', 'registrar_usuario', 'processar_nova_senha', '_token_recuperacao_valido'},
                  sio=sio, seguranca=seguranca, time=__import__('time'), json=json, os=os, base64=__import__('base64'),
                  uuid=__import__('uuid'), datetime=__import__('datetime').datetime,
                  salvar_usuarios=lambda u: salvos.append(json.loads(json.dumps(u))), **globais)
    return ns, sio, salvos


class ContasTest(unittest.TestCase):
    def test_login_com_senha_antiga_troca_pelo_hash_e_aceita_email_com_maiuscula(self):
        usuarios = {'ana@venturainformatica.com.br': {'senha': 'antiga', 'verificado': True}}
        ns, sio, salvos = contas(carregar_usuarios=lambda: usuarios)
        ns['validar_login']({'user': ' Ana@VenturaInformatica.com.br ', 'pass': 'antiga', 'clientId': 'c1'})
        self.assertTrue(sio.ultimo('resultado_login')['sucesso'])
        guardada = salvos[-1]['ana@venturainformatica.com.br']['senha']
        self.assertTrue(seguranca.eh_hash(guardada))
        self.assertTrue(seguranca.conferir_senha(guardada, 'antiga'))

    def test_conta_antiga_com_maiuscula_no_banco_ainda_entra(self):
        usuarios = {'Ana@venturainformatica.com.br': {'senha': seguranca.gerar_hash('certa'), 'verificado': True}}
        ns, sio, _ = contas(carregar_usuarios=lambda: usuarios)
        ns['validar_login']({'user': 'ana@venturainformatica.com.br', 'pass': 'certa', 'clientId': 'c1'})
        resposta = sio.ultimo('resultado_login')
        self.assertTrue(resposta['sucesso'])
        self.assertEqual(resposta['user'], 'Ana@venturainformatica.com.br')

    def test_login_errado_nao_grava(self):
        usuarios = {'ana@venturainformatica.com.br': {'senha': seguranca.gerar_hash('certa'), 'verificado': True}}
        ns, sio, salvos = contas(carregar_usuarios=lambda: usuarios)
        for senha in ('errada', None, ''):
            ns['validar_login']({'user': 'ana@venturainformatica.com.br', 'pass': senha, 'clientId': 'c1'})
            self.assertFalse(sio.ultimo('resultado_login')['sucesso'])
        self.assertEqual(salvos, [])

    def test_cadastro_grava_hash_e_recusa_senha_vazia_e_email_estranho(self):
        ns, sio, salvos = contas(carregar_usuarios=lambda: {}, enviar_email_python=lambda *a, **k: True)
        ns['registrar_usuario']({'email': 'x@fora.com@venturainformatica.com.br', 'senha': 'abcdef', 'clientId': 'c'})
        ns['registrar_usuario']({'email': 'novo@venturainformatica.com.br', 'clientId': 'c'})
        self.assertEqual(salvos, [])
        ns['registrar_usuario']({'email': 'Novo@venturainformatica.com.br', 'senha': 'abcdef', 'clientId': 'c'})
        self.assertTrue(sio.ultimo('resposta_cadastro')['sucesso'])
        conta = salvos[-1]['novo@venturainformatica.com.br']
        self.assertTrue(seguranca.conferir_senha(conta['senha'], 'abcdef'))
        self.assertNotIn('abcdef', json.dumps(salvos))

    def test_nova_senha_grava_hash(self):
        usuarios = {'a@venturainformatica.com.br': {'senha': 'velha', 'token_recuperacao': 'tok'}}
        ns, sio, salvos = contas(carregar_usuarios=lambda: usuarios)
        ns['processar_nova_senha']({'token': 'tok', 'senha': 'abc', 'clientId': 'c'})   # curta demais
        self.assertEqual(salvos, [])
        ns['processar_nova_senha']({'token': 'tok', 'senha': 'nova123', 'clientId': 'c'})
        self.assertTrue(sio.ultimo('resultado_nova_senha')['sucesso'])
        self.assertTrue(seguranca.conferir_senha(salvos[-1]['a@venturainformatica.com.br']['senha'], 'nova123'))

    def test_painel_dev_saiu_do_gerenciador(self):
        # dev.html foi substituído pelo dashboard.html: nenhum evento do gerenciador entrega a lista de contas
        texto = GERENCIADOR.read_text(encoding='utf-8')
        for nome in ('pedir_dados_dev_seguro', 'resposta_painel_dev_cifrado', 'DEV_MASTER_TOKEN'):
            self.assertNotIn(nome, texto)

class ComandosTest(unittest.TestCase):
    def comandos(self, **globais):
        sio = SioFalso()
        rodou = []
        ns = carregar(GERENCIADOR, {'comando_para_robo', 'comando_imprimir'}, sio=sio, seguranca=seguranca,
                      eventos=SimpleNamespace(registrar=lambda *a, **k: None),
                      threading=SimpleNamespace(Thread=lambda target, **k: SimpleNamespace(start=lambda: rodou.append(target))),
                      CONFIG_GLOBAL={}, **globais)
        return ns, sio, rodou

    def test_ariba_recusa_prioridade_que_sairia_da_string_do_vbscript(self):
        ns, sio, rodou = self.comandos()
        ns['comando_para_robo']({'portal': 'ariba', 'modo': 'ligar_robo', 'prioridades': '1"\nWshShell.Run "cmd'})
        self.assertEqual(rodou, [])
        self.assertIn('recusado', sio.ultimo('relatar_progresso_ariba')['mensagem'])
        self.assertEqual(sio.ultimo('sincronizar_estado_ariba'), {'status': 'desligado'})

    def test_findes_recusa_evento_com_aspas(self):
        ns, sio, rodou = self.comandos()
        ns['comando_para_robo']({'portal': 'findes', 'modo': 'ligar_robo', 'evento': '1" & x & "'})
        self.assertEqual(rodou, [])
        self.assertIn('recusado', sio.ultimo('relatar_progresso_findes')['mensagem'])

    def test_impressao_recusa_nome_estranho_e_nunca_usa_shell(self):
        chamadas = []
        ns, sio, rodou = self.comandos(subprocess=SimpleNamespace(run=lambda *a, **k: chamadas.append((a, k))),
                                       time=SimpleNamespace(sleep=lambda s: None), glob=SimpleNamespace(glob=lambda p: []),
                                       os=os, logger=__import__('logging').getLogger('t'))
        ns['comando_imprimir']({'portal': 'coupa', 'impressora': 'HP" & calc & "'})
        self.assertEqual(rodou, [])
        self.assertFalse(sio.ultimo('tarefa_concluida')['sucesso'])
        ns['comando_imprimir']({'portal': None, 'impressora': 'HP'})
        self.assertEqual(rodou, [])

        with patch('os.makedirs'):
            ns['comando_imprimir']({'portal': 'coupa', 'impressora': 'HP LaserJet'})
            rodou[0]()
        (args,), kwargs = chamadas[0]
        self.assertEqual(args, ['RUNDLL32', 'PRINTUI.DLL,PrintUIEntry', '/y', '/n', 'HP LaserJet'])
        self.assertNotIn('shell', kwargs)


class Manager:
    def __init__(self, cotacoes):
        self.lock = threading.RLock()
        self.cotacoes = {'COTAÇÃO': cotacoes}
        self.pedidos = {'PEDIDOS': []}
        self.salvos = 0

    def iniciar(self, *a):
        pass

    def salvar(self):
        self.salvos += 1
        return True


class LinhasTest(unittest.TestCase):
    def preparar(self, linhas):
        import sys
        sio = SioFalso()
        manager = Manager(linhas)
        ns = carregar(GERENCIADOR, {'_achar_linha', '_conferir_edicao', 'comando_apagar_linha', 'comando_editar_linha'}, sio=sio)
        modulo = SimpleNamespace(planilha_manager=manager)
        patcher = patch.dict(sys.modules, {'planilha_manager': modulo})
        patcher.start()
        self.addCleanup(patcher.stop)
        return ns, sio, manager

    def test_apagar_inexistente_e_erro_e_devolve_audit_id(self):
        ns, sio, manager = self.preparar([{'COTAÇÃO': '1', 'VENDEDOR': 'A'}])
        ns['comando_apagar_linha']({'tipo': 'cotacoes', 'chave': '999', 'clientId': 'c', 'audit_id': 'aud1'})
        resposta = sio.ultimo('resposta_acao_linha')
        self.assertFalse(resposta['sucesso'])
        self.assertEqual(resposta['audit_id'], 'aud1')
        self.assertEqual(manager.salvos, 0)

    def test_apagar_devolve_a_linha_apagada(self):
        ns, sio, manager = self.preparar([{'COTAÇÃO': '1', 'VENDEDOR': 'A'}, {'COTAÇÃO': '2'}])
        ns['comando_apagar_linha']({'tipo': 'cotacoes', 'chave': ' 1 ', 'clientId': 'c', 'audit_id': 'aud2'})
        resposta = sio.ultimo('resposta_acao_linha')
        self.assertTrue(resposta['sucesso'])
        self.assertEqual((resposta['linha_apagada'], resposta['audit_id']), ({'COTAÇÃO': '1', 'VENDEDOR': 'A'}, 'aud2'))
        self.assertEqual(manager.cotacoes['COTAÇÃO'], [{'COTAÇÃO': '2'}])

    def test_editar_devolve_o_antes_e_recusa_coluna_nova_ou_valor_estruturado(self):
        ns, sio, manager = self.preparar([{'COTAÇÃO': '1', 'VENDEDOR': 'A', 'RESPOSTA': ''}, {'COTAÇÃO': '2', 'MARCAS': 'X'}])
        ns['comando_editar_linha']({'tipo': 'cotacoes', 'chave': '1', 'novos_dados': {'VENDEDOR': 'B', 'MARCAS': 'Y'},
                                    'clientId': 'c', 'audit_id': 'a'})
        resposta = sio.ultimo('resposta_acao_linha')
        self.assertTrue(resposta['sucesso'])
        self.assertEqual(resposta['antes'], {'VENDEDOR': 'A', 'MARCAS': None})   # MARCAS existe em outra linha
        self.assertEqual(manager.cotacoes['COTAÇÃO'][0]['VENDEDOR'], 'B')
        for novos in ({'__proto__': 'x'}, {'VENDEDOR': {'a': 1}}, {'VENDEDOR': ['x']}, {}):
            ns['comando_editar_linha']({'tipo': 'cotacoes', 'chave': '1', 'novos_dados': novos, 'clientId': 'c'})
            self.assertFalse(sio.ultimo('resposta_acao_linha')['sucesso'], novos)
        ns['comando_editar_linha']({'tipo': 'outra', 'chave': '1', 'novos_dados': {'VENDEDOR': 'C'}, 'clientId': 'c'})
        self.assertFalse(sio.ultimo('resposta_acao_linha')['sucesso'])
        self.assertEqual(manager.salvos, 1)


class ContratoComPortalTest(unittest.TestCase):
    def test_respostas_com_auditoria_devolvem_o_audit_id(self):
        # o portal (server.js) fecha o registro da Auditoria com o audit_id que manda junto do comando
        texto = GERENCIADOR.read_text(encoding='utf-8')
        for evento in ('retorno_registro_vendedor_cotacao', 'retorno_registro_cotacao_manual',
                       'resposta_registro_pedido_manual', 'resposta_acao_linha'):
            emissoes = re.findall(r"sio\.emit\('" + evento + r"',(.*?)\}\)", texto, re.S)
            self.assertTrue(emissoes, evento)
            for e in emissoes:
                self.assertIn("'audit_id': audit_id", e, evento)

    def test_mensagem_de_vendedor_escapa_o_que_o_usuario_digitou(self):
        texto = GERENCIADOR.read_text(encoding='utf-8')
        inicio = texto.index('def comando_registrar_vendedor_cotacao')
        trecho = texto[inicio:texto.index('\n@sio.on', inicio)]
        for linha in (l for l in trecho.splitlines() if l.strip().startswith('msg') and '{' in l):
            for expr in re.findall(r'\{([^{}]*vendedor[^{}]*|[^{}]*join[^{}]*)\}', linha):
                self.assertTrue(expr.startswith('html.escape('), linha)


class TravaJsonlTest(unittest.TestCase):
    def test_linha_acrescentada_espera_a_regravacao_terminar(self):
        import ambiente
        with tempfile.TemporaryDirectory() as tmp:
            arq = os.path.join(tmp, 'eventos.jsonl')
            Path(arq).write_text('{"titulo": "1"}\n', encoding='utf-8')
            th = threading.Thread(target=ambiente.acrescentar_jsonl, args=(arq, {'titulo': '2'}))
            with ambiente.TRAVA_JSONL:   # como a limpeza do gerenciador: lê, regrava e troca o arquivo
                linhas = Path(arq).read_text(encoding='utf-8')
                th.start()
                th.join(0.2)
                self.assertTrue(th.is_alive())
                Path(arq).write_text(linhas, encoding='utf-8')
            th.join(5)
            self.assertEqual([json.loads(l)['titulo'] for l in Path(arq).read_text(encoding='utf-8').splitlines()], ['1', '2'])

    def test_robos_e_gerenciador_usam_a_trava_ao_gravar_o_jsonl(self):
        for arquivo in ('bots/vale_bot.py', 'bots/coupa/mixins/planilhas.py', 'bots/coupa/mixins/banco_dados.py'):
            texto = (RAIZ / arquivo).read_text(encoding='utf-8')
            self.assertNotIn("open(self.ARQUIVO_JSONL, 'a'", texto, arquivo)
        self.assertIn('with ambiente.TRAVA_JSONL:', GERENCIADOR.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
