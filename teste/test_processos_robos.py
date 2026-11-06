# -*- coding: utf-8 -*-
"""Detecção de robô já rodando (processos_robos.py) com processos falsos no lugar do psutil."""
import os
import unittest
from unittest.mock import patch

import psutil

import processos_robos as pr

PASTA = r"C:\Robos\ComprasGOV\ComprasGOV"


class ProcFalso:
    def __init__(self, pid, name, exe='', cmdline=(), cwd='', negar_cwd=False):
        self.pid = pid
        self.info = {'pid': pid, 'name': name, 'exe': exe, 'cmdline': list(cmdline)}
        self._cwd = cwd
        self._negar = negar_cwd
        self.morto = False
        self.filhos = []

    def cwd(self):
        if self._negar:
            raise psutil.AccessDenied(self.pid)
        return self._cwd

    def children(self, recursive=False):
        return self.filhos

    def kill(self):
        self.morto = True


def _com(procs):
    return patch('psutil.process_iter', lambda attrs=None: iter(procs))


class TestProcessosDotnet(unittest.TestCase):
    def test_dotnet_run_com_cwd_na_pasta(self):
        p = ProcFalso(10, 'dotnet.exe', cmdline=['dotnet', 'run'], cwd=PASTA)
        with _com([p]):
            self.assertEqual(pr.processos_dotnet(PASTA), [p])

    def test_dotnet_de_outra_pasta_nao_conta(self):
        p = ProcFalso(10, 'dotnet.exe', cmdline=['dotnet', 'run'], cwd=r"C:\Robos\Outro")
        q = ProcFalso(11, 'dotnet.exe', cmdline=['dotnet', 'run'], cwd=PASTA + '2')   # prefixo parecido
        with _com([p, q]):
            self.assertEqual(pr.processos_dotnet(PASTA), [])

    def test_auxiliares_do_build_nao_contam(self):
        p = ProcFalso(10, 'dotnet.exe', cmdline=['dotnet', r'C:\sdk\MSBuild.dll', '/nodemode:1'], cwd=PASTA)
        with _com([p]):
            self.assertEqual(pr.processos_dotnet(PASTA), [])

    def test_exe_compilado_dentro_da_pasta(self):
        p = ProcFalso(10, 'ComprasGOV.exe', exe=os.path.join(PASTA, 'bin', 'Debug', 'ComprasGOV.exe'))
        d = ProcFalso(11, 'chromedriver.exe', exe=os.path.join(PASTA, 'bin', 'chromedriver.exe'))
        with _com([p, d]):
            self.assertEqual(pr.processos_dotnet(PASTA), [p])

    def test_nome_exe_e_termo_na_linha_de_comando(self):
        a = ProcFalso(10, 'AribaSourcing.exe', exe=r"D:\outro\AribaSourcing.exe")
        b = ProcFalso(11, 'dotnet.exe', cmdline=['dotnet', 'AribaSourcing.dll'], negar_cwd=True)
        with _com([a, b]):
            self.assertEqual(pr.processos_dotnet(r"C:\Ariba", nomes_exe=('AribaSourcing.exe',),
                                                 termos_cmd=('aribasourcing',)), [a, b])

    def test_cwd_negado_nao_quebra(self):
        p = ProcFalso(10, 'dotnet.exe', cmdline=['dotnet', 'run'], negar_cwd=True)
        with _com([p]):
            self.assertEqual(pr.processos_dotnet(PASTA), [])


class TestProcessosComTermo(unittest.TestCase):
    def test_acha_pelo_termo(self):
        f = ProcFalso(10, 'python.exe', cmdline=['python', 'findes.py'])
        with _com([f]):
            self.assertEqual(pr.processos_com_termo('findes.py'), [f])

    def test_ignora_o_gerenciador(self):
        g = ProcFalso(10, 'python.exe', cmdline=['python', 'gerenciador.py', '--findes.py'])
        with _com([g]):
            self.assertEqual(pr.processos_com_termo('findes.py'), [])

    def test_filtra_por_nome(self):
        chrome = ProcFalso(10, 'chrome.exe', cmdline=['chrome', r'--user-data-dir=C:\m\perfis_chrome\coupa'])
        outro = ProcFalso(11, 'notepad.exe', cmdline=['notepad', r'C:\m\perfis_chrome\coupa\x.txt'])
        with _com([chrome, outro]):
            self.assertEqual(pr.processos_com_termo(r'C:\m\perfis_chrome\coupa', nomes={'chrome.exe'}), [chrome])


class TestMatar(unittest.TestCase):
    def test_mata_processo_e_filhos(self):
        filho = ProcFalso(11, 'ComprasGOV.exe')
        pai = ProcFalso(10, 'dotnet.exe')
        pai.filhos = [filho]
        pr.matar([pai])
        self.assertTrue(pai.morto and filho.morto)


if __name__ == '__main__':
    unittest.main()
