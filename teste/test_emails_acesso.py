"""Permissão emails_access (Central de E-mails): login, painel de contas e troca de acesso."""
import json
import os
import unittest

import seguranca
from test_fase0 import carregar
from test_seguranca import GERENCIADOR, SioFalso


def montar(usuarios):
    sio = SioFalso()
    salvos = []
    ns = carregar(GERENCIADOR, {'validar_login', 'admin_listar_usuarios', 'admin_alterar_acesso_dashboard',
                                'CAMPOS_PUBLICOS_USUARIO', '_token_recuperacao_valido'},
                  sio=sio, seguranca=seguranca, time=__import__('time'), json=json, os=os,
                  base64=__import__('base64'), uuid=__import__('uuid'), datetime=__import__('datetime').datetime,
                  carregar_usuarios=lambda: usuarios, salvar_usuarios=lambda u: salvos.append(json.loads(json.dumps(u))))
    return ns, sio, salvos


def conta(**extra):
    return {'senha': seguranca.gerar_hash('certa'), 'verificado': True, **extra}


class EmailsAcessoTest(unittest.TestCase):
    def login(self, **extra):
        ns, sio, _ = montar({'ana@venturainformatica.com.br': conta(**extra)})
        ns['validar_login']({'user': 'ana@venturainformatica.com.br', 'pass': 'certa', 'clientId': 'c1'})
        return sio.ultimo('resultado_login')

    def test_login_devolve_emails_access(self):
        self.assertFalse(self.login()['hasEmailsAccess'])
        self.assertTrue(self.login(emails_access=True)['hasEmailsAccess'])
        self.assertTrue(self.login(admin=True)['hasEmailsAccess'])

    def test_emails_access_nao_libera_portais_e_vice_versa(self):
        r = self.login(emails_access=True)
        self.assertFalse(r['hasPortaisAccess'])
        self.assertFalse(self.login(portais_access=True)['hasEmailsAccess'])

    def test_admin_troca_emails_access(self):
        ns, sio, salvos = montar({'ana@venturainformatica.com.br': conta()})
        ns['admin_alterar_acesso_dashboard']({'email': 'ana@venturainformatica.com.br', 'campo': 'emails_access',
                                              'acesso': True, 'clientId': 'c'})
        resposta = sio.ultimo('admin_resposta_acesso')
        self.assertTrue(resposta['sucesso'])
        self.assertTrue(resposta['emails_access'])
        self.assertTrue(salvos[-1]['ana@venturainformatica.com.br']['emails_access'])
        self.assertFalse(salvos[-1]['ana@venturainformatica.com.br'].get('portais_access', False))

    def test_listagem_inclui_emails_access(self):
        ns, sio, _ = montar({'ana@venturainformatica.com.br': conta(emails_access=True)})
        ns['admin_listar_usuarios']({'clientId': 'c'})
        self.assertTrue(sio.ultimo('admin_resposta_usuarios')['usuarios']['ana@venturainformatica.com.br']['emails_access'])


if __name__ == '__main__':
    unittest.main()
