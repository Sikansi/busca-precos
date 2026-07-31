"""Atualização OTA do payload.

O problema que isto resolve: o Windows tranca o `.exe` em execução, então o
programa não consegue sobrescrever a si mesmo. A saída é separar em duas
camadas:

    BuscaPrecos.exe        interpretador + dependências — muda raramente
    payload/1.0.0/         este pacote, em .py — é o que você atualiza
    payload/1.1.0/
    payload/ATUAL          arquivo texto com a versão em uso

O `.exe` só descobre qual pasta carregar lendo `payload/ATUAL`. Trocar de
versão é escrever outro número ali e reiniciar — nenhum arquivo em uso é
sobrescrito, e não precisa de permissão de administrador.

Fluxo de publicação (na sua máquina):

    python build.py 1.1.0 --sem-exe   # gera payload-1.1.0.zip + version.json
    gh release create v1.1.0 dist/payload-1.1.0.zip dist/version.json

Fluxo no cliente:

    abre o app → checa version.json → "Nova versão 1.1.0" → clica →
    baixa zip → confere SHA-256 → extrai em payload/1.1.0/ →
    escreve ATUAL → reinicia

Se a nova versão der problema, `payload/ATUAL` volta para a anterior — a
pasta antiga continua no disco. É o rollback.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import VERSION
from .rede import nova_sessao

ARQUIVO_ATUAL = "ATUAL"
TIMEOUT = 30


def _versao_como_tupla(v: str) -> tuple[int, ...]:
    partes = []
    for p in v.strip().split("."):
        digitos = "".join(c for c in p if c.isdigit())
        partes.append(int(digitos) if digitos else 0)
    return tuple(partes)


def mais_nova(candidata: str, atual: str = VERSION) -> bool:
    return _versao_como_tupla(candidata) > _versao_como_tupla(atual)


@dataclass
class Atualizacao:
    versao: str
    url_zip: str
    sha256: str
    notas: str = ""
    obrigatoria: bool = False

    @property
    def resumo(self) -> str:
        return f"Versão {self.versao}" + (f" — {self.notas}" if self.notas else "")


def checar(url_version_json: str) -> Atualizacao | None:
    """Consulta o `version.json`. Devolve None se já está atualizado.

    Nunca levanta exceção por falha de rede: sem internet o app tem que abrir
    normalmente, não travar na tela de checagem.
    """
    if not url_version_json:
        return None
    try:
        sessao = nova_sessao(tentativas=1)
        # O CDN do GitHub serve `releases/latest/download/` com cache: medi
        # `Age: 769` devolvendo a versão anterior quase 13 minutos depois de
        # publicar a nova. Sem furar esse cache, o cliente demora — ou deixa —
        # de ver a atualização.
        resp = sessao.get(
            url_version_json,
            timeout=TIMEOUT,
            headers={
                "Cache-Control": "no-cache, no-store, max-age=0",
                "Pragma": "no-cache",
            },
        )
        if resp.status_code != 200:
            return None
        dados = resp.json()
    except Exception:
        return None

    versao = str(dados.get("versao") or "").strip()
    url_zip = str(dados.get("url_zip") or "").strip()
    sha = str(dados.get("sha256") or "").strip().lower()
    if not (versao and url_zip and sha) or not mais_nova(versao):
        return None
    return Atualizacao(
        versao=versao,
        url_zip=url_zip,
        sha256=sha,
        notas=str(dados.get("notas") or ""),
        obrigatoria=bool(dados.get("obrigatoria")),
    )


def baixar_e_instalar(
    atualizacao: Atualizacao,
    pasta_payload: Path | str,
    *,
    ao_progredir: Callable[[int, int], None] | None = None,
) -> Path:
    """Baixa, confere o hash e extrai. Devolve a pasta da nova versão.

    O hash não é assinatura de código — não prova que fui eu que publiquei,
    prova que o arquivo chegou inteiro. Como não vamos comprar certificado, é
    a garantia que temos; por isso o `version.json` deve ficar em HTTPS.
    """
    pasta_payload = Path(pasta_payload)
    pasta_payload.mkdir(parents=True, exist_ok=True)

    sessao = nova_sessao(tentativas=2)
    resp = sessao.get(atualizacao.url_zip, timeout=TIMEOUT, stream=True)
    resp.raise_for_status()

    total = int(resp.headers.get("Content-Length") or 0)
    buffer = io.BytesIO()
    baixado = 0
    for bloco in resp.iter_content(chunk_size=64 * 1024):
        if not bloco:
            continue
        buffer.write(bloco)
        baixado += len(bloco)
        if ao_progredir:
            ao_progredir(baixado, total)

    conteudo = buffer.getvalue()
    obtido = hashlib.sha256(conteudo).hexdigest()
    if obtido != atualizacao.sha256:
        raise ValueError(
            f"SHA-256 não confere (esperado {atualizacao.sha256[:12]}…, "
            f"obtido {obtido[:12]}…) — download descartado"
        )

    destino = pasta_payload / atualizacao.versao
    provisorio = pasta_payload / f".{atualizacao.versao}.parcial"
    if provisorio.exists():
        shutil.rmtree(provisorio)
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        _validar_zip(z)
        z.extractall(provisorio)

    # Só troca de nome depois de extrair inteiro: se cair a luz no meio, a
    # pasta parcial fica com nome oculto e nunca é ativada.
    if destino.exists():
        shutil.rmtree(destino)
    provisorio.rename(destino)
    return destino


def _validar_zip(z: zipfile.ZipFile) -> None:
    """Recusa caminho absoluto ou `..` — o clássico zip-slip."""
    for nome in z.namelist():
        p = Path(nome)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"caminho suspeito no zip: {nome}")


def ativar(pasta_payload: Path | str, versao: str) -> None:
    """Aponta o `ATUAL` para a versão dada. Vale na próxima abertura."""
    pasta_payload = Path(pasta_payload)
    pasta = pasta_payload / versao
    if not (pasta / "buscaprecos" / "__init__.py").exists():
        raise FileNotFoundError(f"payload inválido em {pasta}")
    (pasta_payload / ARQUIVO_ATUAL).write_text(versao, encoding="utf-8")


def versao_ativa(pasta_payload: Path | str) -> str | None:
    arquivo = Path(pasta_payload) / ARQUIVO_ATUAL
    if not arquivo.exists():
        return None
    return arquivo.read_text(encoding="utf-8").strip() or None


def versoes_instaladas(pasta_payload: Path | str) -> list[str]:
    pasta_payload = Path(pasta_payload)
    if not pasta_payload.exists():
        return []
    return sorted(
        (p.name for p in pasta_payload.iterdir()
         if p.is_dir() and not p.name.startswith(".")),
        key=_versao_como_tupla,
    )


def limpar_antigas(pasta_payload: Path | str, manter: int = 2) -> list[str]:
    """Remove versões antigas, preservando as `manter` últimas e a ativa."""
    pasta_payload = Path(pasta_payload)
    ativa = versao_ativa(pasta_payload)
    todas = versoes_instaladas(pasta_payload)
    removidas = []
    for v in todas[:-manter] if len(todas) > manter else []:
        if v == ativa:
            continue
        shutil.rmtree(pasta_payload / v, ignore_errors=True)
        removidas.append(v)
    return removidas


def url_do_asset(base_releases: str, versao: str, nome_arquivo: str) -> str:
    """URL fixada na tag, não em `latest`.

    `releases/latest/download/payload-1.0.4.zip` devolve 404 no instante em que
    a 1.0.5 sai — o asset só existe no release dele. Um `version.json` em cache
    apontando para lá manda o cliente buscar um arquivo que não existe mais.
    `releases/download/v1.0.4/payload-1.0.4.zip` vale para sempre.
    """
    base = base_releases.rstrip("/")
    # Aceita a forma antiga (.../releases/latest/download) sem quebrar.
    for sufixo in ("/latest/download", "/download"):
        if base.endswith(sufixo):
            base = base[: -len(sufixo)]
    return f"{base}/download/v{versao}/{nome_arquivo}"


def gerar_version_json(
    zip_path: Path | str,
    versao: str,
    url_zip: str,
    *,
    notas: str = "",
    obrigatoria: bool = False,
) -> dict:
    """Monta o `version.json` a partir do zip já gerado."""
    zip_path = Path(zip_path)
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return {
        "versao": versao,
        "url_zip": url_zip,
        "sha256": sha,
        "notas": notas,
        "obrigatoria": obrigatoria,
        "tamanho_bytes": zip_path.stat().st_size,
    }
