"""Conversão entre número e texto de preço/percentual no formato brasileiro."""

from __future__ import annotations

import re


def format_price(value: float | str | None) -> str | None:
    """Número → "R$ 1.234,56". Devolve None para valor inválido ou <= 0."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(".", "").replace(",", ".") if "," in value else value
        try:
            num = float(value)
        except ValueError:
            return None
    else:
        num = float(value)
    if num <= 0:
        return None
    inteiro, cent = divmod(round(num * 100), 100)
    return f"R$ {inteiro:,}".replace(",", ".") + f",{cent:02d}"


def parse_price_br(text: str | float | None) -> float | None:
    """"R$ 1.234,56" → 1234.56. Ignora fórmulas (texto começando com "=")."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text) if float(text) > 0 else None
    text = str(text).strip()
    if not text or text.startswith("="):
        return None
    # Célula numérica lida do Excel chega como "31" ou "31.04".
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        num = float(text)
        return num if num > 0 else None
    m = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}|\d+\.\d{2})", text)
    if not m:
        return None
    raw = m.group(1)
    if "," in raw:
        num = float(raw.replace(".", "").replace(",", "."))
    else:
        num = float(raw)
    return num if num > 0 else None


def fmt_pct(value: float) -> str:
    """0.9 → "90%"."""
    return f"{round(value * 100)}%"


LIMITE_MARKUP_FRACAO = 5.0


def parse_markup(text: str | float | None) -> float | None:
    """Markup para fração decimal: "90%", 0.9, 90 e "0.9" → 0.90.

    A mesma planilha guarda o markup de duas formas: no XLSX a coluna
    `MARKUP` é numérica com fração (1.3 = 130%), no CSV exportado vira texto
    com sinal ("130%"). Interpretar todo texto como porcentagem — o que a
    versão anterior fazia — lê "1.3" como 1,3% e erra o `MARKUP ALVO`,
    `PREÇO PERTIN` e `REGRA` de toda a planilha.

    Regra: com "%" é porcentagem; sem "%", valor até 5 já é fração e acima
    disso é porcentagem. Nenhum markup real fica na ambiguidade (os do
    cliente vão de 0,60 a 1,30, ou 60 a 130).
    """
    if text is None or text == "":
        return None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        v = float(text)
        return v if abs(v) <= LIMITE_MARKUP_FRACAO else v / 100

    bruto = str(text).strip()
    tem_sinal = "%" in bruto
    m = re.search(r"(\d+(?:[.,]\d+)?)", bruto.replace("%", ""))
    if not m:
        return None
    valor = float(m.group(1).replace(",", "."))
    if tem_sinal or valor > LIMITE_MARKUP_FRACAO:
        return valor / 100
    return valor


def clean_ean(text: str | None) -> str:
    """Mantém só os dígitos do código de barras.

    O Excel guarda EAN como número, então a leitura devolve
    "7891910000197.0". Remover a pontuação direto produziria
    "78919100001970" — 14 dígitos, um EAN que não existe. O ".0" tem que cair
    antes.
    """
    s = str(text or "").strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".", 1)[0]
    return re.sub(r"\D", "", s)


def is_valid_ean(text: str | None) -> bool:
    """EAN utilizável para busca exata (>= 8 dígitos)."""
    return len(clean_ean(text)) >= 8
