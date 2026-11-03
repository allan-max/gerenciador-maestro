# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch, MagicMock, call
from bots.coupa.mixins.resposta import RespostaMixin
from bots.relatorio_email import gerar_conteudo_email_erro_salvar_item, enviar_email_erro_salvar_item


class DummyRobot(RespostaMixin):
    def __init__(self):
        self.driver = MagicMock()
        self.sio = MagicMock()
        self.config = {
            'SMTP_USER': 'robo@teste.com',
            'SMTP_PASSWORD': 'senha123',
            'emails_relatorio': 'vendedor@teste.com,fiscal@teste.com'
        }


class TestErroSalvarItem(unittest.TestCase):

    def test_gerar_conteudo_email_erro_salvar_item(self):
        """Valida que o e-mail gerado é direto, visual e detalha o erro e os campos vazios."""
        assunto, texto, html = gerar_conteudo_email_erro_salvar_item(
            nome_bot="Coupa Bot",
            evento="301025",
            item_num=1,
            erro_portal="Resolva todos os erros e tente outra vez.",
            campos_vazios=["Preço", "NCM"],
            itens_sucesso=[2, 3],
            total_itens=3
        )

        self.assertIn("Coupa Bot", assunto)
        self.assertIn("301025", assunto)
        self.assertIn("Item 1", assunto)

        # Texto deve conter os detalhes essenciais
        self.assertIn("Resolva todos os erros e tente outra vez", texto)
        self.assertIn("Preço", texto)
        self.assertIn("NCM", texto)
        self.assertIn("Item(ns) [2, 3] foram salvos corretamente", texto)

        # HTML deve conter estrutura visual com badges e sucesso dos demais
        self.assertIn("Resolva todos os erros e tente outra vez", html)
        self.assertIn("#fee2e2", html)
        self.assertIn("Preço", html)
        self.assertIn("NCM", html)
        self.assertIn("Demais itens processados com sucesso", html)
        self.assertIn("Item <strong>1</strong> de 3", html)

    def test_enviar_email_erro_salvar_item_com_sucesso(self):
        """Garante que a função envia o e-mail via SMTP quando credenciais estão presentes."""
        config = {
            'SMTP_USER': 'robo@teste.com',
            'SMTP_PASSWORD': '123',
            'emails_relatorio': 'vendedor@teste.com'
        }
        with patch('bots.relatorio_email._conectar_e_enviar_smtp', return_value=True) as mock_smtp:
            resultado = enviar_email_erro_salvar_item(
                nome_bot="Coupa Bot",
                evento="301025",
                item_num=2,
                erro_portal="Resolva todos os erros e tente outra vez.",
                campos_vazios=["Origem"],
                config=config,
                itens_sucesso=[1]
            )
            self.assertTrue(resultado)
            mock_smtp.assert_called_once()
            args, _ = mock_smtp.call_args
            self.assertEqual(args[0], 'robo@teste.com')
            self.assertEqual(args[2], ['vendedor@teste.com'])

    def test_detectar_erro_salvar_item_positivo(self):
        """Detecta quando o innerbox com a mensagem de erro do Coupa está no DOM."""
        bot = DummyRobot()
        mock_ib = MagicMock()
        mock_ib.text = "Resolva todos os erros e tente outra vez."
        bot.driver.find_elements.side_effect = lambda by, sel: [mock_ib] if sel == "div.innerbox" else []

        self.assertTrue(bot._detectar_erro_salvar_item())

    def test_detectar_erro_salvar_item_negativo(self):
        """Retorna False quando o alerta não está presente."""
        bot = DummyRobot()
        bot.driver.find_elements.return_value = []
        self.assertFalse(bot._detectar_erro_salvar_item())

    def test_inspecionar_campos_linha_identifica_vazios(self):
        """Valida que campos com value vazio são marcados como vazios."""
        bot = DummyRobot()
        linha_mock = MagicMock()

        # Input de preço preenchido
        el_preco = MagicMock()
        el_preco.get_attribute.return_value = "150,00"

        # Input de prazo vazio
        el_prazo = MagicMock()
        el_prazo.get_attribute.return_value = ""

        def find_els(by, sel):
            if "s-price_amount_input" in sel:
                return [el_preco]
            if "s-lead_time_input" in sel:
                return [el_prazo]
            return []

        linha_mock.find_elements.side_effect = find_els

        resultado = bot._inspecionar_campos_linha(
            linha=linha_mock,
            preco_alvo="150,00",
            prazo_alvo="10"
        )

        self.assertIn("Preço", resultado)
        self.assertFalse(resultado["Preço"]["vazio"])

        self.assertIn("Prazo", resultado)
        self.assertTrue(resultado["Prazo"]["vazio"])

    @patch('bots.relatorio_email.enviar_email_erro_salvar_item')
    @patch('eventos.registrar')
    def test_tratar_erro_validacao_item_quando_persiste_erro(self, mock_eventos, mock_email):
        """Se após a nova tentativa o erro persistir, deve disparar e-mail e registrar no evento."""
        bot = DummyRobot()
        linha_mock = MagicMock()
        btn_salvar_mock = MagicMock()

        # Simula que o erro é detectado tanto antes quanto depois do retry
        bot._detectar_erro_salvar_item = MagicMock(return_value=True)

        # Simula inspeção identificando NCM vazio
        bot._inspecionar_campos_linha = MagicMock(return_value={
            'NCM': {'nome': 'NCM', 'vazio': True, 'elemento': MagicMock(), 'valor_alvo': '84713012', 'tipo': 'ncm'},
            'Preço': {'nome': 'Preço', 'vazio': False, 'elemento': MagicMock(), 'valor_alvo': '50', 'tipo': 'preco'}
        })
        bot._repreencher_campos_vazios_linha = MagicMock()
        bot._verificar_e_resolver_captcha = MagicMock()

        sucesso, vazios = bot._tratar_erro_validacao_item(
            linha=linha_mock,
            idx=0,
            evento="301025",
            itens=[{'preco': '50', 'ncm': '84713012'}],
            preco_alvo="50",
            prazo_alvo="5",
            ncm_alvo="84713012",
            origem_alvo="0",
            icms_alvo="",
            btn_salvar_item=btn_salvar_mock,
            itens_sucesso=[]
        )

        self.assertFalse(sucesso)
        self.assertIn("NCM", vazios)

        # Verifica se o repreenchimento foi tentado
        bot._repreencher_campos_vazios_linha.assert_called_once()

        # Verifica se registrou no eventos.py
        mock_eventos.assert_called_once()
        args_ev, kwargs_ev = mock_eventos.call_args
        self.assertEqual(args_ev[1], 'erro_salvar_item')
        self.assertEqual(args_ev[2], 'erro')
        self.assertIn('NCM', args_ev[3])
        self.assertEqual(kwargs_ev.get('ref'), 'Evento 301025')

        # Verifica se enviou o e-mail
        mock_email.assert_called_once()
        _, kwargs_mail = mock_email.call_args
        self.assertEqual(kwargs_mail['evento'], '301025')
        self.assertEqual(kwargs_mail['item_num'], 1)
        self.assertIn('NCM', kwargs_mail['campos_vazios'])

    def test_tratar_erro_validacao_item_quando_sucesso_apos_retry(self):
        """Se após a nova tentativa o erro sumir e campos estiverem preenchidos, deve ter sucesso."""
        bot = DummyRobot()
        linha_mock = MagicMock()
        btn_salvar_mock = MagicMock()

        # Primeira chamada: erro. Segunda chamada (após retry): sem erro!
        bot._detectar_erro_salvar_item = MagicMock(return_value=False)
        bot._inspecionar_campos_linha = MagicMock(return_value={
            'NCM': {'nome': 'NCM', 'vazio': False, 'elemento': MagicMock(), 'valor_alvo': '84713012', 'tipo': 'ncm'}
        })
        bot._repreencher_campos_vazios_linha = MagicMock()
        bot._verificar_e_resolver_captcha = MagicMock()

        sucesso, vazios = bot._tratar_erro_validacao_item(
            linha=linha_mock,
            idx=0,
            evento="301025",
            itens=[{'preco': '50', 'ncm': '84713012'}],
            preco_alvo="50",
            prazo_alvo="5",
            ncm_alvo="84713012",
            origem_alvo="0",
            icms_alvo="",
            btn_salvar_item=btn_salvar_mock,
            itens_sucesso=[]
        )

        self.assertTrue(sucesso)
        self.assertEqual(vazios, [])


if __name__ == '__main__':
    unittest.main()
