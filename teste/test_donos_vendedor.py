"""Dono da cotação pelo nome na coluna VENDEDOR (Minhas Cotações). Mesmos casos de portaismaestro/tests/dono-cotacao.test.js."""
import unittest

import test_fase0  # noqa: F401  (põe a pasta do gerenciador no caminho de import)
import donos_vendedor as d

USUARIOS = {
    'cabral@x': {'nome': 'Gustavo Cabral Calvi', 'vendedor': 'GUSTAVO'},
    'libni@x': {'vendedor': 'libni'},
    'm1@x': {'vendedor': 'MICHELI'},             # mesma pessoa com duas contas antigas
    'm2@x': {'vendedor': 'micheli'},
    'rita@x': {'nome': 'Rita Souza', 'vendedor': ''},     # financeiro: não recebe cotação
    'bloq@x': {'nome': 'Bloq Ueado', 'vendedor': 'BLOQ', 'bloqueado': True},
}


def donos(celula, usuarios=USUARIOS):
    return d.donos_da_cotacao(celula, d.contas_vendedoras(usuarios))


class DonosVendedorTest(unittest.TestCase):
    def test_um_gustavo_so_recebe_as_antigas_com_o_primeiro_nome(self):
        self.assertEqual(donos('GUSTAVO'), {'cabral@x'})
        self.assertEqual(donos(' gustavo '), {'cabral@x'})
        self.assertEqual(donos('Gustavo Cabral'), {'cabral@x'})
        self.assertEqual(donos('GUSTAVO CABRAL CALVI'), {'cabral@x'})
        self.assertEqual(donos('Gustavo Calvi'), {'cabral@x'})

    def test_dois_gustavos(self):
        usuarios = {**USUARIOS, 'santos@x': {'nome': 'Gustavo Santos', 'vendedor': 'GUSTAVO'}}
        self.assertEqual(donos('GUSTAVO', usuarios), set())                 # ambíguo: ninguém
        self.assertEqual(donos('Gustavo Cabral', usuarios), {'cabral@x'})
        self.assertEqual(donos('Gustavo Santos', usuarios), {'santos@x'})
        self.assertEqual(donos('Gustavo Silva', usuarios), set())

    def test_conta_antiga_sem_nome_e_gustavo_novo_sao_pessoas_diferentes(self):
        usuarios = {'velho@x': {'vendedor': 'GUSTAVO'}, 'santos@x': {'nome': 'Gustavo Santos', 'vendedor': 'GUSTAVO'}}
        self.assertEqual(donos('GUSTAVO', usuarios), set())
        self.assertEqual(donos('Gustavo Santos', usuarios), {'santos@x'})

    def test_mesma_pessoa_com_duas_contas_ve_as_duas(self):
        self.assertEqual(donos('MICHELI'), {'m1@x', 'm2@x'})
        self.assertEqual(donos('Libní'), {'libni@x'})

    def test_celula_com_varios_vendedores_e_lixo(self):
        self.assertEqual(donos('GUSTAVO / LIBNI'), {'cabral@x', 'libni@x'})
        for nada in ('', None, 'W', 'VENCIDA', 'Rita Souza', 'BLOQ', 'Cabral'):
            self.assertEqual(donos(nada), set(), nada)


if __name__ == '__main__':
    unittest.main()
