#!/usr/bin/env python3
"""Monta a pasta que o cliente recebe.

    python build.py 1.0.0

Resultado em `dist/BuscaPrecos/`:

    BuscaPrecos.exe        casca: interpretador + dependências
    _internal/             bibliotecas do PyInstaller
    payload/
      ATUAL                arquivo texto com "1.0.0"
      1.0.0/
        buscaprecos/       a lógica — é isto que a atualização troca
        categorias.csv
        config.padrao.json

O `config.json` **não** vai no pacote: ele nasce ao lado do .exe na primeira
abertura, copiado de `config.padrao.json`. Por isso uma atualização nunca
apaga o CEP, as lojas nem o mapeamento de colunas do cliente.

Sem `--sem-exe`, precisa rodar no Windows: o PyInstaller não faz
cross-compile. Do Linux, use `--sem-exe` para gerar só o payload e o
`version.json` (é o que você precisa para publicar uma atualização de lógica,
que é o caso comum).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
DIST = RAIZ / "dist"
PASTA_APP = DIST / "BuscaPrecos"
# Guarda a última base-url usada. Sem isso, esquecer o parâmetro gera um
# version.json apontando para o placeholder e a atualização quebra em silêncio.
MEMORIA = RAIZ / ".build.json"

ARQUIVOS_PAYLOAD = {
    "categorias.csv": "categorias.csv",
    "config.json": "config.padrao.json",
}


def _base_url_lembrada() -> str | None:
    if not MEMORIA.exists():
        return None
    try:
        return json.loads(MEMORIA.read_text(encoding="utf-8")).get("base_url")
    except Exception:
        return None


def atualizar_versao(versao: str) -> None:
    init = RAIZ / "buscaprecos" / "__init__.py"
    texto = init.read_text(encoding="utf-8")
    novo, n = re.subn(
        r'^VERSION = ".*"$', f'VERSION = "{versao}"', texto, flags=re.MULTILINE
    )
    if n != 1:
        raise SystemExit("não achei a linha VERSION em buscaprecos/__init__.py")
    if novo != texto:
        init.write_text(novo, encoding="utf-8")
    print(f"  versão → {versao}")


def montar_payload(destino_pai: Path, versao: str) -> Path:
    """Copia o pacote e os dados para `payload/<versão>/` e marca como ativa."""
    pasta_payload = destino_pai / "payload"
    destino = pasta_payload / versao
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)

    shutil.copytree(
        RAIZ / "buscaprecos",
        destino / "buscaprecos",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    for origem, nome_final in ARQUIVOS_PAYLOAD.items():
        caminho = RAIZ / origem
        if caminho.exists():
            shutil.copy2(caminho, destino / nome_final)

    (pasta_payload / "ATUAL").write_text(versao, encoding="utf-8")
    print(f"  payload → {destino.relative_to(DIST)}")
    return destino


# Vão na pasta do cliente, ao lado do executável.
EXTRAS_DO_CLIENTE = ("MANUAL-CLIENTE.md",)


def copiar_para_o_cliente() -> None:
    """Manual junto do programa — é onde está o aviso do SmartScreen.

    Se o manual não estiver na pasta, o cliente vê "aplicativo não
    reconhecido" na primeira execução, não sabe que é esperado, e desiste.
    """
    for nome in EXTRAS_DO_CLIENTE:
        origem = RAIZ / nome
        if origem.exists():
            shutil.copy2(origem, PASTA_APP / nome)
            print(f"  {nome} → dist/BuscaPrecos/")


def compactar_payload(pasta: Path, versao: str) -> Path:
    """Zip do payload, para publicar como atualização."""
    base = DIST / f"payload-{versao}"
    caminho = Path(shutil.make_archive(str(base), "zip", root_dir=pasta))
    print(f"  {caminho.name} ({caminho.stat().st_size // 1024} KB)")
    return caminho


def rodar_pyinstaller() -> None:
    if sys.platform != "win32":
        raise SystemExit(
            "O .exe só pode ser gerado no Windows (PyInstaller não faz "
            "cross-compile).\nUse --sem-exe para gerar apenas o payload."
        )
    print("  rodando PyInstaller…")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "BuscaPrecos.spec", "--noconfirm"],
        cwd=RAIZ,
        check=True,
    )


def escrever_version_json(caminho_zip: Path, versao: str, base_url: str,
                          notas: str, obrigatoria: bool) -> Path:
    sys.path.insert(0, str(RAIZ))
    from buscaprecos.atualizacao import gerar_version_json

    manifesto = gerar_version_json(
        caminho_zip,
        versao,
        f"{base_url.rstrip('/')}/{caminho_zip.name}",
        notas=notas,
        obrigatoria=obrigatoria,
    )
    destino = DIST / "version.json"
    destino.write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"  version.json (sha256 {manifesto['sha256'][:16]}…)")
    return destino


def main() -> int:
    parser = argparse.ArgumentParser(description="Empacota o programa.")
    parser.add_argument("versao", help='Ex.: "1.0.0"')
    parser.add_argument(
        "--sem-exe", action="store_true",
        help="Gera só o payload e o version.json (roda no Linux)",
    )
    parser.add_argument("--notas", default="", help="O que mudou")
    parser.add_argument("--obrigatoria", action="store_true")
    parser.add_argument(
        "--base-url",
        default=None,
        help="Base da URL dos arquivos de release (fica lembrada)",
    )
    args = parser.parse_args()

    base_url = args.base_url or _base_url_lembrada()
    if not base_url:
        raise SystemExit(
            "Informe --base-url uma vez (ex.: "
            "https://github.com/USUARIO/REPO/releases/latest/download). "
            "Depois disso fica lembrada."
        )
    if args.base_url:
        MEMORIA.write_text(
            json.dumps({"base_url": args.base_url}, indent=2), encoding="utf-8"
        )

    if not re.fullmatch(r"\d+\.\d+\.\d+", args.versao):
        raise SystemExit("versão tem que ser X.Y.Z")

    print(f"empacotando {args.versao}")
    DIST.mkdir(exist_ok=True)
    atualizar_versao(args.versao)

    if args.sem_exe:
        # Payload em pasta própria, sem mexer no build do executável.
        temporario = DIST / "_payload"
        if temporario.exists():
            shutil.rmtree(temporario)
        pasta = montar_payload(temporario, args.versao)
    else:
        rodar_pyinstaller()
        if not PASTA_APP.exists():
            raise SystemExit(f"PyInstaller não gerou {PASTA_APP}")
        pasta = montar_payload(PASTA_APP, args.versao)
        copiar_para_o_cliente()

    caminho_zip = compactar_payload(pasta, args.versao)
    escrever_version_json(
        caminho_zip, args.versao, base_url, args.notas, args.obrigatoria
    )

    print("\npróximos passos:")
    if args.sem_exe:
        print("  1. suba dist/version.json e dist/"
              f"payload-{args.versao}.zip no Release do GitHub")
        print("  2. o cliente vê a atualização na próxima abertura")
    else:
        print(f"  1. teste dist/BuscaPrecos/BuscaPrecos.exe numa máquina limpa")
        print("  2. compacte a pasta dist/BuscaPrecos e mande para o cliente")
        print("  3. suba version.json e o zip do payload para as próximas atualizações")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
