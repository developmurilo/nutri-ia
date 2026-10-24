import sys
import os

# Garante que o diretório raiz esteja no path para importar main e outros módulos
diretorio_raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if diretorio_raiz not in sys.path:
    sys.path.insert(0, diretorio_raiz)

from main import app
