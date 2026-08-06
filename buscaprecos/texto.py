"""Normalização, pontuação e validação de casamento entre descrições de produto.

Este é o coração do valor do sistema: a descrição da nota fiscal
("BISC RECH OREO PC 90G ORIG") tem que casar com o nome no site do
supermercado ("Biscoito Recheado Oreo Original 90g") e **não** casar com
uma gramatura ou sabor diferente. Errar para o lado permissivo produz
preço errado na planilha sem ninguém perceber — por isso as validações de
marca, medida, variante e sabor são barreiras separadas do score.

As funções aqui são puras (texto → texto/número/bool) e cobertas por
`tests/test_matching.py`. Mexer em qualquer limiar sem rodar os testes é
como o sistema volta a produzir falso positivo silencioso.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

# --------------------------------------------------------------------------- #
# Normalização
# --------------------------------------------------------------------------- #

def normalize(text: str) -> str:
    """Remove acento, sobe para maiúscula e troca pontuação por espaço."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


STOPWORDS = {
    "DE", "DA", "DO", "DOS", "DAS", "COM", "SEM", "PARA", "E", "O", "A",
    "UNIDADES", "UNIDADE", "PACOTE", "LATA", "GARRAFA", "EMBALAGEM",
    "TRADICIONAL", "ORIGINAL", "EXTRA", "INTEGRAL", "REFINADO", "TIPO",
}

TYPO_ALIASES = {
    "BARILA": "BARILLA",
    "HELLMANS": "HELLMANNS",
    "HELLMANN": "HELLMANNS",
    # "CR" é como a nota escreve CREME ("CR LEITE", "CR DENTAL", "CR AVELA").
    # Sem expandir, a trava que impede "creme de leite" casar com "leite
    # condensado" nunca dispara — ela procura o token CREME, que não existe
    # na forma abreviada.
    "CR": "CREME",
}

# Abreviações genéricas da nota fiscal. Não são marca, e tratá-las como marca
# é pior que ignorá-las: em "BISC RECH OREO PC 90G ORIG" as candidatas ficavam
# [BISC, RECH, OREO, ORIG], todas com 4 letras, e a eleição por tamanho
# escolhia RECH — que casa com "Recheado" de qualquer produto. Resultado: a
# marca real (OREO) nunca era conferida e Trakinas passava como Oreo.
ABREVIACOES_GENERICAS = {
    "BISC", "RECH", "ORIG", "TRAD", "INTEG", "CONC", "CONF", "COND",
    "DESOD", "DETERG", "REFRIG", "REFRI", "CERV", "CHOC", "SALG", "CROC",
    "ISOT", "ISOTON", "ENERG", "AMAC", "LIMP", "MULTIUSO", "UNID",
    "PACOTE", "SACHE", "FARDO", "CAIXA", "GARRAFA", "LATA", "POTE",
}

# Substantivo principal do produto: se está na consulta, tem que estar no
# candidato. É o que impede "CREME DE LEITE" casar com "LEITE CONDENSADO".
CORE_NOUNS = {
    "AZEITE", "ARROZ", "LEITE", "CREME", "MAIONESE", "MOLHO", "MACARRAO",
    "SPAGHETTI", "PENNE", "FUSILLI", "CAFE", "ACHOCOLATADO", "FEIJAO",
    "ACUCAR", "OLEO", "MANTEIGA", "IOGURTE", "QUEIJO", "PRESUNTO",
    "MORTADELA", "SALSICHA", "LINGUICA", "BISCOITO", "BOLACHA",
    "REFRIGERANTE", "SUCO", "AGUA", "CERVEJA", "VINHO", "WHISKY",
    "DETERGENTE", "SABAO", "PAPEL", "FRALDA", "SHAMPOO", "CONDICIONADOR",
    "DESINFETANTE", "AMACIANTE", "FARINHA", "FUBA", "AVEIA",
    "MILHO", "ATUM", "SARDINHA", "EXTRATO", "KETCHUP", "MOSTARDA",
    "TEMPERO", "SAL", "PIMENTA", "VINAGRE", "CALDO", "SOPA",
}

PASTA_TYPES = {"SPAGHETTI", "PENNE", "FUSILLI", "PARAFUSO", "LASANHA", "NHOQUE"}


def apply_typos(text: str) -> str:
    """Normaliza e corrige erros de digitação recorrentes da nota fiscal."""
    return " ".join(TYPO_ALIASES.get(t, t) for t in normalize(text).split())


def extract_tokens(text: str) -> set[str]:
    return {t for t in normalize(text).split() if len(t) > 1 and t not in STOPWORDS}


def brand_tokens(text: str) -> list[str]:
    """Palavras que provavelmente são marca.

    Exclui substantivo do produto, número e abreviação genérica. Quando nada
    sobra, devolve lista vazia — "não identifiquei marca" é mais honesto que
    eleger uma abreviação, porque as outras barreiras (substantivo, gramatura)
    continuam valendo.
    """
    tokens = [
        t for t in apply_typos(text).split()
        if len(t) >= 4 and t not in STOPWORDS and not re.match(r"^\d", t)
    ]
    brands = [
        t for t in tokens
        if t not in CORE_NOUNS and t not in ABREVIACOES_GENERICAS
    ]
    if brands:
        return brands
    # Sem candidata clara: aceita substantivo como último recurso, mas nunca
    # abreviação genérica.
    return [t for t in tokens if t not in ABREVIACOES_GENERICAS][:2]


def core_noun_tokens(text: str) -> set[str]:
    tokens = set(apply_typos(text).split())
    found = tokens & CORE_NOUNS
    if tokens & PASTA_TYPES:
        found |= tokens & PASTA_TYPES
        found.add("MACARRAO")
    return found


def token_in_candidate(token: str, candidate: str, threshold: int = 82) -> bool:
    """Token aparece no candidato, exato ou por similaridade."""
    cn = normalize(candidate)
    if token in cn:
        return True
    for part in cn.split():
        if len(part) < 4:
            continue
        if fuzz.ratio(token, part) >= threshold:
            return True
    return False


def nouns_satisfied(query_nouns: set[str], candidate: str) -> bool:
    if not query_nouns:
        return True
    pasta = query_nouns & (PASTA_TYPES | {"MACARRAO"})
    if pasta:
        opts = (query_nouns & PASTA_TYPES) | {
            "MACARRAO", "MASSA", "SPAGHETTI", "PENNE", "FUSILLI",
        }
        return any(token_in_candidate(n, candidate, threshold=80) for n in opts)
    return all(token_in_candidate(n, candidate, threshold=80) for n in query_nouns)


# --------------------------------------------------------------------------- #
# Medidas (gramatura / volume)
# --------------------------------------------------------------------------- #

def extract_measures(text: str) -> set[str]:
    """Medidas tipadas: {"90G", "1L", "350ML"}."""
    norm = normalize(text)
    norm = re.sub(r"\bGRAMAS?\b", "G", norm)
    norm = re.sub(r"\bLITROS?\b", "L", norm)
    norm = re.sub(r"\bMILILITROS?\b", "ML", norm)
    found = set(re.findall(r"\d+(?:[.,]\d+)?\s*(?:G|KG|ML|L|UN)", norm))
    found.update(re.findall(r"\d+(?:[.,]\d+)?(?:G|KG|ML|L)", norm))
    return {re.sub(r"\s+", "", x) for x in found}


def measure_numbers(text: str) -> set[str]:
    """Só os números, sem unidade — usado quando não há medida tipada."""
    norm = re.sub(r"\bGRAMAS?\b", "G", normalize(text))
    return set(re.findall(r"\d+(?:[.,]\d+)?", norm))


# --------------------------------------------------------------------------- #
# Barreiras de compatibilidade (aplicadas depois do score)
# --------------------------------------------------------------------------- #

# Palavras que parecem marca mas são genéricas: exigir match nelas rejeitaria
# candidatos válidos.
WEAK_BRAND_TOKENS = {
    "NECTAR", "NÉCTAR", "BEBIDA", "REFRIG", "REFRIGERANTE", "SUCO", "AGUA",
    "BISCOITO", "BISC", "CHOCOLATE", "CHOC", "SALGADINHO", "SALG", "BOLINHO",
    "FARINHA", "CALDO", "LEITE", "CREME", "MOLHO", "TABLETE", "BOMBOM",
}

VARIANT_TOKENS = {
    "ZERO", "DIET", "LIGHT", "LITE", "INTEGRAL", "SEM ACUCAR", "S ACUCAR",
    "SEM LACTOSE", "DESNATADO", "SEMIDESNATADO", "VEGANO", "ORGÂNICO", "ORGANICO",
}

FLAVOR_TOKENS = {
    "MORANGO", "CHOCOLATE", "CHOC", "UVA", "LARANJA", "LIMAO", "LIMÃO", "BAUNILHA",
    "CARAMELO", "MENTA", "CEREJA", "ABACAXI", "MARACUJA", "MARACUJÁ", "PESSEGO",
    "PÊSSEGO", "COCO", "CAFE", "CAFÉ", "PIZZA", "PRESUNTO", "CHURRASCO",
    "CEBOLA", "QUEIJO", "BACON", "ORIGINAL", "TRADICIONAL",
}


# Padrões de embalagem múltipla no nome do produto. Sem esta trava, uma
# consulta de unidade casa com fardo ou kit e grava o preço do conjunto:
# medido "REFRIG COCA COLA LT 350ML" → R$ 23,34 (fardo de 12) no Supernosso, e
# "BISC RECH OREO 90G" → R$ 7,78 em "Kit 2 Biscoito Oreo" no Carrefour. A trava
# de gramatura não pega porque o multipack declara a mesma gramatura unitária.
RE_PACOTES = (
    re.compile(r"\bKIT\s*(?:C/\s*)?(\d{1,2})\b"),
    re.compile(r"\bPACK\s*(?:C/\s*)?(\d{1,2})\b"),
    re.compile(r"\bLEVE\s*(\d{1,2})\b"),
    re.compile(r"\bFARDO\s*(?:C/\s*)?(\d{1,2})\b"),
    re.compile(r"\b(?:CX|CAIXA|DP|DISPLAY|PC|PACOTE)\s*C(?:/|OM)\s*(\d{1,2})\b"),
    re.compile(r"\bC/\s*(\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})\s*UNIDADES?\b"),
    re.compile(r"\b(\d{1,2})\s*UN\b"),
)


def _normalize_com_barra(text: str) -> str:
    """Como `normalize`, mas preserva a barra.

    `normalize` troca "/" por espaço, e aí "C/6" some: a nota escreve o pacote
    assim ("BISC CLUB SOCIAL PRESUNT C/6 141G") e o multiplicador passava
    despercebido. Detectar "C 6" no texto já normalizado seria ambíguo demais
    ("VIT C 12"), então a barra tem que sobreviver até a detecção.
    """
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).upper()
    t = re.sub(r"[^A-Z0-9/]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def multiplicador_de_pacote(text: str) -> int | None:
    """Quantas unidades o nome declara, se declarar. None quando é unidade.

    Ignora 1, que não muda o preço, e números acima de 30, que quase sempre são
    gramatura ("350ML C/ 350" não existe, mas "SABONETE 90G" com regex frouxa
    daria falso positivo).
    """
    norm = _normalize_com_barra(text)
    for regex in RE_PACOTES:
        m = regex.search(norm)
        if m:
            try:
                n = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if 2 <= n <= 30:
                return n
    return None


def pacotes_compativeis(query: str, candidate: str) -> bool:
    """Unidade não casa com fardo, e fardo de 6 não casa com fardo de 12."""
    mq = multiplicador_de_pacote(query)
    mc = multiplicador_de_pacote(candidate)
    if mc is None:
        # Candidato não declara pacote: aceita. Se a consulta pedia fardo, o
        # preço unitário é o erro menos grave, e a gramatura ainda filtra.
        return True
    if mq is None:
        return False  # consulta de unidade contra candidato em pacote
    return mq == mc


def brands_compatible(query: str, candidate: str) -> bool:
    """A marca principal da consulta tem que aparecer no candidato."""
    brands = brand_tokens(query)
    if not brands:
        return True
    strong = [br for br in brands if br not in WEAK_BRAND_TOKENS]
    primary_pool = strong or brands
    primary = max(primary_pool, key=len)
    if not token_in_candidate(primary, candidate, threshold=80):
        return False
    extras = [br for br in strong if br != primary and len(br) >= 5]
    return all(token_in_candidate(br, candidate, threshold=80) for br in extras[:1])


def measures_compatible(query: str, candidate: str) -> bool:
    """Gramatura/volume não pode divergir quando os dois lados declaram."""
    mq, mc = extract_measures(query), extract_measures(candidate)
    if mq and mc:
        return bool(mq & mc)
    if mq or mc:
        # um lado sem medida tipada — não bloqueia
        return True
    nq, nc = measure_numbers(query), measure_numbers(candidate)
    if nq and nc:
        return bool(nq & nc)
    return True


def variants_compatible(query: str, candidate: str) -> bool:
    """ZERO/DIET/LIGHT/INTEGRAL tem que estar nos dois ou em nenhum."""
    qn, cn = normalize(query), normalize(candidate)
    for v in VARIANT_TOKENS:
        vn = normalize(v)
        if (vn in qn) != (vn in cn):
            return False
    return True


def flavors_compatible(query: str, candidate: str) -> bool:
    """Se a consulta declara sabor, o candidato precisa declarar algum igual."""
    qn, cn = normalize(query), normalize(candidate)
    flavors_q = {f for f in FLAVOR_TOKENS if normalize(f) in qn}
    if not flavors_q:
        return True
    return bool({normalize(f) for f in flavors_q} & {
        normalize(f) for f in FLAVOR_TOKENS if normalize(f) in cn
    })


def validate_candidate(query: str, candidate: str, *, relaxed: bool = False) -> bool:
    """Barreira mínima usada no passe por texto (marca + substantivo)."""
    brands = brand_tokens(query)
    if brands:
        if not all(token_in_candidate(b, candidate, threshold=76) for b in brands[:2]):
            return False
    nouns = core_noun_tokens(query)
    if nouns and not nouns_satisfied(nouns, candidate):
        return False
    if "LEITE" in nouns and "CREME" in nouns:
        cn = normalize(candidate)
        if "CREME" not in cn and "LEITE CONDENSADO" not in cn:
            return False
        if "LEITE" not in cn and "LEITE" not in apply_typos(candidate):
            return False
    return True


def fully_compatible(query: str, candidate: str) -> bool:
    """Todas as barreiras: marca, medida, variante e sabor.

    Usada quando o preço vem de fonte permissiva (lookup por nome no estoque,
    raspagem do Araújo) e o score sozinho não é garantia suficiente.
    """
    return (
        brands_compatible(query, candidate)
        and measures_compatible(query, candidate)
        and pacotes_compativeis(query, candidate)
        and variants_compatible(query, candidate)
        and flavors_compatible(query, candidate)
    )


# --------------------------------------------------------------------------- #
# Score
# --------------------------------------------------------------------------- #

def score_match(query: str, candidate: str, *, relaxed: bool = False) -> float:
    """Pontua o candidato. Não é 0..100 fechado: bônus podem passar de 100."""
    query = apply_typos(query)
    qn, cn = normalize(query), normalize(candidate)
    base = fuzz.token_set_ratio(qn, cn)
    q_tokens = extract_tokens(query)
    if q_tokens:
        overlap = sum(1 for t in q_tokens if token_in_candidate(t, cn)) / len(q_tokens)
        base = base * 0.45 + overlap * 100 * 0.55
    for brand in brand_tokens(query):
        if token_in_candidate(brand, cn, threshold=78):
            base += 18
        elif not relaxed:
            base -= 10
    q_nums, c_nums = measure_numbers(query), measure_numbers(candidate)
    q_meas, c_meas = extract_measures(query), extract_measures(candidate)
    if q_meas and c_meas and (q_meas & c_meas):
        base += 16
    elif q_nums and c_nums and (q_nums & c_nums):
        base += 8
    elif q_meas and not relaxed:
        base -= 12
    return base


# --------------------------------------------------------------------------- #
# Geração de consultas
# --------------------------------------------------------------------------- #

def search_queries(product: str) -> list[str]:
    """Consultas em ordem de especificidade, sem repetição."""
    norm = apply_typos(product)
    tokens = [t for t in norm.split() if len(t) > 1 and t not in STOPWORDS]
    queries: list[str] = [(product or "").strip()]
    if tokens:
        queries.append(" ".join(tokens[:3]))
        queries.append(" ".join(tokens[:2]))
    queries.extend(brand_tokens(product))
    for token in sorted(tokens, key=len, reverse=True):
        if len(token) >= 4:
            queries.append(token)
    seen: set[str] = set()
    result: list[str] = []
    for q in queries:
        key = normalize(q)
        if key and key not in seen:
            seen.add(key)
            result.append(q)
    return result


def vtex_query_tokens(product: str) -> list[str]:
    """VTEX responde mal a frase com espaço — usa só termos únicos."""
    return [q for q in search_queries(product) if " " not in q.strip()]
