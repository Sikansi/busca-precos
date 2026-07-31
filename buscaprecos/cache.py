"""Cache SQLite de resultados de busca, com validade.

Antes o cache vivia só na memória de uma execução: reabrir o programa
refazia as ~2.000 chamadas. Aqui ele sobrevive entre execuções, o que faz
diferença tanto no tempo quanto em não bater na mesma API repetidamente.

A chave é (loja, EAN) quando há código de barras e (loja, descrição
normalizada) quando não há — a mesma regra do `cache_key` original, agora
persistida. Resultado negativo ("não encontrado") também é guardado, senão
os itens sem match são reconsultados a cada execução.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .texto import normalize

_SCHEMA = """
CREATE TABLE IF NOT EXISTS precos (
    loja     TEXT NOT NULL,
    chave    TEXT NOT NULL,
    preco    TEXT,
    nome     TEXT,
    score    REAL,
    via      TEXT,
    salvo_em REAL NOT NULL,
    PRIMARY KEY (loja, chave)
);
"""


def chave_produto(ean: str, descricao: str) -> str:
    """EAN quando existe, senão a descrição normalizada."""
    return f"ean:{ean}" if ean else f"desc:{normalize(descricao)}"


class CachePrecos:
    def __init__(self, caminho: Path | str, validade_horas: float = 24.0):
        self.caminho = Path(caminho)
        self.validade_seg = validade_horas * 3600
        self._lock = threading.Lock()
        self._con = sqlite3.connect(str(self.caminho), check_same_thread=False)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.executescript(_SCHEMA)
        self._con.commit()

    def buscar(self, loja: str, chave: str) -> dict | None:
        """Devolve o registro se ainda válido; None se ausente ou expirado.

        Um registro de "não encontrado" volta como dict com preco=None — é
        diferente de None, que significa "nunca consultado".
        """
        limite = time.time() - self.validade_seg
        with self._lock:
            row = self._con.execute(
                "SELECT preco, nome, score, via, salvo_em FROM precos"
                " WHERE loja=? AND chave=? AND salvo_em>=?",
                (loja, chave, limite),
            ).fetchone()
        if row is None:
            return None
        return {"preco": row[0], "nome": row[1], "score": row[2], "via": row[3]}

    def gravar(
        self,
        loja: str,
        chave: str,
        *,
        preco: str | None,
        nome: str | None = None,
        score: float | None = None,
        via: str | None = None,
    ) -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO precos (loja, chave, preco, nome, score, via, salvo_em)"
                " VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(loja, chave) DO UPDATE SET"
                " preco=excluded.preco, nome=excluded.nome, score=excluded.score,"
                " via=excluded.via, salvo_em=excluded.salvo_em",
                (loja, chave, preco, nome, score, via, time.time()),
            )
            self._con.commit()

    def limpar_expirados(self) -> int:
        limite = time.time() - self.validade_seg
        with self._lock:
            cur = self._con.execute("DELETE FROM precos WHERE salvo_em<?", (limite,))
            self._con.commit()
            return cur.rowcount

    def estatisticas(self) -> dict[str, int]:
        with self._lock:
            total = self._con.execute("SELECT COUNT(*) FROM precos").fetchone()[0]
            com_preco = self._con.execute(
                "SELECT COUNT(*) FROM precos WHERE preco IS NOT NULL"
            ).fetchone()[0]
        return {"registros": total, "com_preco": com_preco}

    def close(self) -> None:
        with self._lock:
            self._con.close()

    def __enter__(self) -> CachePrecos:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
