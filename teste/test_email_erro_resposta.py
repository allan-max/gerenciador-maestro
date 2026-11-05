import unittest
from unittest.mock import MagicMock, patch
from bots.relatorio_email import gerar_conteudo_email_erro_resposta, enviar_email_erro_resposta


class TestEmailErroResposta(unittest.TestCase):
    def test_gerar_conteudo_email_erro_resposta_com_print(self):
        assunto, texto, html = gerar_conteudo_email_erro_resposta(
            nome_bot="Coupa Bot",
            evento="301099",
            erro_detalhado="Evento não encontrado no cliente 'Vale' ou 'Vale Base Metals'.",
            etapa="Pesquisa de Evento no Sourcing",
            campos_vazios=None,
            item_num=None,
            itens_sucesso=None,
            total_itens=1,
            tem_print=True
        )

        self.assertIn("Coupa Bot", assunto)
        self.assertIn("301099", assunto)
        self.assertIn("Evento não encontrado", texto)
        self.assertIn("print_erro_evento_301099.png", texto)

        # HTML
        self.assertIn("Falha ao Responder Cotação", html)
        self.assertIn("301099", html)
        self.assertIn("Pesquisa de Evento no Sourcing", html)
        self.assertIn("cid:print_erro_screenshot", html)
        self.assertIn("vendas7@venturainformatica.com.br", html)

    def test_enviar_email_erro_resposta_dispara_para_vendas7_com_anexo_print(self):
        config = {
            'SMTP_USER': 'suporte@venturainformatica.com.br',
            'SMTP_PASSWORD': 'segredo_teste',
            'emails_relatorio': 'outro@teste.com'
        }
        fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRFAKE"

        with patch('bots.relatorio_email._conectar_e_enviar_smtp', return_value=True) as mock_smtp:
            sucesso = enviar_email_erro_resposta(
                nome_bot="Vale Bot",
                evento="991020",
                erro_detalhado="A tela não tem botão de responder e não pediu aceite de termos.",
                etapa="Abertura do Formulário",
                print_bytes=fake_png,
                config=config,
                destinatario_obrigatorio="vendas7@venturainformatica.com.br"
            )

            self.assertTrue(sucesso)
            mock_smtp.assert_called_once()
            remetente, senha, destinatarios, msg, bot, _ = mock_smtp.call_args[0]
            self.assertEqual(remetente, 'suporte@venturainformatica.com.br')
            self.assertIn('vendas7@venturainformatica.com.br', destinatarios)
            self.assertIn('outro@teste.com', destinatarios)

            # Verifica se o anexo foi incluído no MIMEMultipart
            partes = list(msg.walk())
            attachments = [p for p in partes if p.get_content_disposition() == 'attachment']
            self.assertTrue(len(attachments) >= 1)
            self.assertEqual(attachments[0].get_filename(), 'print_erro_evento_991020.png')
            self.assertEqual(attachments[0].get_payload(decode=True), fake_png)

    def test_responder_evento_envia_email_com_print_em_caso_de_excecao(self):
        from bots.coupa.mixins.resposta import RespostaMixin

        class BotTeste(RespostaMixin):
            def __init__(self):
                self.driver = MagicMock()
                self.driver.get_screenshot_as_png.return_value = b"FAKE_PNG_PRINT"
                self.driver.window_handles = ['handle1']
                self.sio = MagicMock()
                self.config = {
                    'SMTP_USER': 'suporte@venturainformatica.com.br',
                    'SMTP_PASSWORD': 'senha',
                    'emails_relatorio': 'vendas7@venturainformatica.com.br'
                }
                self.BASE_SUPPLIER_URL = 'https://portal.teste'

            def _verificar_e_resolver_captcha(self):
                pass

            def garantir_logado(self, url):
                pass

            def _selecionar_cliente_sourcing(self, cliente):
                pass

            def _pesquisar_link_evento(self, evento):
                return None

        bot = BotTeste()
        with patch('bots.relatorio_email.enviar_email_erro_resposta', return_value=True) as mock_envio:
            bot.responder_evento({'evento': '778899', 'precos': ['10.00']})

            mock_envio.assert_called_once()
            kwargs = mock_envio.call_args[1]
            self.assertEqual(kwargs['evento'], '778899')
            self.assertEqual(kwargs['print_bytes'], b"FAKE_PNG_PRINT")
            self.assertIn("A tela não tem botão de responder", kwargs['erro_detalhado'])
            self.assertEqual(kwargs['destinatario_obrigatorio'], 'vendas7@venturainformatica.com.br')


if __name__ == '__main__':
    unittest.main()
