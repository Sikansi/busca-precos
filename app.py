#!/usr/bin/env python3
"""Ponto de entrada do aplicativo — é isto que o `.exe` executa.

Este arquivo é a **casca**: fica dentro do executável e quase nunca muda.
Toda a lógica vem do payload (`buscaprecos/`), que é carregado em runtime e
pode ser substituído por uma atualização com o app aberto.

Como o payload é localizado, em ordem:

1. `payload/<versão em ATUAL>/` ao lado do executável — é o caso do cliente,
   depois de uma atualização;
2. `buscaprecos/` na pasta atual — é o caso de desenvolvimento, aqui na sua
   máquina.

Assim o mesmo arquivo roda nos dois lugares sem `if` espalhado.
"""

from __future__ import annotations

import sys
from pathlib import Path


def raiz_do_app() -> Path:
    """Pasta do executável (ou deste arquivo, em desenvolvimento)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def preparar_payload(raiz: Path) -> Path:
    """Coloca o payload no `sys.path` e devolve a pasta de dados."""
    pasta_payload = raiz / "payload"
    arquivo_atual = pasta_payload / "ATUAL"
    if arquivo_atual.exists():
        versao = arquivo_atual.read_text(encoding="utf-8").strip()
        candidato = pasta_payload / versao
        if (candidato / "buscaprecos" / "__init__.py").exists():
            sys.path.insert(0, str(candidato))
            return candidato
    # Desenvolvimento: o pacote está aqui do lado.
    sys.path.insert(0, str(raiz))
    return raiz


def checar_atualizacao(raiz: Path, pasta_payload: Path) -> None:
    """Pergunta ao usuário se quer atualizar. Falha em silêncio sem internet.

    Nunca bloqueia a abertura do app: erro de rede, URL não configurada ou
    servidor fora significam apenas "segue com a versão atual".
    """
    from tkinter import messagebox

    from buscaprecos import VERSION
    from buscaprecos.atualizacao import ativar, baixar_e_instalar, checar
    from buscaprecos.config import carregar_config

    try:
        cfg = carregar_config(raiz, pasta_payload)
    except Exception:
        return
    conf = cfg.atualizacao or {}
    if not conf.get("checar_na_abertura", True):
        return
    url = str(conf.get("url_version_json") or "").strip()
    if not url:
        return

    nova = checar(url)
    if nova is None:
        return

    quer = messagebox.askyesno(
        "Atualização disponível",
        f"Você está na versão {VERSION}.\n"
        f"{nova.resumo}\n\n"
        "Atualizar agora? O programa vai reiniciar."
        + ("\n\nEsta atualização é obrigatória." if nova.obrigatoria else ""),
    )
    if not quer:
        if nova.obrigatoria:
            messagebox.showwarning(
                "Atualização obrigatória",
                "Esta versão não pode continuar sem atualizar. Fechando.",
            )
            raise SystemExit(0)
        return

    try:
        baixar_e_instalar(nova, raiz / "payload")
        ativar(raiz / "payload", nova.versao)
    except Exception as exc:
        messagebox.showerror(
            "Falha ao atualizar",
            f"Não consegui instalar a versão {nova.versao}.\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "O programa segue funcionando na versão atual.",
        )
        return

    messagebox.showinfo(
        "Atualizado",
        f"Versão {nova.versao} instalada. Abra o programa de novo para usá-la.",
    )
    raise SystemExit(0)


def main() -> int:
    raiz = raiz_do_app()
    pasta_payload = preparar_payload(raiz)

    try:
        from buscaprecos.gui import main as abrir_janela
    except ImportError as exc:
        print(
            "Não encontrei a lógica do programa (payload).\n"
            f"Procurei em: {pasta_payload}\n{exc}",
            file=sys.stderr,
        )
        return 2

    checar_atualizacao(raiz, pasta_payload)
    return abrir_janela(raiz, pasta_payload)


if __name__ == "__main__":
    raise SystemExit(main())
