#!/usr/bin/env python3
"""Busca de preços — linha de comando.

Este arquivo é a casca. Toda a lógica está no pacote `buscaprecos/`, que é o
"payload" substituível pela atualização OTA. Manter a casca fina é o que
permite trocar a lógica sem regerar o executável.

Exemplos:
    python main.py                      # passe 1, grava em arquivo novo
    python main.py --pass2              # só células vazias, matching flexível
    python main.py --somente-calculo    # recalcula sem chamar API
    python main.py --no-local           # grava na própria planilha (faz .bak)
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

from buscaprecos import VERSION
from buscaprecos.busca import Buscador, Progresso
from buscaprecos.cache import CachePrecos
from buscaprecos.config import carregar_config
from buscaprecos.planilha import Planilha
from buscaprecos.regras import COLUNAS_DERIVADAS, calcular_derivadas

RAIZ = Path(__file__).resolve().parent


def _barra(p: Progresso) -> None:
    pct = int(100 * p.feitas / p.total) if p.total else 100
    print(f"  {p.loja:18} {pct:3}%  ({p.feitas}/{p.total})  {p.preenchidos} preços", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Busca preços de varejo e calcula markup OK/AJUSTAR."
    )
    parser.add_argument("--planilha", help="Sobrepõe o arquivo do config.json")
    parser.add_argument("--saida", help="Arquivo de saída (padrão: novo, com data/hora)")
    parser.add_argument(
        "--no-local", action="store_true",
        help="Grava na própria planilha de entrada (cria cópia .bak antes)",
    )
    parser.add_argument(
        "--pass2", action="store_true",
        help="Só preenche células vazias, com matching flexível",
    )
    parser.add_argument(
        "--somente-calculo", action="store_true",
        help="Recalcula as colunas derivadas sem chamar API",
    )
    parser.add_argument(
        "--sem-cache", action="store_true", help="Ignora o cache SQLite"
    )
    parser.add_argument("--version", action="version", version=f"buscaprecos {VERSION}")
    args = parser.parse_args()

    cfg = carregar_config(RAIZ)
    caminho = Path(args.planilha) if args.planilha else cfg.caminho("planilha")
    if not caminho.is_file():
        print(f"ERRO: planilha não encontrada: {caminho}", file=sys.stderr)
        return 2

    print(f"buscaprecos {VERSION}")
    print(f"planilha: {caminho.name}")

    planilha = Planilha.carregar(caminho, coluna_chave=cfg.colunas["descricao"])
    planilha.limpar_erros_excel(cfg.colunas["custo"])
    planilha.garantir_colunas(
        cfg.colunas_de_preco(), depois_de=["Observações", "Vl Total (R$)"]
    )
    planilha.garantir_colunas(COLUNAS_DERIVADAS)
    print(f"linhas: {len(planilha.linhas)} | colunas: {len(planilha.colunas)}")
    for aviso in planilha.avisos:
        print(f"  aviso: {aviso}")

    cache = None if args.sem_cache else CachePrecos(
        cfg.caminho("cache"),
        validade_horas=float(cfg.busca.get("cache_validade_horas", 24)),
    )

    log: dict = {"cep": cfg.cep, "versao": VERSION, "produtos": {}}
    caminho_log = cfg.caminho("log")
    if args.pass2 and caminho_log.exists():
        try:
            log = json.loads(caminho_log.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    cancelar = threading.Event()

    def _interromper(*_: object) -> None:
        print("\ninterrompendo... (salvando o que já foi encontrado)", flush=True)
        cancelar.set()

    signal.signal(signal.SIGINT, _interromper)

    buscador = Buscador(
        cfg, cache=cache, log=log, ao_progredir=_barra,
        ao_avisar=lambda texto, nivel: print(f"  AVISO: {texto}", flush=True),
        cancelar=cancelar,
    )
    categorias = buscador.categorias_estoque()

    if not args.somente_calculo:
        passe = 2 if args.pass2 else 1
        print(f"\nbuscando (passe {passe}) em: "
              f"{', '.join(lj.nome for lj in cfg.lojas_para_buscar())}")
        resultado = buscador.executar(
            planilha.linhas, somente_vazias=args.pass2, passe=passe
        )
        print(f"\n{resultado.resumo()}")
        for loja, motivo in resultado.lojas_desligadas.items():
            print(f"  LOJA FORA: {loja} — {motivo}")

    n_estoque = buscador.aplicar_estoque(planilha.linhas)
    calculadas = calcular_derivadas(planilha.linhas, cfg, categorias)
    buscador.fechar()

    saida = planilha.salvar(
        cfg.colunas_de_preco() + COLUNAS_DERIVADAS,
        destino=args.saida,
        no_local=args.no_local,
    )
    caminho_log.write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if cache is not None:
        cache.close()

    print(f"\nestoque próprio (PAULO): {n_estoque} preços")
    print(f"linhas calculadas: {calculadas}/{len(planilha.linhas)}")
    for aviso in planilha.avisos:
        print(f"aviso: {aviso}")
    print(f"saída: {saida}")
    print(f"log:   {caminho_log.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
