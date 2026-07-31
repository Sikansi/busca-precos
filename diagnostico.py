#!/usr/bin/env python3
"""Roda o diagnóstico pela linha de comando.

    python diagnostico.py

A lógica está em `buscaprecos/diagnostico.py`, dentro do payload, para o
cliente poder rodar pelo botão da interface sem instalar nada.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

from buscaprecos.diagnostico import executar  # noqa: E402

if __name__ == "__main__":
    destino = executar(RAIZ)
    print(f"\nMande {destino.name} para o desenvolvedor.")
