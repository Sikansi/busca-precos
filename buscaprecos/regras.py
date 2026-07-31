"""Classificação de markup e cálculo das colunas derivadas."""

from __future__ import annotations

from typing import Any

from .config import Config
from .precos import clean_ean, fmt_pct, format_price, parse_markup, parse_price_br
from .texto import normalize

# Colunas que o programa calcula (as duas últimas são novas).
COLUNAS_DERIVADAS = [
    "MENOR PREÇO",
    "SUPERMERCADO MENOR",
    "MAIOR PREÇO",
    "SUPERMERCADO MAIOR",
    "MÉDIA 3 MAIORES",
    "MARKUP ALVO",
    "PREÇO PERTIN",
    "MARGEM",
    "REGRA",
    "QTD PREÇOS",
    "OBS BUSCA",
]


FATOR_DISCREPANTE = 3.0


def detectar_discrepantes(
    precos: list[tuple[str, float]], fator: float = FATOR_DISCREPANTE
) -> list[str]:
    """Lojas cujo preço se afasta demais da mediana das outras.

    Serve para o caso que nenhuma barreira de texto pega: quando a descrição
    da nota não declara gramatura ("DOCE PAÇOCA PAÇOQUITA"), nada impede o
    site devolver o preço da caixa fechada. Foi assim que Villefort entrou
    com R$ 41,98 numa paçoca de R$ 1 e Americanas com R$ 23,99 num Bis de
    R$ 9.

    Não corrige nada — só sinaliza em `OBS BUSCA`, porque decidir qual está
    certo exige olhar o produto. Precisa de pelo menos 3 preços para ter
    mediana com significado.
    """
    if len(precos) < 3:
        return []
    valores = sorted(v for _, v in precos)
    meio = len(valores) // 2
    mediana = (
        valores[meio] if len(valores) % 2
        else (valores[meio - 1] + valores[meio]) / 2
    )
    if mediana <= 0:
        return []
    return [
        loja for loja, v in precos
        if v > mediana * fator or v < mediana / fator
    ]


def classificar_por_heuristica(descricao: str, cfg: Config) -> str | None:
    """Primeira chave de categoria cuja palavra aparece na descrição."""
    texto = normalize(descricao)
    for chave, palavras in cfg.regras_categoria:
        for palavra in palavras:
            p = normalize(palavra)
            if p and p in texto:
                return chave
    return None


def classificar_markup(
    descricao: str,
    ean: str,
    categorias_estoque: dict[str, str],
    cfg: Config,
) -> str | None:
    """Categoria do estoque quando existe, com a heurística desempatando.

    A categoria do estoque é grossa ("BEBIDAS" cobre cerveja, vinho e
    refrigerante, que têm markup 75%, 96% e 90%), então quando a heurística
    é mais específica ela ganha.
    """
    heuristica = classificar_por_heuristica(descricao, cfg)
    if ean and ean in categorias_estoque:
        cat_norm = normalize(categorias_estoque[ean])
        for rotulo, chave in cfg.categoria_estoque_para_markup.items():
            if normalize(rotulo) != cat_norm:
                continue
            if chave == "BEBIDAS_NAO_ALCOOLICAS" and heuristica in (
                "CERVEJAS", "VINHOS", "BEBIDAS_NAO_ALCOOLICAS",
            ):
                return heuristica
            if chave in ("SALGADINHOS", "MERCEARIA") and heuristica in (
                "BARRINHAS_NUTS", "SALGADINHOS", "DOCES_IMPULSO", "MERCEARIA",
            ):
                return heuristica
            if heuristica and chave == "MERCEARIA" and heuristica != "MERCEARIA":
                return heuristica
            return chave
    return heuristica


def resolver_markup(
    linha: dict[str, Any],
    ean: str,
    categorias_estoque: dict[str, str],
    cfg: Config,
) -> float | None:
    """Markup da planilha tem prioridade; senão vem da categoria."""
    da_planilha = parse_markup(
        linha.get(cfg.colunas.get("markup_planilha", "MARKUP")) or linha.get("MARKUP ALVO")
    )
    if da_planilha is not None:
        return da_planilha
    chave = classificar_markup(
        linha.get(cfg.colunas["descricao"]) or "", ean, categorias_estoque, cfg
    )
    return cfg.markup_alvo.get(chave) if chave else None


def calcular_derivadas(
    linhas: list[dict[str, Any]],
    cfg: Config,
    categorias_estoque: dict[str, str] | None = None,
) -> int:
    """Preenche as colunas calculadas. Devolve quantas linhas foram tocadas.

    Sobre `MÉDIA 3 MAIORES`: com menos de 3 preços ela é a média do que
    existir. Isso não é erro, mas muda o significado — por isso `QTD PREÇOS`
    e `OBS BUSCA` passaram a existir, para dar para filtrar no Excel as
    linhas cuja `REGRA` saiu de amostra pequena.
    """
    categorias_estoque = categorias_estoque or {}
    col_desc = cfg.colunas["descricao"]
    col_ean = cfg.colunas["ean"]
    col_custo = cfg.colunas["custo"]
    cols_preco = cfg.colunas_de_preco()
    cols_stat = cfg.colunas_estatistica()
    minimo = int(cfg.estatisticas.get("minimo_precos_para_regra", 1))
    tolerancia = float(cfg.estatisticas.get("tolerancia_margem", 0.005))

    calculadas = 0
    for linha in linhas:
        # Normaliza o texto de todas as colunas de preço.
        for col in cols_preco:
            valor = parse_price_br(linha.get(col))
            if valor is not None:
                linha[col] = format_price(valor) or ""
            elif str(linha.get(col) or "").strip():
                linha[col] = ""

        precos = [
            (col, parse_price_br(linha.get(col)))
            for col in cols_stat
            if parse_price_br(linha.get(col)) is not None
        ]
        precos = [(c, v) for c, v in precos if v is not None]

        ean = clean_ean(linha.get(col_ean))
        markup = resolver_markup(linha, ean, categorias_estoque, cfg)
        linha["MARKUP ALVO"] = fmt_pct(markup) if markup is not None else ""

        custo = parse_price_br(linha.get(col_custo))
        if custo is not None:
            linha[col_custo] = format_price(custo) or linha[col_custo]

        if custo is not None and markup is not None:
            pertin = format_price(custo * (1 + markup)) or ""
        else:
            pertin = ""
        linha["PREÇO PERTIN"] = pertin
        if "PREÇO PERTIN Estranho" in linha:
            linha["PREÇO PERTIN Estranho"] = pertin

        linha["QTD PREÇOS"] = str(len(precos))
        avisos: list[str] = []

        if not precos:
            for col in (
                "MENOR PREÇO", "SUPERMERCADO MENOR", "MAIOR PREÇO",
                "SUPERMERCADO MAIOR", "MÉDIA 3 MAIORES", "MARGEM", "REGRA",
            ):
                linha[col] = ""
            linha["OBS BUSCA"] = "nenhum preço encontrado"
            if markup is not None:
                calculadas += 1
            continue

        loja_min, valor_min = min(precos, key=lambda x: x[1])
        loja_max, valor_max = max(precos, key=lambda x: x[1])
        tres_maiores = sorted((v for _, v in precos), reverse=True)[:3]
        media3 = sum(tres_maiores) / len(tres_maiores)

        linha["MENOR PREÇO"] = format_price(valor_min) or ""
        linha["SUPERMERCADO MENOR"] = loja_min
        linha["MAIOR PREÇO"] = format_price(valor_max) or ""
        linha["SUPERMERCADO MAIOR"] = loja_max
        linha["MÉDIA 3 MAIORES"] = format_price(media3) or ""

        if len(precos) < 3:
            avisos.append(f"média sobre {len(precos)} preço(s)")

        discrepantes = detectar_discrepantes(precos)
        if discrepantes:
            avisos.append("conferir preço de " + ", ".join(sorted(discrepantes)))

        if custo is None or custo <= 0:
            linha["MARGEM"] = ""
            linha["REGRA"] = ""
            avisos.append("sem custo unitário")
        elif markup is None:
            linha["MARGEM"] = ""
            linha["REGRA"] = ""
            avisos.append("sem markup alvo")
        elif len(precos) < minimo:
            margem = (media3 - custo) / custo
            linha["MARGEM"] = fmt_pct(margem)
            linha["REGRA"] = ""
            avisos.append(f"amostra abaixo do mínimo ({minimo})")
        else:
            margem = (media3 - custo) / custo
            linha["MARGEM"] = fmt_pct(margem)
            linha["REGRA"] = "OK" if abs(margem - markup) < tolerancia else "AJUSTAR"

        linha["OBS BUSCA"] = "; ".join(avisos)
        calculadas += 1
    return calculadas
