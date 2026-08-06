"""Busca de preços de varejo e cálculo de markup — núcleo do app.

Este pacote é o "payload" atualizável: nada aqui depende de caminho absoluto
nem do executável que o carrega, para que uma atualização OTA possa substituir
a pasta inteira com o app aberto. Ver `atualizacao.py`.
"""

from __future__ import annotations

VERSION = "1.1.1"

__all__ = ["VERSION"]
