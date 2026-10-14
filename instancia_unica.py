"""Um gerenciador por vez.

Trava um arquivo na pasta da base (compartilhada, vale até entre máquinas e
cópias do gerenciador em outras pastas) enquanto o processo vive. Se o
processo morrer, o Windows solta a trava sozinho: nunca fica trava presa.
"""
import os
import socket
import time

ARQ_INSTANCIA = "gerenciador.instancia"
POS_TRAVA = 1 << 20  # trava um byte longe do texto, que continua legível por quem é recusado


class JaRodando(RuntimeError):
    pass


def _travar(arq):
    arq.seek(POS_TRAVA)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(arq.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.lockf(arq, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, POS_TRAVA)


def instancia_unica(pasta):
    """Devolve o arquivo travado: mantenha a referência enquanto o processo roda."""
    fd = os.open(os.path.join(str(pasta), ARQ_INSTANCIA), os.O_RDWR | os.O_CREAT)
    arq = os.fdopen(fd, "r+", encoding="utf-8")
    try:
        _travar(arq)
    except OSError:
        arq.seek(0)
        quem = arq.read(500).strip() or "outro processo"
        arq.close()
        raise JaRodando(quem)
    arq.seek(0)
    arq.truncate()
    arq.write(f"{socket.gethostname()}, PID {os.getpid()}, desde {time.strftime('%d/%m/%Y %H:%M')}")
    arq.flush()
    return arq
