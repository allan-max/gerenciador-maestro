# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch, MagicMock
from bots.relatorio_email import gerar_conteudo_relatorio, enviar_email_relatorio


class TestRelatorioEmail(unittest.TestCase):

    def test_sem_problemas_nao_gera_email(self):
        """Quando não há problemas, não deve gerar nem enviar e-mail."""
        stats = {
            'total_cotacoes': 15,
            'sem_descricao': [],
            'faltando_info': [],
            'reabertas_ou_data_mudou': ['1001', '1002'],
            'eventos_sem_itens': [],
            'erros_planilha': [],
            'erros_extracao': [],
            'erro_critico_bot': None,
            'hora_inicio_extracao': 1000.0
        }
        assunto, texto, html = gerar_conteudo_relatorio("Vale Bot", stats)
        self.assertIsNone(assunto)
        self.assertIsNone(texto)
        self.assertIsNone(html)

        # Envio direto deve retornar False e não chamar SMTP
        logger_mock = MagicMock()
        config = {'SMTP_USER': 'robo@teste.com', 'SMTP_PASSWORD': '123', 'emails_relatorio': 'dest@teste.com'}
        with patch('smtplib.SMTP_SSL') as mock_ssl:
            resultado = enviar_email_relatorio("Vale Bot", stats, config, logger_mock)
            self.assertFalse(resultado)
            mock_ssl.assert_not_called()

    def test_stats_vazio_ou_nulo(self):
        """Stats None ou vazio não deve gerar e-mail."""
        assunto, texto, html = gerar_conteudo_relatorio("Coupa Bot", None)
        self.assertIsNone(assunto)
        assunto, texto, html = gerar_conteudo_relatorio("Coupa Bot", {})
        self.assertIsNone(assunto)

    def test_evento_sem_itens_gera_email_direto(self):
        """Cotação sem itens deve gerar e-mail focado no problema e reportar sucesso do resto."""
        stats = {
            'total_cotacoes': 8,
            'sem_descricao': [],
            'faltando_info': [],
            'eventos_sem_itens': ['301025'],
            'erros_planilha': [],
            'erros_extracao': [],
            'erro_critico_bot': None
        }
        assunto, texto, html = gerar_conteudo_relatorio("Coupa Bot", stats)
        self.assertIsNotNone(assunto)
        self.assertIn("1 cotação com problemas detectados", assunto)
        self.assertIn("Coupa Bot", assunto)

        # Deve apontar a cotação com problema
        self.assertIn("301025", texto)
        self.assertIn("301025", html)
        self.assertIn("Cotações sem itens detectados", texto)

        # Deve confirmar o sucesso das demais 7 cotações
        self.assertIn("As demais 7 cotação(ões) foram extraídas e salvas na planilha", texto)
        self.assertIn("As outras <strong>7 cotação(ões)</strong>", html)

        # NÃO deve poluir com categorias vazias ("(0): Nenhuma")
        self.assertNotIn("Falha ao salvar na planilha", texto)
        self.assertNotIn("Itens sem descrição", texto)

    def test_erro_critico_gera_alerta(self):
        """Erro crítico deve ter assunto e destaque apropriados."""
        stats = {
            'total_cotacoes': 0,
            'sem_descricao': [],
            'faltando_info': [],
            'eventos_sem_itens': [],
            'erros_planilha': [],
            'erros_extracao': [],
            'erro_critico_bot': "TimeoutException: Portal Vale indisponível"
        }
        assunto, texto, html = gerar_conteudo_relatorio("Vale Bot", stats)
        self.assertIsNotNone(assunto)
        self.assertIn("Erro Crítico", assunto)
        self.assertIn("Vale Bot", assunto)
        self.assertIn("TimeoutException: Portal Vale indisponível", texto)
        self.assertIn("TimeoutException: Portal Vale indisponível", html)

    def test_multiplas_inconsistencias_e_sucesso_restante(self):
        """Deve detalhar múltiplos tipos de problemas e calcular cotações restantes."""
        stats = {
            'total_cotacoes': 10,
            'sem_descricao': ['2001'],
            'faltando_info': ['2002'],
            'eventos_sem_itens': ['2003'],
            'erros_planilha': [],
            'erros_extracao': ['9999'],
            'erro_critico_bot': None
        }
        assunto, texto, html = gerar_conteudo_relatorio("Vale Bot", stats)
        self.assertIsNotNone(assunto)
        self.assertIn("4 cotações com problemas detectados", assunto)

        # Cotações afetadas
        for num in ['2001', '2002', '2003', '9999']:
            self.assertIn(num, texto)
            self.assertIn(num, html)

        # Sucesso do restante (10 processadas - 3 pós-extração = 7 com sucesso)
        self.assertIn("As demais 7 cotação(ões)", texto)
        self.assertIn("7 cotação(ões)", html)

    @patch('smtplib.SMTP_SSL')
    def test_enviar_email_relatorio_sucesso(self, mock_smtp_ssl):
        """Verifica o disparo SMTP quando há falhas detectadas."""
        server_inst = MagicMock()
        mock_smtp_ssl.return_value = server_inst

        stats = {
            'total_cotacoes': 3,
            'sem_descricao': ['5544'],
            'faltando_info': [],
            'eventos_sem_itens': [],
            'erros_planilha': [],
            'erros_extracao': [],
            'erro_critico_bot': None
        }
        config = {
            'SMTP_USER': 'robo@empresa.com.br',
            'SMTP_PASSWORD': 'segredo_forte',
            'emails_relatorio': 'gestor@empresa.com.br, equipe@empresa.com.br'
        }
        logger_mock = MagicMock()

        resultado = enviar_email_relatorio("Coupa Bot", stats, config, logger_mock)
        self.assertTrue(resultado)

        mock_smtp_ssl.assert_called_once_with('email-ssl.com.br', 465, timeout=15)
        server_inst.login.assert_called_once_with('robo@empresa.com.br', 'segredo_forte')
        server_inst.sendmail.assert_called_once()
        args = server_inst.sendmail.call_args[0]
        self.assertEqual(args[0], 'robo@empresa.com.br')
        self.assertEqual(args[1], ['gestor@empresa.com.br', 'equipe@empresa.com.br'])
        server_inst.quit.assert_called_once()

    @patch('bots.relatorio_email.enviar_email_relatorio')
    def test_integracao_metodo_vale_bot(self, mock_enviar):
        """Garante que o método enviar_relatorio_email do ValeScraper delega corretamente."""
        from bots.vale_bot import ValeScraper
        instancia = ValeScraper.__new__(ValeScraper)
        instancia.relatorio_email_stats = {'eventos_sem_itens': ['123']}
        instancia.config = {'emails_relatorio': 'teste@teste.com'}
        instancia.enviar_relatorio_email()

        mock_enviar.assert_called_once()
        args, kwargs = mock_enviar.call_args
        self.assertEqual(kwargs['nome_bot'], 'Vale Bot')
        self.assertEqual(kwargs['stats'], {'eventos_sem_itens': ['123']})

    @patch('bots.relatorio_email.enviar_email_relatorio')
    def test_integracao_metodo_coupa_bot(self, mock_enviar):
        """Garante que o método enviar_relatorio_email do CoupaScraper delega corretamente."""
        from bots.coupa import CoupaScraper
        instancia = CoupaScraper.__new__(CoupaScraper)
        instancia.relatorio_email_stats = {'eventos_sem_itens': ['456']}
        instancia.config = {'emails_relatorio': 'teste@teste.com'}
        instancia.enviar_relatorio_email()

        mock_enviar.assert_called_once()
        args, kwargs = mock_enviar.call_args
        self.assertEqual(kwargs['nome_bot'], 'Coupa Bot')
        self.assertEqual(kwargs['stats'], {'eventos_sem_itens': ['456']})


if __name__ == '__main__':
    unittest.main()
