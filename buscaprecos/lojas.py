"""Clientes de consulta de preço por loja.

Três famílias:

* **VipCommerce** (Verdemar, Villeforte) — API JSON, devolve o código de
  barras no payload, o que permite match exato.
* **VTEX** (Supernosso, Lojas Americanas, Atacadão) — API JSON do
  `catalog_system`. A busca textual usa `ft=`; a busca por código de barras
  **precisa** de `fq=alternateIds_Ean:` (com `ft=<ean>` a VTEX devolve zero).
* **Estoque local** (PAULO) — planilha de planograma do próprio cliente.

Preferência sempre: match exato por EAN > match por texto validado.
"""

from __future__ import annotations

import csv
import re
import time
import uuid
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from .precos import clean_ean, format_price, parse_price_br
from .rede import LimiteDeTaxa, sessao_curl_cffi, sessao_tls_navegador
from .texto import (
    fully_compatible,
    measures_compatible,
    normalize,
    score_match,
    search_queries,
    validate_candidate,
    vtex_query_tokens,
)


def _texto_aceitavel(descricao: str, nome: str, *, relaxed: bool) -> bool:
    """Barreiras aplicadas ao match por texto.

    `validate_candidate` cuida de marca e substantivo. A gramatura entra aqui
    porque sem ela o passe por texto aceita tamanho errado: uma consulta de
    "REFRIG COCA COLA LT 350ML" casava com "Refrigerante Coca-Cola 1,5L" e
    gravava R$ 9,69 como se fosse o preço da lata.

    Variante (ZERO/INTEGRAL) e sabor **não** entram: a descrição da nota vem
    abreviada ("LEITE LV ITAMBE TP 1L INTEG") e exigir a palavra inteira
    rejeitaria o produto correto. Essas duas barreiras seguem valendo só onde
    a fonte é permissiva — ver `fully_compatible`.
    """
    if not validate_candidate(descricao, nome, relaxed=relaxed):
        return False
    return measures_compatible(descricao, nome)


VIP_API = "https://services.vipcommerce.com.br/api-admin/v1"
VIP_LOJA_USER = "loja"
# Chave pública usada pelo próprio front das lojas VipCommerce.
VIP_AUTH_KEY = "df072f85df9bf7dd71b6811c34bdbaa4f219d98775b56cff9dfa5f8ca1bf8469"

SCORE_EAN = 200.0  # score sintético de match exato por código de barras


@dataclass
class Achado:
    nome: str
    preco: float
    score: float
    loja: str
    via: str  # "ean" | "texto"

    def preco_formatado(self) -> str:
        return format_price(self.preco) or ""


# --------------------------------------------------------------------------- #
# VipCommerce
# --------------------------------------------------------------------------- #

class ClienteVip:
    """Verdemar, Villeforte.

    O campo de código de barras no payload é `codigo_barras` (plural). A
    versão anterior procurava `codigo_barra` e por isso **nunca** conseguia
    match exato: 99% das linhas têm EAN e ainda assim tudo caía no fuzzy de
    texto (1 match por EAN contra 254 por texto no log de 24/07).
    """

    CAMPOS_EAN = ("codigo_barras", "codigo_barra", "ean", "gtin", "codigo_de_barras")

    def __init__(
        self,
        loja: str,
        dominio: str,
        sessao: requests.Session,
        *,
        timeout: int = 30,
    ):
        self.loja = loja
        self.dominio = dominio
        self.sessao = sessao
        self.timeout = timeout
        self.org_id: int | None = None
        self.filial_id = 1
        self.cd_id = 1
        self.headers: dict[str, str] = {}
        self._autenticar()

    def _autenticar(self) -> None:
        info = self.sessao.get(
            f"{VIP_API}/organizacoes/filiais/dominio/{self.dominio}",
            timeout=self.timeout,
        ).json()
        self.org_id = info["data"]["organizacao"]["id"]
        base = {
            "OrganizationId": str(self.org_id),
            "DomainKey": self.dominio,
            "Content-Type": "application/json",
        }
        auth = self.sessao.post(
            f"{VIP_API}/auth/loja/login",
            headers=base,
            json={
                "domain": self.dominio,
                "username": VIP_LOJA_USER,
                "key": VIP_AUTH_KEY,
            },
            timeout=self.timeout,
        ).json()
        self.headers = {**base, "Authorization": f"Bearer {auth['data']}"}

    def buscar_bruto(self, termo: str) -> list[dict[str, Any]]:
        slug = quote(normalize(termo).replace(" ", "+"))
        url = (
            f"{VIP_API}/org/{self.org_id}/filial/{self.filial_id}/"
            f"centro_distribuicao/{self.cd_id}/loja/buscas/produtos/termo/{slug}"
        )
        data = self.sessao.get(
            url,
            headers=self.headers,
            params={"page": 1, "session": str(uuid.uuid4())},
            timeout=self.timeout,
        ).json()
        payload = data.get("data")
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return payload.get("produtos", []) or []
        return []

    def _codigos(self, item: dict[str, Any]) -> set[str]:
        codigos = {clean_ean(item.get(c)) for c in self.CAMPOS_EAN}
        codigos.add(clean_ean(item.get("sku")))
        return {c for c in codigos if len(c) >= 8}

    @staticmethod
    def _preco(item: dict[str, Any]) -> float | None:
        if not item.get("disponivel", True):
            return None
        try:
            preco = float(item.get("preco"))
        except (TypeError, ValueError):
            return None
        return preco if preco > 0 else None

    def por_ean(self, ean: str, itens: list[dict[str, Any]]) -> Achado | None:
        melhor: Achado | None = None
        for item in itens:
            if not isinstance(item, dict) or ean not in self._codigos(item):
                continue
            preco = self._preco(item)
            if preco is None:
                continue
            achado = Achado(
                nome=item.get("descricao", ""),
                preco=preco,
                score=SCORE_EAN,
                loja=self.loja,
                via="ean",
            )
            if melhor is None or achado.preco < melhor.preco:
                melhor = achado
        return melhor

    def por_texto(
        self,
        descricao: str,
        itens: list[dict[str, Any]],
        *,
        min_score: float,
        relaxed: bool,
    ) -> Achado | None:
        melhor: Achado | None = None
        for item in itens:
            if not isinstance(item, dict):
                continue
            preco = self._preco(item)
            if preco is None:
                continue
            nome = item.get("descricao", "")
            score = score_match(descricao, nome, relaxed=relaxed)
            if score < min_score:
                continue
            if not _texto_aceitavel(descricao, nome, relaxed=relaxed):
                continue
            achado = Achado(nome, preco, score, self.loja, "texto")
            if melhor is None or achado.score > melhor.score:
                melhor = achado
        return melhor

    def buscar(
        self,
        ean: str,
        descricao: str,
        *,
        min_score: float,
        relaxed: bool,
    ) -> Achado | None:
        if ean:
            itens = self.buscar_bruto(ean)
            achado = self.por_ean(ean, itens)
            if achado:
                return achado
            # Busca por EAN que devolve 1 produto é sinal forte mesmo sem o
            # código no payload — exige só que marca/medida não conflitem.
            if len(itens) == 1 and isinstance(itens[0], dict):
                preco = self._preco(itens[0])
                nome = itens[0].get("descricao", "")
                if preco and fully_compatible(descricao or ean, nome):
                    return Achado(nome, preco, SCORE_EAN, self.loja, "ean")
        if not descricao:
            return None
        juntos: dict[str, dict[str, Any]] = {}
        for consulta in search_queries(descricao):
            for item in self.buscar_bruto(consulta):
                if isinstance(item, dict):
                    juntos[str(item.get("produto_id"))] = item
        return self.por_texto(
            descricao, list(juntos.values()), min_score=min_score, relaxed=relaxed
        )


# --------------------------------------------------------------------------- #
# VTEX
# --------------------------------------------------------------------------- #

class ClienteVtex:
    """Supernosso, Lojas Americanas, Atacadão.

    `ft=<EAN>` devolve zero resultado nas três lojas — o passe por código de
    barras da versão anterior era requisição jogada fora e todo match caía no
    texto. O filtro correto é `fq=alternateIds_Ean:<EAN>`, que resolve
    Americanas e Atacadão. Supernosso não indexa EAN nesse campo e continua
    dependendo do texto (registrado como limitação conhecida).
    """

    def __init__(
        self,
        loja: str,
        base_url: str,
        sessao: requests.Session,
        *,
        timeout: int = 30,
    ):
        self.loja = loja
        self.base_url = base_url.rstrip("/")
        self.sessao = sessao
        self.timeout = timeout

    def _get(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        resp = self.sessao.get(
            f"{self.base_url}/api/catalog_system/pub/products/search",
            params=params,
            timeout=self.timeout,
        )
        if resp.status_code not in (200, 206):
            return []
        try:
            data = resp.json()
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    def buscar_por_texto(self, termo: str) -> list[dict[str, Any]]:
        return self._get({"ft": termo, "_from": 0, "_to": 19})

    def buscar_por_ean(self, ean: str) -> list[dict[str, Any]]:
        return self._get({"fq": f"alternateIds_Ean:{ean}", "_from": 0, "_to": 19})

    @staticmethod
    def _codigos(item: dict[str, Any]) -> set[str]:
        codigos = {clean_ean(item.get("productReference")), clean_ean(item.get("RefId"))}
        for sku in item.get("items", []) or []:
            codigos.add(clean_ean(sku.get("ean")))
            ref = sku.get("referenceId")
            if isinstance(ref, list):
                for r in ref:
                    if isinstance(r, dict):
                        codigos.add(clean_ean(r.get("Value")))
            elif isinstance(ref, str):
                codigos.add(clean_ean(ref))
        return {c for c in codigos if len(c) >= 8}

    @staticmethod
    def _preco(item: dict[str, Any]) -> float | None:
        """Menor preço disponível entre os SKUs/sellers do produto."""
        melhor: float | None = None
        for sku in item.get("items", []) or []:
            for seller in sku.get("sellers", []) or []:
                oferta = seller.get("commertialOffer") or {}
                if oferta.get("IsAvailable") is False:
                    continue
                try:
                    preco = float(oferta.get("Price"))
                except (TypeError, ValueError):
                    continue
                if preco <= 0:
                    continue
                if melhor is None or preco < melhor:
                    melhor = preco
        return melhor

    @staticmethod
    def _nome(item: dict[str, Any]) -> str:
        return item.get("productName") or item.get("productTitle") or ""

    def por_ean(self, ean: str, itens: list[dict[str, Any]]) -> Achado | None:
        for item in itens:
            if not isinstance(item, dict) or ean not in self._codigos(item):
                continue
            preco = self._preco(item)
            if preco is None:
                continue
            return Achado(self._nome(item), preco, SCORE_EAN, self.loja, "ean")
        return None

    def por_texto(
        self,
        descricao: str,
        itens: list[dict[str, Any]],
        *,
        min_score: float,
        relaxed: bool,
    ) -> Achado | None:
        melhor: Achado | None = None
        for item in itens:
            if not isinstance(item, dict):
                continue
            preco = self._preco(item)
            if preco is None:
                continue
            nome = self._nome(item)
            score = score_match(descricao, nome, relaxed=relaxed)
            if score < min_score:
                continue
            if not _texto_aceitavel(descricao, nome, relaxed=relaxed):
                continue
            achado = Achado(nome, preco, score, self.loja, "texto")
            if melhor is None or achado.score > melhor.score:
                melhor = achado
        return melhor

    def buscar(
        self,
        ean: str,
        descricao: str,
        *,
        min_score: float,
        relaxed: bool,
    ) -> Achado | None:
        if ean:
            itens = self.buscar_por_ean(ean)
            achado = self.por_ean(ean, itens)
            if achado:
                return achado
            if len(itens) == 1:
                preco = self._preco(itens[0])
                nome = self._nome(itens[0])
                if preco and fully_compatible(descricao or ean, nome):
                    return Achado(nome, preco, SCORE_EAN, self.loja, "ean")
        if not descricao:
            return None
        juntos: dict[str, dict[str, Any]] = {}
        for token in vtex_query_tokens(descricao):
            for item in self.buscar_por_texto(token.lower()):
                if isinstance(item, dict):
                    juntos[str(item.get("productId") or self._nome(item))] = item
        return self.por_texto(
            descricao, list(juntos.values()), min_score=min_score, relaxed=relaxed
        )


# --------------------------------------------------------------------------- #
# Estoque local (PAULO)
# --------------------------------------------------------------------------- #

class ArquivoDeEstoqueInvalido(RuntimeError):
    """O arquivo existe mas não é uma planilha de estoque utilizável."""


class ClienteEstoque:
    """Preço da própria loja, lido do relatório de planograma.

    Aceita **CSV e XLSX**. A versão anterior só lia CSV e, quando apontada
    para um .xlsx, tentava decodificar um arquivo ZIP como texto: dava
    UnicodeDecodeError no meio do processamento e derrubava a execução inteira.
    Pior: com fallback de codificação ela pararia de estourar e passaria a ler
    lixo binário como se fossem produtos — erro silencioso, que é o pior tipo
    aqui.

    Resolve em cascata: EAN → código do produto → ID → nome (fuzzy com todas
    as barreiras de compatibilidade). Não faz rede.
    """

    # Nomes que o relatório pode usar para cada campo.
    APELIDOS = {
        "preco": ("preco", "preço", "valor", "preco venda", "preço de venda"),
        "categoria": ("categoria produto", "categoria", "secao", "seção"),
        "descricao": ("descricao produto", "descrição produto", "descricao",
                      "descrição", "produto", "nome"),
        "ean": ("codigo de barras", "código de barras", "ean", "gtin", "barras"),
        "codigo": ("codigo produto", "código produto", "codigo", "código", "sku"),
        "id": ("id produto", "id"),
    }

    def __init__(self, loja: str, caminho: Path | str):
        self.loja = loja
        self.caminho = Path(caminho)
        self.por_ean: dict[str, dict[str, str]] = {}
        self.por_codigo: dict[str, dict[str, str]] = {}
        self.por_id: dict[str, dict[str, str]] = {}
        self.por_descricao: dict[str, dict[str, str]] = {}
        self._carregar()

    def _linhas_do_arquivo(self) -> list[dict[str, Any]]:
        """Lê CSV ou XLSX. Recusa qualquer outra coisa em vez de adivinhar."""
        from .planilha import Planilha, abrir_texto

        sufixo = self.caminho.suffix.lower()
        if sufixo in {".xlsx", ".xlsm", ".xltx"}:
            planilha = Planilha.carregar(self.caminho, coluna_chave="Preço")
            return planilha.linhas
        if sufixo in {".csv", ".txt", ".tsv"}:
            with abrir_texto(self.caminho, newline="") as f:
                return list(csv.DictReader(f))
        raise ArquivoDeEstoqueInvalido(
            f"{self.caminho.name}: só sei ler CSV e XLSX. "
            "Se for .xls antigo, salve como .xlsx no Excel."
        )

    def _mapear(self, colunas: list[str]) -> dict[str, str]:
        """Casa os campos do estoque com os cabeçalhos que o arquivo tem."""
        from .planilha import _chave

        presentes = {_chave(c): c for c in colunas if str(c).strip()}
        mapa: dict[str, str] = {}
        for campo, apelidos in self.APELIDOS.items():
            for apelido in apelidos:
                chave = _chave(apelido)
                if chave in presentes:
                    mapa[campo] = presentes[chave]
                    break
        return mapa

    def _carregar(self) -> None:
        # Caminho vazio no config resolve para a pasta do programa; pasta não
        # é planilha, então is_file() e não exists().
        if not self.caminho.is_file():
            return
        linhas = self._linhas_do_arquivo()
        if not linhas:
            raise ArquivoDeEstoqueInvalido(f"{self.caminho.name} está vazio")

        mapa = self._mapear(list(linhas[0].keys()))
        # Sem preço não há o que aproveitar; sem nenhuma forma de identificar o
        # produto, também não. Falar isso alto é melhor que devolver 0 preços
        # e deixar a coluna vazia sem explicação.
        if "preco" not in mapa:
            raise ArquivoDeEstoqueInvalido(
                f"{self.caminho.name}: não achei uma coluna de preço. "
                f"Colunas encontradas: {', '.join(list(linhas[0].keys())[:8])}"
            )
        if not ({"ean", "codigo", "id", "descricao"} & set(mapa)):
            raise ArquivoDeEstoqueInvalido(
                f"{self.caminho.name}: não achei código de barras, código do "
                "produto nem descrição — sem isso não dá para casar os produtos."
            )

        def campo(row: dict[str, Any], nome: str) -> str:
            coluna = mapa.get(nome)
            return str(row.get(coluna) or "").strip() if coluna else ""

        for row in linhas:
                info = {
                    "preco": campo(row, "preco"),
                    "categoria": campo(row, "categoria"),
                    "descricao": campo(row, "descricao"),
                }
                ean = clean_ean(campo(row, "ean"))
                codigo = campo(row, "codigo")
                pid = campo(row, "id")
                if ean:
                    self.por_ean[ean] = info
                if codigo:
                    self.por_codigo[codigo] = info
                    digitos = clean_ean(codigo)
                    if len(digitos) >= 8:
                        self.por_ean.setdefault(digitos, info)
                if pid:
                    self.por_id[pid] = info
                chave = normalize(info["descricao"])
                if chave:
                    self.por_descricao[chave] = info

    def categorias_por_ean(self) -> dict[str, str]:
        return {
            ean: info["categoria"]
            for ean, info in self.por_ean.items()
            if info.get("categoria")
        }

    def _por_nome(self, descricao: str) -> tuple[float | None, float]:
        from rapidfuzz import fuzz, process

        if not descricao or not self.por_descricao:
            return None, 0.0
        consulta = normalize(descricao)
        if not consulta:
            return None, 0.0
        if consulta in self.por_descricao:
            return parse_price_br(self.por_descricao[consulta].get("preco")), 100.0

        melhor_score = 0.0
        for chave, score, _ in process.extract(
            consulta, self.por_descricao.keys(), scorer=fuzz.token_set_ratio, limit=8
        ):
            melhor_score = max(melhor_score, float(score))
            if score < 85:
                continue
            candidato = self.por_descricao[chave]
            nome = candidato.get("descricao") or ""
            if not fully_compatible(descricao, nome):
                continue
            if score < 90 and fuzz.token_sort_ratio(consulta, chave) < 65:
                continue
            preco = parse_price_br(candidato.get("preco"))
            if preco is None:
                continue
            return preco, float(score)
        return None, melhor_score

    def buscar(
        self,
        ean: str,
        descricao: str,
        *,
        codigo_interno: str = "",
        min_score: float = 0,
        relaxed: bool = False,
    ) -> Achado | None:
        for chave, tabela in (
            (ean, self.por_ean),
            (codigo_interno, self.por_codigo),
            (codigo_interno, self.por_id),
        ):
            if chave and chave in tabela:
                info = tabela[chave]
                preco = parse_price_br(info.get("preco"))
                if preco is not None:
                    return Achado(
                        info.get("descricao", ""), preco, SCORE_EAN, self.loja, "ean"
                    )
        preco, score = self._por_nome(descricao)
        if preco is None:
            return None
        return Achado(descricao, preco, score, self.loja, "texto")


# --------------------------------------------------------------------------- #
# Araújo (Salesforce Commerce Cloud / Demandware)
# --------------------------------------------------------------------------- #

class ClienteAraujo:
    """Araújo — busca por HTTP, sem navegador.

    O endpoint `Search-UpdateGrid` devolve HTML, não JSON, e está atrás de um
    WAF. A investigação mostrou que:

    * sem os cabeçalhos de navegador (`Accept`, `Accept-Language`, `Referer`,
      `X-Requested-With`) a resposta é 403 — só `User-Agent` não basta;
    * com uma `Session` reutilizada e ~1s entre requisições ele responde 200
      consistentemente; rajada sem sessão toma 403;
    * o 403 é limite de taxa, **não** "produto inexistente" — tratá-lo como
      resposta vazia gravaria "não encontrado" em massa. Por isso ele entra em
      espera progressiva e tenta de novo.

    Isso dispensa Playwright: o Chromium headless resolveria o mesmo problema
    custando ~150 MB no instalador. O modo navegador existe como reserva, para
    o caso de o WAF apertar — ver `usar_navegador`.

    Bônus: a URL da imagem do produto carrega o EAN
    (`.../26230/07894900010015_4.webp`), então aqui também dá para casar por
    código de barras em vez de depender de texto.
    """

    BASE = "https://www.araujo.com.br"
    CAMINHO = "/on/demandware.store/Sites-Araujo-Site/pt_BR/Search-UpdateGrid"

    CABECALHOS = {
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Referer": "https://www.araujo.com.br/",
        "X-Requested-With": "XMLHttpRequest",
    }

    # Um tile de produto começa em data-pid e vai até o próximo.
    RE_TILE = re.compile(r'data-pid="(\d+)"')
    RE_NOME = re.compile(r"<h3>\s*([^<]+?)\s*</h3>", re.I)
    RE_NOME_ALT = re.compile(r'class="productTile__name"[^>]*title="([^"]+)"', re.I)
    RE_PRECO = re.compile(
        r'class="productPrice__price"[^>]*>\s*(?:<[^>]+>\s*)*R\$\s*([\d.,]+)', re.I
    )
    RE_EAN_IMG = re.compile(r"/\d{4,7}/(\d{12,14})_\d+\.(?:webp|jpg|jpeg|png)", re.I)

    # `tls_navegador` vem primeiro porque foi medido funcionando nas duas
    # redes testadas (200/200), enquanto `padrao` funciona só em uma (403 no
    # Windows do cliente, 200 no Linux do desenvolvedor). Começar pelo que
    # falha custava duas requisições negadas e ~4s de recuo antes de trocar.
    # `padrao` fica logo atrás como reserva: se o OpenSSL local recusar a
    # lista de cifras, ainda há um caminho.
    # Os dois primeiros usam só a biblioteca padrão e chegam por atualização
    # de payload; os outros dois são dependência binária e exigem regerar o
    # executável.
    TRANSPORTES = ("tls_navegador", "padrao", "curl_cffi", "navegador")

    def __init__(
        self,
        loja: str,
        sessao: requests.Session,
        *,
        timeout: int = 30,
        pausa: float = 1.2,
        usar_navegador: bool = False,
        transporte: str = "auto",
    ):
        self.loja = loja
        self.sessao = sessao
        self.timeout = timeout
        self.pausa = pausa
        self.usar_navegador = usar_navegador
        self._ultima_requisicao = 0.0
        self._espera_extra = 0.0
        self._navegador: Any = None

        if usar_navegador:
            transporte = "navegador"
        self._auto = transporte == "auto"
        self.transporte = self.TRANSPORTES[0] if self._auto else transporte
        self._transportes_tentados: list[str] = []
        self._sessao_alternativa: Any = None
        self.trocas_de_transporte: list[str] = []

    # -- escolha de transporte -------------------------------------------- #

    def _proximo_transporte(self) -> bool:
        """Sobe para o transporte seguinte. False quando acabaram as opções.

        O WAF do Araújo recusa cliente Python em algumas redes e aceita em
        outras (403 no Windows do cliente, 200 no Linux do desenvolvedor, com
        cabeçalhos e ritmo idênticos). Como não há como saber de antemão, o
        programa tenta em ordem em vez de exigir configuração manual.
        """
        if not self._auto:
            return False
        indice = self.TRANSPORTES.index(self.transporte)
        for candidato in self.TRANSPORTES[indice + 1:]:
            if candidato == "curl_cffi" and sessao_curl_cffi() is None:
                continue
            if candidato == "navegador":
                try:
                    import playwright  # noqa: F401
                except ImportError:
                    continue
            self.transporte = candidato
            self._sessao_alternativa = None
            self._espera_extra = 0.0
            self.pausa = min(self.pausa, 2.0)
            self.trocas_de_transporte.append(candidato)
            return True
        return False

    def _obter_sessao(self) -> Any:
        if self.transporte == "padrao":
            return self.sessao
        if self._sessao_alternativa is None:
            if self.transporte == "tls_navegador":
                self._sessao_alternativa = sessao_tls_navegador()
            elif self.transporte == "curl_cffi":
                self._sessao_alternativa = sessao_curl_cffi()
        # Transporte que não conseguiu montar sessão não pode travar a busca:
        # cai para o seguinte.
        if self._sessao_alternativa is None and self._proximo_transporte():
            return self._obter_sessao()
        return self._sessao_alternativa

    # Hipótese testada e descartada: visitar a home antes de buscar não
    # estabelece sessão. A home responde 403 e não devolve cookie nenhum
    # (medido pelo diagnostico.py), então a visita só gastava uma requisição
    # negada — que ainda pode contar contra a reputação do IP no WAF.
    # O que comprovadamente importa são os cabeçalhos de CABECALHOS.

    # -- transporte ------------------------------------------------------- #

    def _respeitar_ritmo(self) -> None:
        alvo = self.pausa + self._espera_extra
        decorrido = time.monotonic() - self._ultima_requisicao
        if decorrido < alvo:
            time.sleep(alvo - decorrido)
        self._ultima_requisicao = time.monotonic()

    def buscar_html(self, termo: str, *, tentativas: int = 4) -> str:
        """HTML da grade de resultados. 403 vira espera progressiva."""
        if self.transporte == "navegador":
            return self._buscar_html_navegador(termo)
        params = {"q": termo, "start": 0, "sz": 12}
        negados = 0
        for tentativa in range(tentativas):
            self._respeitar_ritmo()
            sessao = self._obter_sessao()
            if sessao is None:
                raise RuntimeError(f"transporte {self.transporte} indisponível")
            resp = sessao.get(
                f"{self.BASE}{self.CAMINHO}",
                params=params,
                headers=self.CABECALHOS,
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                # Voltou a responder: alivia a espera acumulada aos poucos.
                self._espera_extra = max(0.0, self._espera_extra / 2)
                return resp.text
            if resp.status_code in (403, 429, 503):
                negados += 1
                # 403 imediato e repetido não é ritmo: é o cliente sendo
                # recusado. Esperar mais não resolve — trocar de transporte,
                # sim. Só insiste no ritmo em 429/503, que é limite de taxa
                # de verdade.
                if negados >= 2 and resp.status_code == 403:
                    if self._proximo_transporte():
                        negados = 0
                        continue
                self._espera_extra = min(20.0, max(2.0, self._espera_extra * 2 or 2.0))
                self.pausa = min(8.0, self.pausa * 1.5)
                time.sleep(min(30.0, 4.0 * (2 ** tentativa)))
                continue
            resp.raise_for_status()
        if self._proximo_transporte():
            return self.buscar_html(termo, tentativas=tentativas)
        raise LimiteDeTaxa(self.loja, termo, espera_sugerida=90.0)

    def _buscar_html_navegador(self, termo: str) -> str:
        """Reserva: Chromium headless, se o WAF passar a bloquear o HTTP.

        Só é usado com `usar_navegador: true` no config e Playwright instalado
        (`pip install playwright && python -m playwright install chromium`).
        """
        if self._navegador is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "modo navegador pedido mas Playwright não está instalado"
                ) from exc
            self._pw = sync_playwright().start()
            # channel="msedge" usa o Edge que já vem no Windows: dispensa
            # baixar os ~150 MB do Chromium do Playwright.
            for canal in ("msedge", "chrome", None):
                try:
                    self._navegador = (
                        self._pw.chromium.launch(headless=True, channel=canal)
                        if canal else self._pw.chromium.launch(headless=True)
                    )
                    break
                except Exception:
                    continue
            if self._navegador is None:
                raise RuntimeError("nenhum navegador disponível para o modo reserva")
            self._pagina = self._navegador.new_page()
            # Uma visita à home estabelece os cookies de sessão.
            self._pagina.goto(self.BASE, wait_until="domcontentloaded", timeout=60000)
        self._respeitar_ritmo()
        url = f"{self.BASE}{self.CAMINHO}?q={quote(termo)}&start=0&sz=12"
        return self._pagina.evaluate(
            "async (u) => (await fetch(u, {credentials: 'include'})).text()", url
        )

    def fechar(self) -> None:
        if self._navegador is not None:  # pragma: no cover
            try:
                self._navegador.close()
                self._pw.stop()
            finally:
                self._navegador = None

    # -- parsing ---------------------------------------------------------- #

    @classmethod
    def extrair_produtos(cls, html: str) -> list[dict[str, Any]]:
        """Divide a grade em tiles e extrai nome, preço e EAN de cada um."""
        marcas = list(cls.RE_TILE.finditer(html))
        produtos: list[dict[str, Any]] = []
        vistos: set[str] = set()
        for i, marca in enumerate(marcas):
            pid = marca.group(1)
            if pid in vistos:
                continue
            fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(html)
            bloco = html[marca.start():fim]

            m_preco = cls.RE_PRECO.search(bloco)
            if not m_preco:
                continue
            m_nome = cls.RE_NOME.search(bloco) or cls.RE_NOME_ALT.search(bloco)
            if not m_nome:
                continue
            m_ean = cls.RE_EAN_IMG.search(bloco)

            vistos.add(pid)
            produtos.append({
                "pid": pid,
                "nome": unescape(m_nome.group(1)).strip(),
                "preco": parse_price_br(m_preco.group(1)),
                # O EAN vem com zero à esquerda na URL da imagem.
                "ean": m_ean.group(1).lstrip("0") if m_ean else "",
            })
        return produtos

    # -- busca ------------------------------------------------------------ #

    def buscar(
        self,
        ean: str,
        descricao: str,
        *,
        min_score: float,
        relaxed: bool,
    ) -> Achado | None:
        produtos: list[dict[str, Any]] = []
        if ean:
            produtos = self.extrair_produtos(self.buscar_html(ean))
            alvo = ean.lstrip("0")
            for p in produtos:
                if p["ean"] and p["ean"] == alvo and p["preco"]:
                    return Achado(p["nome"], p["preco"], SCORE_EAN, self.loja, "ean")
            # Busca por EAN com resultado único: aceita se nada conflitar.
            if len(produtos) == 1 and produtos[0]["preco"]:
                p = produtos[0]
                if fully_compatible(descricao or ean, p["nome"]):
                    return Achado(p["nome"], p["preco"], SCORE_EAN, self.loja, "ean")

        if not produtos and descricao:
            termos = [
                t for t in normalize(descricao).split() if len(t) >= 4
            ][:3]
            if termos:
                produtos = self.extrair_produtos(self.buscar_html(" ".join(termos)))

        if not descricao:
            return None

        melhor: Achado | None = None
        for p in produtos:
            if not p["preco"]:
                continue
            score = score_match(descricao, p["nome"], relaxed=relaxed)
            if score < min_score:
                continue
            if not _texto_aceitavel(descricao, p["nome"], relaxed=relaxed):
                continue
            achado = Achado(p["nome"], p["preco"], score, self.loja, "texto")
            if melhor is None or achado.score > melhor.score:
                melhor = achado
        return melhor
