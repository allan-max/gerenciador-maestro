"""Permissão cotabot_access (Cotabot, robô que cota pela tela Minhas Cotações): login, painel e troca."""
import unittest

from test_emails_acesso import conta, montar

EMAIL = 'ana@venturainformatica.com.br'


class CotabotAcessoTest(unittest.TestCase):
    def login(self, **extra):
        ns, sio, _ = montar({EMAIL: conta(**extra)})
        ns['validar_login']({'user': EMAIL, 'pass': 'certa', 'clientId': 'c1'})
        return sio.ultimo('resultado_login')

    def test_login_devolve_cotabot_access(self):
        self.assertFalse(self.login()['hasCotabotAccess'])
        self.assertTrue(self.login(cotabot_access=True)['hasCotabotAccess'])
        self.assertTrue(self.login(admin=True)['hasCotabotAccess'])

    def test_cotabot_nao_libera_outras_permissoes(self):
        r = self.login(cotabot_access=True)
        for campo in ('hasPortaisAccess', 'hasEmailsAccess', 'hasPlanilhaAccess', 'hasBuscaOutrosAccess', 'hasDashboardAccess'):
            self.assertFalse(r[campo], campo)
        self.assertFalse(self.login(portais_access=True, emails_access=True)['hasCotabotAccess'])

    def test_admin_liga_e_desliga_cotabot(self):
        ns, sio, salvos = montar({EMAIL: conta()})
        for acesso in (True, False):
            ns['admin_alterar_acesso_dashboard']({'email': EMAIL, 'campo': 'cotabot_access', 'acesso': acesso, 'clientId': 'c'})
            resposta = sio.ultimo('admin_resposta_acesso')
            self.assertTrue(resposta['sucesso'])
            self.assertIs(resposta['cotabot_access'], acesso)
            self.assertIs(salvos[-1][EMAIL]['cotabot_access'], acesso)
            self.assertFalse(salvos[-1][EMAIL].get('portais_access', False))

    def test_listagem_inclui_cotabot_access(self):
        ns, sio, _ = montar({EMAIL: conta(cotabot_access=True), 'b@venturainformatica.com.br': conta()})
        ns['admin_listar_usuarios']({'clientId': 'c'})
        usuarios = sio.ultimo('admin_resposta_usuarios')['usuarios']
        self.assertTrue(usuarios[EMAIL]['cotabot_access'])
        self.assertFalse(usuarios['b@venturainformatica.com.br']['cotabot_access'])


if __name__ == '__main__':
    unittest.main()
