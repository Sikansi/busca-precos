"""Testes das lojas que o cliente pediu: Carrefour, Mart Minas e Epa.

O cliente cadastrou as três pela tela e nenhuma funcionou. A investigação
mostrou que o cadastro estava correto — o problema era a plataforma:

* **Carrefour** é VTEX, mas da geração Intelligent Search. O endpoint antigo
  (`catalog_system`) devolve **403** lá.
* **Mart Minas** e **Epa** não publicam preço na web: o site é institucional
  (WordPress/regional) e as ofertas são encarte em PDF/imagem e app.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from buscaprecos.config import carregar_config
from buscaprecos.lojas import ClienteVtex
from buscaprecos.texto import multiplicador_de_pacote, pacotes_compativeis

RAIZ = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# As duas gerações da API VTEX
# --------------------------------------------------------------------------- #

class SessaoFalsa:
    """Responde conforme a rota, imitando cada geração de loja VTEX."""

    def __init__(self, status_catalogo: int = 200, corpo_catalogo=None,
                 corpo_inteligente=None):
        self.status_catalogo = status_catalogo
        self.corpo_catalogo = corpo_catalogo if corpo_catalogo is not None else []
        self.corpo_inteligente = corpo_inteligente or {"products": []}
        self.chamadas: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.chamadas.append((url, dict(params or {})))
        eh_catalogo = "catalog_system" in url
        status = self.status_catalogo if eh_catalogo else 200
        corpo = self.corpo_catalogo if eh_catalogo else self.corpo_inteligente

        class Resp:
            status_code = status

            @staticmethod
            def json():
                return corpo

        return Resp()


def _produto(nome="Refrigerante Coca-Cola Lata 350ml", ean="7894900010015",
             preco=4.59):
    return {
        "productName": nome,
        "productId": "1",
        "items": [{"ean": ean, "sellers": [
            {"commertialOffer": {"Price": preco, "IsAvailable": True}}]}],
    }


def test_loja_no_catalogo_antigo_nao_troca_de_api():
    """Supernosso, Americanas e Atacadão respondem pelo endpoint antigo."""
    sessao = SessaoFalsa(status_catalogo=200, corpo_catalogo=[_produto()])
    cliente = ClienteVtex("SUPERNOSSO", "https://x.com", sessao)
    itens = cliente.buscar_por_ean("7894900010015")
    assert len(itens) == 1
    assert cliente.api == "catalogo"
    assert all("catalog_system" in url for url, _ in sessao.chamadas)


@pytest.mark.parametrize("status", [403, 404, 500])
def test_loja_que_recusa_o_catalogo_migra_para_intelligent_search(status):
    """No Carrefour o catálogo antigo devolve 403; a API nova responde."""
    sessao = SessaoFalsa(
        status_catalogo=status,
        corpo_inteligente={"products": [_produto()], "recordsFiltered": 11},
    )
    cliente = ClienteVtex("CARREFOUR", "https://www.carrefour.com.br", sessao)
    itens = cliente.buscar_por_ean("7894900010015")
    assert len(itens) == 1, "devia ter caído para a Intelligent Search"
    assert cliente.api == "inteligente"


def test_migracao_de_api_e_permanente_na_execucao():
    """Insistir no endpoint que já recusou é requisição jogada fora."""
    sessao = SessaoFalsa(
        status_catalogo=403,
        corpo_inteligente={"products": [_produto()]},
    )
    cliente = ClienteVtex("CARREFOUR", "https://x.com", sessao)
    cliente.buscar_por_ean("7894900010015")
    sessao.chamadas.clear()
    cliente.buscar_por_texto("coca")
    assert not any("catalog_system" in url for url, _ in sessao.chamadas)


def test_intelligent_search_manda_o_termo_em_query():
    """Como segmento de caminho ela responde 200 e ignora a busca.

    Medido: devolve o catálogo inteiro (`recordsFiltered` de 21 milhões), o que
    parece resultado válido — falha silenciosa.
    """
    sessao = SessaoFalsa(status_catalogo=403,
                         corpo_inteligente={"products": []})
    cliente = ClienteVtex("CARREFOUR", "https://x.com", sessao)
    cliente.buscar_por_texto("coca cola")
    url, params = sessao.chamadas[-1]
    assert "intelligent-search" in url
    assert params.get("query") == "coca cola"
    assert "coca cola" not in url, "o termo não pode ir no caminho"


def test_api_fixa_nao_migra():
    sessao = SessaoFalsa(status_catalogo=403)
    cliente = ClienteVtex("X", "https://x.com", sessao, api="catalogo")
    assert cliente.buscar_por_texto("coca") == []
    assert cliente.api == "catalogo"


# --------------------------------------------------------------------------- #
# Embalagem múltipla
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("nome,esperado", [
    ("Refrigerante Coca-Cola Original Lata 350ml Pack 12 unidades", 12),
    ("Kit 2 Biscoito Recheado Oreo Original 90g", 2),
    ("BISC CLUB SOCIAL PRESUNT C/6 141G", 6),
    ("BOMBOM FERRERO ROCHER DP 8UN", 8),
    ("Leite Itambé Integral 1L Caixa com 12", 12),
    ("Refrigerante Coca Cola Lata 350ml", None),
    ("SABONETE TAB DOVE 90G", None),
    ("Vitamina C 500mg", None),
])
def test_detecta_embalagem_multipla(nome, esperado):
    assert multiplicador_de_pacote(nome) == esperado


def test_unidade_nao_casa_com_fardo():
    """Medido no Supernosso: R$ 23,34 numa consulta de lata de 350ml.

    A trava de gramatura não pega porque o fardo declara a mesma gramatura
    unitária ("Lata 350ml Pack 12").
    """
    assert not pacotes_compativeis(
        "REFRIG COCA COLA LT 350ML ORIG",
        "Refrigerante Coca-Cola Original Lata 350ml Pack 12 unidades",
    )


def test_unidade_nao_casa_com_kit():
    """Medido no Carrefour: R$ 7,78 em "Kit 2 Biscoito Oreo" para 90g."""
    assert not pacotes_compativeis(
        "BISC RECH OREO PC 90G ORIG", "Kit 2 Biscoito Recheado Oreo Original 90g"
    )


def test_pacote_do_mesmo_tamanho_casa():
    assert pacotes_compativeis(
        "BISC CLUB SOCIAL PRESUNT C/6 141G", "Club Social Presunto 6 unidades 141g"
    )


def test_pacotes_de_tamanhos_diferentes_nao_casam():
    assert not pacotes_compativeis(
        "BISC CLUB SOCIAL PRESUNT C/6 141G", "Club Social Presunto Kit 12 141g"
    )


def test_barra_sobrevive_a_normalizacao():
    """`normalize` troca "/" por espaço e "C/6" sumia."""
    from buscaprecos.texto import _normalize_com_barra

    assert "C/6" in _normalize_com_barra("BISC CLUB SOCIAL PRESUNT C/6 141G")


# --------------------------------------------------------------------------- #
# Cadastro padrão
# --------------------------------------------------------------------------- #

def test_carrefour_vem_cadastrado_e_e_buscado():
    cfg = carregar_config(RAIZ)
    assert "CARREFOUR" in cfg.lojas
    assert cfg.lojas["CARREFOUR"].tipo == "vtex"
    assert "CARREFOUR" in [lj.nome for lj in cfg.lojas_para_buscar()]


@pytest.mark.parametrize("loja", ["MART MINAS", "EPA"])
def test_mart_minas_e_epa_vem_como_manual(loja):
    """Não publicam preço na web: site institucional, ofertas em encarte e app.

    Cadastradas como `manual` para a coluna existir na planilha, mas fora da
    busca automática — prometer que o programa preenche seria mentira.
    """
    cfg = carregar_config(RAIZ)
    assert loja in cfg.lojas
    assert cfg.lojas[loja].tipo == "manual"
    assert not cfg.lojas[loja].consulta_rede
    assert loja in cfg.colunas_de_preco(), "a coluna tem que existir para digitar"


def test_seed_do_payload_tem_as_tres():
    """O cliente recebe as três já cadastradas, sem mexer na tela."""
    padrao = json.loads((RAIZ / "config.padrao.json").read_text(encoding="utf-8"))
    for loja in ("CARREFOUR", "MART MINAS", "EPA"):
        assert loja in padrao["lojas"], f"{loja} fora do seed"
        assert loja in padrao["estatisticas"]["lojas"]


def test_paralelismo_cobre_as_lojas_de_rede():
    """Menos threads que lojas serializa parte da busca sem necessidade."""
    cfg = carregar_config(RAIZ)
    assert int(cfg.busca["lojas_em_paralelo"]) >= len(cfg.lojas_para_buscar())


def test_aviso_de_pacote_chega_na_obs_busca():
    """Match exato por EAN num kit não é rejeitado, mas é sinalizado.

    Carrefour cadastra "Kit 2 Biscoito Oreo" (R$ 7,78) sob o EAN do 90g.
    Rejeitar seria pior: Supernosso devolve "Caixa com 12" por EAN com preço
    unitário correto. Então a busca anexa o aviso e a regra o publica.
    """
    from buscaprecos.regras import calcular_derivadas

    cfg = carregar_config(RAIZ)
    linha = {
        cfg.colunas["descricao"]: "BISC RECH OREO PC 90G ORIG",
        cfg.colunas["ean"]: "7622300830151",
        cfg.colunas["custo"]: "R$ 2,50",
        "MARKUP": 1.3,
        "VERDEMAR": "R$ 5,49", "SUPERNOSSO": "R$ 4,79", "CARREFOUR": "R$ 7,78",
        "__avisos__": ["CARREFOUR pode ser embalagem de 2"],
    }
    calcular_derivadas([linha], cfg, {})
    assert "embalagem de 2" in linha["OBS BUSCA"]
    assert "CARREFOUR" in linha["OBS BUSCA"]


def test_campo_interno_de_avisos_nao_vira_coluna():
    """`__avisos__` é comunicação entre módulos, não dado do cliente."""
    cfg = carregar_config(RAIZ)
    from buscaprecos.regras import COLUNAS_DERIVADAS

    assert "__avisos__" not in cfg.colunas_de_preco()
    assert "__avisos__" not in COLUNAS_DERIVADAS
