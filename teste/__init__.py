import sys
from pathlib import Path

PASTA_TESTE = Path(__file__).resolve().parent
PASTA_GERENCIADOR = PASTA_TESTE.parent

if str(PASTA_GERENCIADOR) not in sys.path:
    sys.path.insert(0, str(PASTA_GERENCIADOR))
if str(PASTA_TESTE) not in sys.path:
    sys.path.insert(0, str(PASTA_TESTE))
