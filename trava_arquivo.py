"""Trava entre processos (e máquinas, pelo SMB) para a troca final de um JSON da base.

Mesmo formato do cotafrete (cruzar_nf/arquivo_pedidos.py, trava_pedidos): arquivo "<base>.lock" na mesma pasta,
1 byte na posição 0, nova tentativa a cada 0,2 s por até 30 s. Segura só a conferência final e o os.replace.
O arquivo de trava nunca é apagado. Mudar aqui exige mudar lá também.
"""
import os
import time
from contextlib import contextmanager

INTERVALO_S = 0.2
ESPERA_S = 30


class TravaOcupada(RuntimeError):
    pass


def _travar(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0)


def _soltar(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.lockf(fd, fcntl.LOCK_UN, 1, 0)


@contextmanager
def trava_arquivo(caminho, espera_s=None, intervalo_s=INTERVALO_S, relogio=time):
    espera_s = ESPERA_S if espera_s is None else espera_s
    fd = os.open(str(caminho) + '.lock', os.O_RDWR | os.O_CREAT)
    try:
        limite = relogio.time() + espera_s
        while True:
            try:
                _travar(fd)
                break
            except OSError:
                if relogio.time() >= limite:
                    raise TravaOcupada(f'{os.path.basename(str(caminho))} está sendo gravado por outro serviço; '
                                       'nada foi gravado. Tente novamente.')
                relogio.sleep(intervalo_s)
        try:
            yield
        finally:
            _soltar(fd)
    finally:
        os.close(fd)
