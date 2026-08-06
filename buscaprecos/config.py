"""Carga de `config.json` e de `categorias.csv`.

Tudo que o cliente pode querer mudar sem mexer em código mora aqui: CEP,
lojas ativas, markup por categoria, limiares de busca e quais lojas entram
na estatística de mercado.
"""

from __future__ import annotations

import csv
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Plataformas com cliente implementado. O cliente pode cadastrar lojas novas
# destes tipos sozinho; um tipo novo (site próprio, Shopify) exige código.
TIPOS_LOJA = {
    "vip": "VipCommerce (informe o domínio, ex.: loja.exemplo.com.br)",
    "vtex": "VTEX (informe a URL, ex.: https://www.exemplo.com.br)",
    "araujo": "Araújo (Salesforce Commerce)",
    "estoque": "Planilha de estoque próprio",
    "manual": "Preenchida à mão (o programa não consulta)",
}


@dataclass
class Loja:
    nome: str
    tipo: str  # "vip" | "vtex" | "araujo" | "estoque" | "manual"
    endereco: str
    ativa: bool = True

    @property
    def consulta_rede(self) -> bool:
        return self.tipo in {"vip", "vtex", "araujo"}


@dataclass
class Config:
    """Configuração resolvida.

    Duas raízes de propósito:

    * `raiz` — dados do cliente: `config.json`, cache, planilhas, log. Fica ao
      lado do executável e **nunca** é tocada por uma atualização.
    * `raiz_payload` — conteúdo que vem na atualização: `categorias.csv`,
      `config.padrao.json`. É a pasta que a atualização substitui.

    Sem essa separação o `config.json` nasceria dentro do payload e o cliente
    perderia CEP, lojas e mapeamento de colunas em cada atualização.
    """

    raiz: Path
    cep: str
    arquivos: dict[str, str]
    colunas: dict[str, str]
    lojas: dict[str, Loja]
    estatisticas: dict[str, Any]
    busca: dict[str, Any]
    markup_alvo: dict[str, float]
    categoria_estoque_para_markup: dict[str, str]
    atualizacao: dict[str, Any]
    regras_categoria: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    arquivo: Path | None = None
    bruto: dict[str, Any] = field(default_factory=dict)
    raiz_payload: Path | None = None
    avisos_de_migracao: list[str] = field(default_factory=list)

    # -- caminhos ---------------------------------------------------------- #

    def caminho(self, chave: str) -> Path:
        """Resolve um arquivo: dados do cliente primeiro, payload como base.

        Assim o cliente pode substituir `categorias.csv` localmente, e quem
        não substituiu recebe a versão que veio na atualização.
        """
        nome = self.arquivos[chave]
        do_cliente = (self.raiz / nome).resolve()
        if do_cliente.is_file():
            return do_cliente
        if self.raiz_payload is not None:
            do_payload = (self.raiz_payload / nome).resolve()
            if do_payload.is_file():
                return do_payload
        return do_cliente  # ainda não existe: será criado nos dados do cliente

    # -- seleções ---------------------------------------------------------- #

    def lojas_ativas(self) -> list[Loja]:
        return [lj for lj in self.lojas.values() if lj.ativa]

    def lojas_para_buscar(self) -> list[Loja]:
        """As que o programa consulta pela rede (exclui só PAULO e manuais)."""
        return [lj for lj in self.lojas_ativas() if lj.consulta_rede]

    def loja_estoque(self) -> Loja | None:
        for lj in self.lojas_ativas():
            if lj.tipo == "estoque":
                return lj
        return None

    def colunas_de_preco(self) -> list[str]:
        """Ordem em que as colunas de loja aparecem na planilha."""
        return list(self.lojas.keys())

    def colunas_estatistica(self) -> list[str]:
        """Lojas que compõem MENOR/MAIOR PREÇO e MÉDIA 3 MAIORES."""
        registradas = self.estatisticas.get("lojas") or self.colunas_de_preco()
        # Loja removida do cadastro não pode continuar puxando estatística.
        return [c for c in registradas if c in self.lojas]

    def adicionar_loja(self, nome: str, tipo: str, endereco: str) -> Loja:
        nome = nome.strip().upper()
        if not nome:
            raise ValueError("a loja precisa de um nome")
        if tipo not in TIPOS_LOJA:
            raise ValueError(f"tipo desconhecido: {tipo}")
        if nome in self.lojas:
            raise ValueError(f"já existe uma loja chamada {nome}")
        loja = Loja(nome=nome, tipo=tipo, endereco=endereco.strip(), ativa=True)
        self.lojas[nome] = loja
        estat = list(self.estatisticas.get("lojas") or [])
        estat.append(nome)
        self.estatisticas["lojas"] = estat
        return loja

    def remover_loja(self, nome: str) -> None:
        self.lojas.pop(nome, None)
        self.estatisticas["lojas"] = [
            c for c in (self.estatisticas.get("lojas") or []) if c != nome
        ]


ARQUIVO_PADRAO = "config.padrao.json"


def _caminho_config(raiz: Path, nome: str, raiz_payload: Path | None = None) -> Path:
    """Config do cliente; na primeira abertura, copia do padrão do payload.

    O payload traz `config.padrao.json` justamente para uma atualização nunca
    sobrescrever o `config.json` já ajustado pelo cliente.
    """
    destino = raiz / nome
    if not destino.exists():
        for base in (raiz, raiz_payload):
            if base is None:
                continue
            padrao = base / ARQUIVO_PADRAO
            if padrao.exists():
                destino.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(padrao, destino)
                break
    return destino


def carregar_config(
    raiz: Path | str = ".",
    raiz_payload: Path | str | None = None,
    nome: str = "config.json",
) -> Config:
    raiz = Path(raiz).resolve()
    base_payload = Path(raiz_payload).resolve() if raiz_payload else None
    caminho = _caminho_config(raiz, nome, base_payload)
    dados = json.loads(caminho.read_text(encoding="utf-8"))

    lojas = {
        nome_loja: Loja(
            nome=nome_loja,
            tipo=info["tipo"],
            endereco=info.get("endereco", ""),
            ativa=bool(info.get("ativa", True)),
        )
        for nome_loja, info in dados["lojas"].items()
    }

    cfg = Config(
        raiz=raiz,
        raiz_payload=base_payload,
        cep=dados["cep"],
        arquivos=dados["arquivos"],
        colunas=dados["colunas"],
        lojas=lojas,
        estatisticas=dados.get("estatisticas", {}),
        busca=dados.get("busca", {}),
        markup_alvo={k: float(v) for k, v in dados.get("markup_alvo", {}).items()},
        categoria_estoque_para_markup=dados.get("categoria_estoque_para_markup", {}),
        atualizacao=dados.get("atualizacao", {}),
    )
    cfg.regras_categoria = carregar_categorias(cfg.caminho("categorias"))
    cfg.arquivo = caminho
    cfg.bruto = dados
    _adotar_url_de_atualizacao(cfg, base_payload)
    cfg.avisos_de_migracao = _mesclar_lojas_do_padrao(cfg, base_payload)
    if cfg.avisos_de_migracao:
        # Grava na hora: sem isso a mudança só valeria em memória e o cliente
        # veria a loja aparecer e desaparecer conforme salvasse ou não.
        try:
            salvar_config(cfg)
        except Exception:
            pass
    return cfg


def _ler_padrao(base_payload: Path | None) -> dict[str, Any] | None:
    if base_payload is None:
        return None
    padrao = base_payload / ARQUIVO_PADRAO
    if not padrao.is_file():
        return None
    try:
        return json.loads(padrao.read_text(encoding="utf-8"))
    except Exception:
        return None


def _mesclar_lojas_do_padrao(
    cfg: Config, base_payload: Path | None
) -> list[str]:
    """Traz lojas novas do payload para o `config.json` já existente.

    O `config.json` do cliente é preservado nas atualizações — certo para CEP,
    colunas e lojas que ele cadastrou, errado para as lojas que **eu** mantenho.
    Sem isto, publicar o Carrefour no seed não fazia efeito em nenhuma
    instalação existente: o seed só é lido quando não há `config.json`. Foi o
    mesmo furo da URL de atualização, que eu tratei como caso isolado em vez de
    generalizar.

    A divisão de responsabilidade:

    * loja que vem no seed → **eu** mantenho `tipo` e `endereco` (é
      infraestrutura: quando uma loja troca de plataforma, a correção precisa
      chegar); o cliente mantém `ativa`.
    * loja que o cliente cadastrou → intocada, sempre.
    * loja que saiu do seed → **nunca** removida.

    Devolve a lista de mudanças, para a interface dizer o que aconteceu.
    """
    padrao = _ler_padrao(base_payload)
    if not padrao:
        return []
    lojas_padrao = padrao.get("lojas") or {}
    if not isinstance(lojas_padrao, dict):
        return []

    mudancas: list[str] = []
    estat = list(cfg.estatisticas.get("lojas") or [])

    for nome, info in lojas_padrao.items():
        if not isinstance(info, dict):
            continue
        tipo = str(info.get("tipo") or "")
        endereco = str(info.get("endereco") or "")
        atual = cfg.lojas.get(nome)

        if atual is None:
            cfg.lojas[nome] = Loja(
                nome=nome, tipo=tipo, endereco=endereco,
                ativa=bool(info.get("ativa", True)),
            )
            if nome not in estat:
                estat.append(nome)
            mudancas.append(f"loja {nome} incluída")
            continue

        # Já existe: corrige só plataforma e endereço, preservando o liga/desliga.
        if tipo and atual.tipo != tipo:
            mudancas.append(
                f"loja {nome}: plataforma {atual.tipo or '(vazia)'} → {tipo}"
            )
            atual.tipo = tipo
        if endereco and atual.endereco != endereco:
            mudancas.append(f"loja {nome}: endereço atualizado")
            atual.endereco = endereco
        if nome not in estat:
            estat.append(nome)

    cfg.estatisticas["lojas"] = estat
    return mudancas


def _adotar_url_de_atualizacao(cfg: Config, base_payload: Path | None) -> None:
    """Herda a URL de atualização do padrão do payload, se o cliente não tem.

    O `config.json` do cliente é preservado entre atualizações — que é o certo
    para CEP e lojas, mas ruim para a URL de atualização: uma instalação antiga
    ficaria com o campo vazio para sempre e nunca receberia correção. Este é o
    único campo que a atualização pode preencher, e só quando está em branco:
    é infraestrutura, não preferência do usuário.
    """
    if str(cfg.atualizacao.get("url_version_json") or "").strip():
        return
    padrao = _ler_padrao(base_payload)
    if not padrao:
        return
    url = str((padrao.get("atualizacao") or {}).get("url_version_json") or "").strip()
    if url:
        cfg.atualizacao["url_version_json"] = url


def salvar_config(cfg: Config) -> Path:
    """Grava as alterações feitas na interface, preservando o resto do arquivo.

    Sem isso, CEP e lojas marcadas valiam só para a execução em curso: fechar
    o programa perdia tudo. Guarda uma cópia antes de escrever, porque um
    config quebrado impede o programa de abrir.
    """
    destino = cfg.arquivo
    if destino is None:
        raise ValueError("config sem arquivo de origem")
    dados = dict(cfg.bruto or {})
    dados["cep"] = cfg.cep
    dados["lojas"] = {
        nome: {"tipo": lj.tipo, "endereco": lj.endereco, "ativa": lj.ativa}
        for nome, lj in cfg.lojas.items()
    }
    dados["colunas"] = dict(cfg.colunas)
    dados["arquivos"] = dict(cfg.arquivos)
    dados["estatisticas"] = dict(cfg.estatisticas)
    dados["busca"] = dict(cfg.busca)

    if destino.exists():
        reserva = destino.with_name(
            f"{destino.stem}.bak-{datetime.now():%Y%m%d-%H%M}{destino.suffix}"
        )
        shutil.copy2(destino, reserva)
    destino.write_text(
        json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    cfg.bruto = dados
    return destino


def carregar_categorias(caminho: Path) -> list[tuple[str, tuple[str, ...]]]:
    """Lê `categorias.csv` preservando a ordem das chaves.

    A ordem importa: a primeira chave cuja palavra aparecer na descrição
    ganha, então CERVEJAS tem que vir antes de BEBIDAS_NAO_ALCOOLICAS.
    """
    if not caminho.exists():
        return []
    agrupado: dict[str, list[str]] = {}
    with caminho.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            chave = (row.get("chave") or "").strip()
            palavra = (row.get("palavra") or "").strip()
            if chave and palavra:
                agrupado.setdefault(chave, []).append(palavra)
    return [(chave, tuple(palavras)) for chave, palavras in agrupado.items()]
