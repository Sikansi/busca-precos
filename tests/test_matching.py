"""Testes do casamento de produto.

O sistema erra em silêncio: um match errado grava um preço plausível na
planilha e ninguém percebe. Estes casos vêm de erros reais observados nos
dados de julho/2026 — em especial a lata de Coca 350ml que casava com a
garrafa de 1,5L no Atacadão (R$ 9,69 gravado como se fosse o preço da lata).

Rodar: python -m pytest tests/ -q
"""

from __future__ import annotations

import pytest

from buscaprecos.precos import (
    clean_ean,
    format_price,
    is_valid_ean,
    parse_markup,
    parse_price_br,
)
from buscaprecos.regras import detectar_discrepantes
from buscaprecos.texto import (
    brands_compatible,
    extract_measures,
    fully_compatible,
    measures_compatible,
    normalize,
    score_match,
    validate_candidate,
    variants_compatible,
)


# --------------------------------------------------------------------------- #
# Gramatura — a barreira que faltava
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("consulta,candidato", [
    # o falso positivo real do Atacadão
    ("REFRIG COCA COLA LT 350ML ORIG", "Refrigerante Coca-Cola 1,5L"),
    ("BISC RECH OREO PC 90G ORIG", "Biscoito Recheado Oreo Original 36g"),
    ("CERV HEINEKEN GF 600ML", "Cerveja Heineken Lata 350ml"),
    ("LEITE LV ITAMBE TP 1L INTEG", "Leite Longa Vida Itambé Integral 200ml"),
    ("ACUCAR REFINADO UNIAO PC 1KG", "Açúcar Refinado União 5kg"),
])
def test_rejeita_gramatura_diferente(consulta, candidato):
    assert not measures_compatible(consulta, candidato)


@pytest.mark.parametrize("consulta,candidato", [
    ("REFRIG COCA COLA LT 350ML ORIG", "Refrigerante Coca Cola Lata 350ml"),
    ("BISC RECH OREO PC 90G ORIG", "Biscoito Recheado Oreo Original 90g"),
    ("LEITE LV ITAMBE TP 1L INTEG", "Leite Longa Vida Itambé Integral 1L"),
    ("BOMBOM FERRERO ROCHER DP 8UN", "Ferrero Rocher 8 unidades"),
    # candidato sem medida declarada não pode ser bloqueado
    ("MOLHO TOM POMAROLA 300G", "Molho de Tomate Pomarola Tradicional"),
])
def test_aceita_gramatura_igual_ou_ausente(consulta, candidato):
    assert measures_compatible(consulta, candidato)


def test_extract_measures_normaliza_unidade():
    assert extract_measures("PACOTE 500 GRAMAS") == {"500G"}
    assert extract_measures("GARRAFA 2 LITROS") == {"2L"}
    assert "350ML" in extract_measures("REFRIG LT 350ML")


# --------------------------------------------------------------------------- #
# Marca
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("consulta,candidato", [
    ("BISC RECH OREO PC 90G ORIG", "Biscoito Recheado Trakinas Chocolate 90g"),
    ("CERV HEINEKEN GF 600ML", "Cerveja Brahma Garrafa 600ml"),
    ("KETCHUP HEINZ 567G TRAD", "Ketchup Hemmer 567g Tradicional"),
])
def test_rejeita_marca_diferente(consulta, candidato):
    assert not brands_compatible(consulta, candidato)


@pytest.mark.parametrize("consulta,candidato", [
    ("BISC RECH OREO PC 90G ORIG", "Biscoito Recheado Oreo Original 90g"),
    # erro de digitação recorrente da nota fiscal
    ("MACARRAO BARILA PENNE 500G", "Macarrão Penne Rigate Barilla 500g"),
    ("MAIONESE HELLMANS 335G", "Maionese Hellmann's 335g"),
])
def test_aceita_mesma_marca(consulta, candidato):
    assert brands_compatible(consulta, candidato)


# --------------------------------------------------------------------------- #
# Substantivo principal
# --------------------------------------------------------------------------- #

def test_creme_de_leite_nao_casa_com_leite_condensado():
    assert not validate_candidate(
        "CR LEITE ITAMBE 200G TP", "Leite Condensado Itambé 395g"
    )


def test_creme_de_leite_casa_com_creme_de_leite():
    assert validate_candidate(
        "CR LEITE ITAMBE 200G TP", "Creme de Leite Itambé 200g"
    )


# --------------------------------------------------------------------------- #
# Variante (só usada em fonte permissiva)
# --------------------------------------------------------------------------- #

def test_zero_nao_casa_com_tradicional():
    assert not variants_compatible(
        "REFRIG COCA COLA PET 2L ZERO", "Refrigerante Coca-Cola 2L Original"
    )


def test_variante_ausente_nos_dois_lados_e_compativel():
    assert variants_compatible(
        "REFRIG COCA COLA LT 350ML", "Refrigerante Coca Cola Lata 350ml"
    )


def test_fully_compatible_barra_sabor_trocado():
    assert not fully_compatible(
        "BALA GELATINA MORANGO 80G FINI", "Bala de Gelatina Fini Uva 80g"
    )


# --------------------------------------------------------------------------- #
# Score
# --------------------------------------------------------------------------- #

def test_score_premia_candidato_correto():
    consulta = "BISC RECH OREO PC 90G ORIG"
    certo = score_match(consulta, "Biscoito Recheado Oreo Original 90g")
    errado = score_match(consulta, "Biscoito Recheado Trakinas Morango 126g")
    assert certo > errado
    assert certo >= 68, "o candidato certo tem que passar do limiar do passe 1"


def test_normalize_remove_acento_e_pontuacao():
    assert normalize("Açúcar Refinado União 1kg") == "ACUCAR REFINADO UNIAO 1KG"
    assert normalize("Coca-Cola  350ml!") == "COCA COLA 350ML"


# --------------------------------------------------------------------------- #
# Preço e markup
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("entrada,esperado", [
    (5.25, "R$ 5,25"),
    (1234.5, "R$ 1.234,50"),
    (0.99, "R$ 0,99"),
    (0, None),
    (-3, None),
    (None, None),
])
def test_format_price(entrada, esperado):
    assert format_price(entrada) == esperado


@pytest.mark.parametrize("entrada,esperado", [
    ("R$ 1.234,56", 1234.56),
    ("R$ 5,25", 5.25),
    ("5.25", 5.25),
    ("=A1*2", None),      # fórmula não é preço
    ("#DIV/0!", None),
    ("", None),
    (None, None),
])
def test_parse_price_br(entrada, esperado):
    assert parse_price_br(entrada) == esperado


def test_parse_price_br_aceita_numero():
    assert parse_price_br(5.25) == 5.25


@pytest.mark.parametrize("entrada,esperado", [
    ("90%", 0.90),
    ("130%", 1.30),
    ("60", 0.60),
    (0.6, 0.6),      # XLSX guarda a fração
    (1.3, 1.3),
    (90, 0.9),       # e às vezes guarda a porcentagem
    (130, 1.3),
    ("", None),
    (None, None),
])
def test_parse_markup(entrada, esperado):
    assert parse_markup(entrada) == esperado


@pytest.mark.parametrize("entrada,esperado", [
    ("1.3", 1.3),
    ("0.6", 0.6),
    ("1", 1.0),
    ("0,9", 0.9),
])
def test_parse_markup_texto_de_fracao_nao_e_porcentagem(entrada, esperado):
    """O leitor de XLSX entrega número como texto.

    Tratar "1.3" como 1,3% errava o MARKUP ALVO, o PREÇO PERTIN e a REGRA de
    todas as linhas da planilha.
    """
    assert parse_markup(entrada) == pytest.approx(esperado)


# --------------------------------------------------------------------------- #
# Discrepante entre lojas
# --------------------------------------------------------------------------- #

def test_detecta_preco_de_caixa_fechada():
    """Sem gramatura na descrição, nada barra o preço do fardo."""
    precos = [
        ("VERDEMAR", 1.19), ("SUPERNOSSO", 1.00), ("LOJAS AMERICANAS", 2.99),
        ("VILLEFORTE", 41.98), ("PAULO", 0.99), ("ATACADAO", 11.98),
    ]
    assert "VILLEFORTE" in detectar_discrepantes(precos)


def test_precos_proximos_nao_geram_alerta():
    precos = [("VERDEMAR", 5.99), ("SUPERNOSSO", 5.49), ("VILLEFORTE", 6.48)]
    assert detectar_discrepantes(precos) == []


def test_menos_de_tres_precos_nao_tem_mediana_confiavel():
    assert detectar_discrepantes([("A", 1.0), ("B", 90.0)]) == []


@pytest.mark.parametrize("entrada,esperado", [
    ("7894900010015", True),
    ("789.4900-010015", True),
    ("78905498", True),
    ("1234567", False),
    ("", False),
    (None, False),
])
def test_is_valid_ean(entrada, esperado):
    assert is_valid_ean(entrada) is esperado


def test_clean_ean_tira_nao_digitos():
    assert clean_ean("789.4900-010015") == "7894900010015"
    assert clean_ean(None) == ""


def test_clean_ean_nao_inventa_digito_em_numero_do_excel():
    """O Excel devolve EAN como float; o ".0" não pode virar dígito."""
    assert clean_ean("7891910000197.0") == "7891910000197"
    assert clean_ean(7891910000197.0) == "7891910000197"
    assert is_valid_ean("7891910000197.0")


# --------------------------------------------------------------------------- #
# Araújo — parsing do HTML
# --------------------------------------------------------------------------- #

import pathlib

from buscaprecos.lojas import ClienteAraujo

GRADE = (pathlib.Path(__file__).parent / "dados" / "araujo_grade.html").read_text(
    encoding="utf-8"
)


def test_araujo_extrai_produtos_da_grade():
    produtos = ClienteAraujo.extrair_produtos(GRADE)
    # o terceiro tile não tem preço e é descartado
    assert len(produtos) == 2
    assert [p["pid"] for p in produtos] == ["26230", "31877"]


def test_araujo_pega_o_preco_atual_e_nao_o_riscado():
    """`productPrice__lineThrough` é o preço antigo — o de prateleira é o outro."""
    primeiro = ClienteAraujo.extrair_produtos(GRADE)[0]
    assert primeiro["preco"] == 3.99


def test_araujo_extrai_ean_da_url_da_imagem():
    """O HTML não traz campo de EAN, mas a URL da imagem traz — com zero à
    esquerda, que precisa cair para casar com o EAN da planilha."""
    produtos = ClienteAraujo.extrair_produtos(GRADE)
    assert produtos[0]["ean"] == "7894900010015"
    assert produtos[1]["ean"] == "7894900701517"


def test_araujo_desescapa_entidade_html_no_nome():
    segundo = ClienteAraujo.extrair_produtos(GRADE)[1]
    assert segundo["nome"] == "Refrigerante Coca Cola Sem Açúcar 2 Litros"


def test_araujo_produto_sem_preco_nao_entra():
    assert all(p["preco"] for p in ClienteAraujo.extrair_produtos(GRADE))


def test_araujo_grade_vazia_nao_quebra():
    assert ClienteAraujo.extrair_produtos("<div>nada aqui</div>") == []
