"""Orquestração da busca.

Cada loja roda em sua própria thread e percorre todas as linhas em sequência.
Assim as 5 lojas de API avançam em paralelo, mas nenhuma delas dispara
requisições concorrentes contra si mesma — o que manteria o ritmo educado que
os `sleep` originais garantiam, sem pagar o custo somado deles.

O progresso é reportado por callback e a execução é interrompível, porque a
Fase 2 (interface) precisa das duas coisas: barra de progresso e botão
cancelar.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from .cache import CachePrecos, chave_produto
from .config import Config, Loja
from .lojas import Achado, ClienteAraujo, ClienteEstoque, ClienteVip, ClienteVtex
from .precos import clean_ean, is_valid_ean
from .rede import Disjuntor, LimiteDeTaxa, nova_sessao
from .texto import multiplicador_de_pacote


# Quantas consultas sem nenhum acerto bastam para desconfiar do cadastro.
LIMITE_AVISO_LOJA_VAZIA = 20


@dataclass
class Progresso:
    loja: str
    feitas: int
    total: int
    preenchidos: int


@dataclass
class Resultado:
    preenchidos: int = 0
    do_cache: int = 0
    por_ean: int = 0
    por_texto: int = 0
    nao_encontrados: int = 0
    erros: int = 0
    esperas_por_limite: int = 0
    lojas_desligadas: dict[str, str] = field(default_factory=dict)
    preenchidos_por_loja: dict[str, int] = field(default_factory=dict)
    cancelado: bool = False

    def lojas_vazias(self) -> list[str]:
        """Lojas que foram consultadas e não preencheram nada.

        Quase sempre é cadastro errado (endereço ou plataforma) ou o site
        mudou. Sem isso o cliente só descobre olhando a coluna vazia na
        planilha depois de meia hora de busca.
        """
        return sorted(loja for loja, n in self.preenchidos_por_loja.items() if n == 0)

    def resumo(self) -> str:
        partes = [
            f"{self.preenchidos} preços preenchidos",
            f"{self.por_ean} por código de barras",
            f"{self.por_texto} por texto",
            f"{self.nao_encontrados} não encontrados",
        ]
        if self.do_cache:
            partes.append(f"{self.do_cache} reaproveitados do cache")
        if self.esperas_por_limite:
            partes.append(f"{self.esperas_por_limite} pausas por limite de taxa")
        if self.erros:
            partes.append(f"{self.erros} erros")
        if self.lojas_desligadas:
            partes.append(
                "lojas desligadas: " + ", ".join(sorted(self.lojas_desligadas))
            )
        vazias = self.lojas_vazias()
        if vazias:
            partes.append("sem nenhum preço: " + ", ".join(vazias))
        if self.cancelado:
            partes.append("INTERROMPIDO pelo usuário")
        return "; ".join(partes)


class Buscador:
    def __init__(
        self,
        cfg: Config,
        *,
        cache: CachePrecos | None = None,
        log: dict[str, Any] | None = None,
        ao_progredir: Callable[[Progresso], None] | None = None,
        ao_avisar: Callable[[str, str], None] | None = None,
        cancelar: threading.Event | None = None,
    ):
        self.cfg = cfg
        self.cache = cache
        self.log = log if log is not None else {"cep": cfg.cep, "produtos": {}}
        self.ao_progredir = ao_progredir
        self.ao_avisar = ao_avisar
        self.cancelar = cancelar or threading.Event()
        self.sessao = nova_sessao(int(cfg.busca.get("tentativas", 3)))
        self.disjuntor = Disjuntor(int(cfg.busca.get("falhas_seguidas_para_desistir", 8)))
        self._lock = threading.Lock()
        self._resultado = Resultado()
        self._fechaveis: list[Any] = []

    # ------------------------------------------------------------------ #

    def _criar_cliente(self, loja: Loja) -> Any:
        timeout = int(self.cfg.busca.get("timeout_seg", 30))
        if loja.tipo == "vip":
            return ClienteVip(loja.nome, loja.endereco, self.sessao, timeout=timeout)
        if loja.tipo == "vtex":
            return ClienteVtex(loja.nome, loja.endereco, self.sessao, timeout=timeout)
        if loja.tipo == "araujo":
            # Sessão própria: o Araújo tem WAF e ritmo mais lento, não deve
            # dividir pool de conexão nem cookies com as APIs JSON.
            cliente = ClienteAraujo(
                loja.nome,
                nova_sessao(int(self.cfg.busca.get("tentativas", 3))),
                timeout=timeout,
                pausa=float(self.cfg.busca.get("pausa_araujo_seg", 1.2)),
                usar_navegador=bool(self.cfg.busca.get("araujo_usar_navegador", False)),
                transporte=str(self.cfg.busca.get("araujo_transporte", "auto")),
            )
            with self._lock:
                self._fechaveis.append(cliente)
            return cliente
        raise ValueError(f"tipo de loja sem cliente de rede: {loja.tipo}")

    def _avisar(self, texto: str, nivel: str = "aviso") -> None:
        if self.ao_avisar is not None:
            try:
                self.ao_avisar(texto, nivel)
            except Exception:
                pass

    def _registrar(self, rotulo: str, loja: str, dados: dict[str, Any]) -> None:
        with self._lock:
            entrada = self.log.setdefault("produtos", {}).setdefault(
                rotulo, {"matches": {}}
            )
            entrada["matches"][loja] = dados

    # ------------------------------------------------------------------ #

    def aplicar_estoque(self, linhas: list[dict[str, Any]]) -> int:
        """Coluna da própria loja (PAULO) — lookup local, sem rede."""
        loja = self.cfg.loja_estoque()
        if loja is None:
            return 0
        cliente = ClienteEstoque(loja.nome, self.cfg.caminho("estoque"))
        col_ean = self.cfg.colunas["ean"]
        col_desc = self.cfg.colunas["descricao"]
        col_cod = self.cfg.colunas.get("codigo_interno", "Código Interno")
        preenchidos = 0
        for linha in linhas:
            achado = cliente.buscar(
                clean_ean(linha.get(col_ean)),
                linha.get(col_desc) or "",
                codigo_interno=str(linha.get(col_cod) or "").strip(),
            )
            linha[loja.nome] = achado.preco_formatado() if achado else ""
            if achado:
                preenchidos += 1
        return preenchidos

    def categorias_estoque(self) -> dict[str, str]:
        loja = self.cfg.loja_estoque()
        if loja is None:
            return {}
        return ClienteEstoque(loja.nome, self.cfg.caminho("estoque")).categorias_por_ean()

    # ------------------------------------------------------------------ #

    def _processar_loja(
        self,
        loja: Loja,
        linhas: list[dict[str, Any]],
        *,
        somente_vazias: bool,
        min_score: float,
        relaxed: bool,
        passe: int,
    ) -> None:
        try:
            cliente = self._criar_cliente(loja)
        except Exception as exc:  # autenticação/bootstrap falhou: loja fora
            self.disjuntor.desligar(loja.nome, exc)
            with self._lock:
                self._resultado.erros += 1
            return

        col_ean = self.cfg.colunas["ean"]
        col_desc = self.cfg.colunas["descricao"]
        pausa = float(self.cfg.busca.get("pausa_entre_itens_seg", 0.08))
        total = len(linhas)
        feitas = 0
        preenchidos_loja = 0
        consultadas = 0        # itens que realmente foram perguntados à loja
        ja_avisou_vazia = False

        for linha in linhas:
            if self.cancelar.is_set():
                with self._lock:
                    self._resultado.cancelado = True
                return
            feitas += 1

            descricao = str(linha.get(col_desc) or "").strip()
            ean = clean_ean(linha.get(col_ean)) if is_valid_ean(linha.get(col_ean)) else ""
            if not descricao and not ean:
                continue
            if somente_vazias and str(linha.get(loja.nome) or "").strip():
                continue
            if self.disjuntor.aberto(loja.nome):
                continue

            rotulo = descricao or ean
            chave = chave_produto(ean, descricao)
            consultadas += 1

            # Aviso precoce: loja que não preencheu nada nas primeiras consultas
            # quase certamente está com cadastro errado. Falar em ~1 minuto é
            # melhor que o cliente descobrir no fim de meia hora.
            if (
                not ja_avisou_vazia
                and consultadas >= LIMITE_AVISO_LOJA_VAZIA
                and preenchidos_loja == 0
            ):
                ja_avisou_vazia = True
                self._avisar(
                    f"{loja.nome} não achou nenhum preço nos primeiros "
                    f"{consultadas} produtos. Confira o endereço e a plataforma "
                    "em 'Cadastrar lojas…' — a busca continua nas outras."
                )

            # 1) cache
            if self.cache is not None:
                registro = self.cache.buscar(loja.nome, chave)
                if registro is not None:
                    if registro["preco"]:
                        linha[loja.nome] = registro["preco"]
                        preenchidos_loja += 1
                        with self._lock:
                            self._resultado.preenchidos += 1
                            self._resultado.do_cache += 1
                    else:
                        with self._lock:
                            self._resultado.nao_encontrados += 1
                    continue

            # 2) rede
            achado: Achado | None = None
            falhou = False
            # Limite de taxa não é defeito: espera e tenta de novo o mesmo
            # item. Só desiste dele depois de insistir, e sem desligar a loja.
            for tentativa in range(3):
                try:
                    achado = cliente.buscar(
                        ean, descricao, min_score=min_score, relaxed=relaxed
                    )
                    self.disjuntor.registrar_sucesso(loja.nome)
                    break
                except LimiteDeTaxa as exc:
                    with self._lock:
                        self._resultado.esperas_por_limite += 1
                    if self.ao_progredir:
                        self.ao_progredir(Progresso(
                            f"{loja.nome} (aguardando)", feitas, total, preenchidos_loja
                        ))
                    espera = exc.espera_sugerida * (tentativa + 1)
                    if self.cancelar.wait(timeout=espera):
                        with self._lock:
                            self._resultado.cancelado = True
                        return
                    continue
                except Exception as exc:
                    self.disjuntor.registrar_falha(loja.nome, exc)
                    self._registrar(rotulo, loja.nome, {
                        "preco": str(linha.get(loja.nome) or "") or None,
                        "erro": f"{type(exc).__name__}: {exc}"[:200],
                        "passe": passe,
                    })
                    with self._lock:
                        self._resultado.erros += 1
                    falhou = True
                    break
            if falhou:
                continue
            if achado is None and self._resultado.esperas_por_limite and not ean:
                # Esgotou as esperas: registra e segue, sem matar a loja.
                self._registrar(rotulo, loja.nome, {
                    "preco": None, "motivo": "limite de taxa", "passe": passe,
                })
                continue

            if achado:
                preco = achado.preco_formatado()
                linha[loja.nome] = preco
                preenchidos_loja += 1
                # Match exato por EAN cujo nome declara embalagem múltipla:
                # algumas lojas cadastram o kit sob o código da unidade
                # (Carrefour, "Kit 2 Biscoito Oreo" a R$ 7,78 no EAN do 90g).
                # Rejeitar seria pior — Supernosso devolve "Caixa com 12" por
                # EAN com preço unitário correto. Então sinaliza para conferir.
                multi = multiplicador_de_pacote(achado.nome)
                if multi and not multiplicador_de_pacote(descricao):
                    linha.setdefault("__avisos__", []).append(
                        f"{loja.nome} pode ser embalagem de {multi}"
                    )
                self._registrar(rotulo, loja.nome, {
                    "preco": preco,
                    "nome": achado.nome,
                    "score": round(achado.score, 1),
                    "via": achado.via,
                    "passe": passe,
                })
                if self.cache is not None:
                    self.cache.gravar(
                        loja.nome, chave,
                        preco=preco, nome=achado.nome,
                        score=achado.score, via=achado.via,
                    )
                with self._lock:
                    self._resultado.preenchidos += 1
                    if achado.via == "ean":
                        self._resultado.por_ean += 1
                    else:
                        self._resultado.por_texto += 1
            else:
                self._registrar(rotulo, loja.nome, {
                    "preco": None,
                    "motivo": "não encontrado",
                    "passe": passe,
                })
                if self.cache is not None:
                    self.cache.gravar(loja.nome, chave, preco=None)
                with self._lock:
                    self._resultado.nao_encontrados += 1

            if self.ao_progredir and feitas % 5 == 0:
                self.ao_progredir(Progresso(loja.nome, feitas, total, preenchidos_loja))
            time.sleep(pausa)

        with self._lock:
            self._resultado.preenchidos_por_loja[loja.nome] = preenchidos_loja
        if consultadas and preenchidos_loja == 0:
            self._avisar(
                f"{loja.nome} terminou sem nenhum preço em {consultadas} "
                "consultas. Isso costuma ser cadastro errado ou mudança no site."
            )
        if self.ao_progredir:
            self.ao_progredir(Progresso(loja.nome, total, total, preenchidos_loja))

    # ------------------------------------------------------------------ #

    def executar(
        self,
        linhas: list[dict[str, Any]],
        *,
        somente_vazias: bool = False,
        passe: int = 1,
    ) -> Resultado:
        chave_score = "min_score_passe2" if passe == 2 else "min_score_passe1"
        min_score = float(self.cfg.busca.get(chave_score, 68))
        relaxed = passe == 2

        lojas = self.cfg.lojas_para_buscar()
        paralelas = max(1, int(self.cfg.busca.get("lojas_em_paralelo", 5)))

        with ThreadPoolExecutor(max_workers=min(paralelas, len(lojas) or 1)) as pool:
            futuros = [
                pool.submit(
                    self._processar_loja,
                    loja,
                    linhas,
                    somente_vazias=somente_vazias,
                    min_score=min_score,
                    relaxed=relaxed,
                    passe=passe,
                )
                for loja in lojas
            ]
            for f in futuros:
                f.result()

        self._resultado.lojas_desligadas = self.disjuntor.desligadas()
        return self._resultado

    def fechar(self) -> None:
        for cliente in self._fechaveis:
            try:
                cliente.fechar()
            except Exception:
                pass
        try:
            self.sessao.close()
        except Exception:
            pass
