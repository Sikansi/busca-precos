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
def test_loja_sem_preco_na_web_nao_e_cadastrada(loja):
    """Loja listada no programa é promessa de busca.

    Elas entraram na v1.1.1 como coluna para o cliente digitar, o que não
    entrega nada: criar coluna na planilha ele faz sozinho, e ver a loja na
    lista faz esperar que o preço venha preenchido. Foram retiradas.
    """
    cfg = carregar_config(RAIZ)
    assert loja not in cfg.lojas
    assert loja not in cfg.colunas_de_preco()


def test_toda_loja_cadastrada_e_preenchida_pelo_programa():
    """O invariante do cadastro: nada aparece na lista sem ser preenchido."""
    cfg = carregar_config(RAIZ)
    for lj in cfg.lojas.values():
        assert lj.consulta_rede or lj.tipo == "estoque", (
            f"{lj.nome} é tipo {lj.tipo}: aparece na lista mas ninguém preenche"
        )


def test_manual_nao_e_oferecido_no_cadastro():
    from buscaprecos.config import TIPOS_LOJA

    assert "manual" not in TIPOS_LOJA


def test_seed_traz_o_carrefour_e_manda_retirar_as_manuais():
    padrao = json.loads((RAIZ / "config.padrao.json").read_text(encoding="utf-8"))
    assert "CARREFOUR" in padrao["lojas"]
    assert "CARREFOUR" in padrao["estatisticas"]["lojas"]
    for loja in ("MART MINAS", "EPA"):
        assert loja not in padrao["lojas"]
        assert loja in padrao["lojas_retiradas"], (
            "quem recebeu a v1.1.1 precisa que a atualização desfaça o cadastro"
        )


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


# --------------------------------------------------------------------------- #
# Lojas novas chegando por atualização
# --------------------------------------------------------------------------- #

def _instalacao(tmp_path, mexer=None):
    """Instalação existente: config.json antigo + payload novo."""
    import shutil

    payload = tmp_path / "payload" / "9.9.9"
    payload.mkdir(parents=True)
    shutil.copy2(RAIZ / "config.padrao.json", payload / "config.padrao.json")
    shutil.copy2(RAIZ / "categorias.csv", payload / "categorias.csv")

    antigo = json.loads((RAIZ / "config.padrao.json").read_text(encoding="utf-8"))
    for loja in ("CARREFOUR", "MART MINAS", "EPA"):
        antigo["lojas"].pop(loja, None)
    antigo["estatisticas"]["lojas"] = [
        l for l in antigo["estatisticas"]["lojas"]
        if l not in ("CARREFOUR", "MART MINAS", "EPA")
    ]
    antigo["cep"] = "31000-000"
    antigo["lojas"]["ATACADAO"]["ativa"] = False
    if mexer:
        mexer(antigo)
    (tmp_path / "config.json").write_text(
        json.dumps(antigo, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def test_loja_nova_do_seed_chega_em_instalacao_existente(tmp_path):
    """O config.json do cliente é preservado nas atualizações.

    Publicar o Carrefour no seed não tinha efeito nenhum em quem já usava o
    programa: o seed só é lido quando não existe config.json. Mesmo furo da URL
    de atualização, que eu tratei como caso isolado.
    """
    payload = _instalacao(tmp_path)
    cfg = carregar_config(tmp_path, payload)
    assert "CARREFOUR" in cfg.lojas
    assert "CARREFOUR" in [lj.nome for lj in cfg.lojas_para_buscar()]
    assert "CARREFOUR" in cfg.colunas_estatistica()


def test_mudanca_e_gravada_no_arquivo(tmp_path):
    """Só em memória, a loja apareceria e desapareceria conforme o cliente
    salvasse ou não."""
    payload = _instalacao(tmp_path)
    carregar_config(tmp_path, payload)
    gravado = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert "CARREFOUR" in gravado["lojas"]


def test_preferencias_do_cliente_sobrevivem(tmp_path):
    payload = _instalacao(tmp_path)
    cfg = carregar_config(tmp_path, payload)
    assert cfg.cep == "31000-000"
    assert cfg.lojas["ATACADAO"].ativa is False, "o liga/desliga é do cliente"


def test_plataforma_errada_do_cliente_e_corrigida(tmp_path):
    """Loja que vem no seed é minha para manter: se o cliente cadastrou com a
    plataforma errada, a atualização conserta em vez de deixar sempre vazia."""
    def cadastro_errado(c):
        c["lojas"]["CARREFOUR"] = {
            "tipo": "vip", "endereco": "carrefour.com.br", "ativa": True,
        }
    payload = _instalacao(tmp_path, cadastro_errado)
    cfg = carregar_config(tmp_path, payload)
    assert cfg.lojas["CARREFOUR"].tipo == "vtex"
    assert cfg.lojas["CARREFOUR"].endereco == "https://www.carrefour.com.br"
    assert any("plataforma" in m for m in cfg.avisos_de_migracao)


def test_loja_que_o_cliente_criou_nao_e_tocada(tmp_path):
    def loja_propria(c):
        c["lojas"]["MERCADO DO ZE"] = {
            "tipo": "vtex", "endereco": "https://ze.com.br", "ativa": True,
        }
    payload = _instalacao(tmp_path, loja_propria)
    cfg = carregar_config(tmp_path, payload)
    assert cfg.lojas["MERCADO DO ZE"].endereco == "https://ze.com.br"


def test_loja_desligada_pelo_cliente_nao_e_religada(tmp_path):
    def desligou_carrefour(c):
        c["lojas"]["CARREFOUR"] = {
            "tipo": "vtex", "endereco": "https://www.carrefour.com.br",
            "ativa": False,
        }
    payload = _instalacao(tmp_path, desligou_carrefour)
    cfg = carregar_config(tmp_path, payload)
    assert cfg.lojas["CARREFOUR"].ativa is False


def test_mudancas_ficam_disponiveis_para_a_interface(tmp_path):
    payload = _instalacao(tmp_path)
    cfg = carregar_config(tmp_path, payload)
    assert cfg.avisos_de_migracao, "aparecer loja nova sem explicação confunde"
    assert any("CARREFOUR" in m for m in cfg.avisos_de_migracao)


def test_sem_payload_nao_mexe_em_nada(tmp_path):
    """Rodando pela linha de comando não há seed separado: nada a mesclar."""
    import shutil

    shutil.copy2(RAIZ / "config.padrao.json", tmp_path / "config.padrao.json")
    shutil.copy2(RAIZ / "categorias.csv", tmp_path / "categorias.csv")
    cfg = carregar_config(tmp_path)
    assert cfg.avisos_de_migracao == []


def test_atualizacao_desfaz_a_loja_manual_que_ela_mesma_criou(tmp_path):
    """A v1.1.1 cadastrou MART MINAS e EPA como coluna para digitar.

    A regra geral é "atualização nunca remove", que protege dado do cliente.
    Mas estas duas foram criadas pela própria atualização, então desfazê-las é
    consertar, não apagar trabalho dele.
    """
    def com_manuais(c):
        for loja in ("MART MINAS", "EPA"):
            c["lojas"][loja] = {"tipo": "manual", "endereco": "", "ativa": True}
            c["estatisticas"]["lojas"].append(loja)

    payload = _instalacao(tmp_path, com_manuais)
    cfg = carregar_config(tmp_path, payload)
    assert "MART MINAS" not in cfg.lojas
    assert "EPA" not in cfg.lojas
    assert "MART MINAS" not in cfg.colunas_estatistica()
    assert any("removida" in m for m in cfg.avisos_de_migracao)


def test_nao_remove_se_o_cliente_transformou_em_loja_de_verdade(tmp_path):
    """Se ele descobriu um endereço que funciona, o cadastro é dele."""
    def cliente_achou_a_loja(c):
        c["lojas"]["MART MINAS"] = {
            "tipo": "vtex", "endereco": "https://loja.martminas.com.br",
            "ativa": True,
        }
    payload = _instalacao(tmp_path, cliente_achou_a_loja)
    cfg = carregar_config(tmp_path, payload)
    assert "MART MINAS" in cfg.lojas
    assert cfg.lojas["MART MINAS"].tipo == "vtex"


def test_dialogo_de_lojas_testa_antes_de_cadastrar():
    """Cadastrar loja errada não dá erro: a coluna fica vazia.

    Antes só se descobria depois de esperar a busca inteira — meia hora para
    saber que o endereço estava errado.
    """
    fonte = (RAIZ / "buscaprecos" / "gui.py").read_text(encoding="utf-8")
    inicio = fonte.index("def _incluir(self) -> None:")
    corpo = fonte[inicio:fonte.index("def _decidir_inclusao", inicio)]
    assert "_testar_loja" in corpo, "o cadastro tem que consultar a loja"
    assert "adicionar_loja" not in corpo, (
        "não pode cadastrar antes de saber se a loja responde"
    )


def test_inclusao_so_cadastra_depois_do_resultado():
    fonte = (RAIZ / "buscaprecos" / "gui.py").read_text(encoding="utf-8")
    assert "def _decidir_inclusao" in fonte
    inicio = fonte.index("def _decidir_inclusao")
    corpo = fonte[inicio:inicio + 1400]
    assert "_cadastrar" in corpo
    assert "askyesno" in corpo, "loja que falhou o teste exige confirmação"


# --------------------------------------------------------------------------- #
# Aviso de loja que não preenche
# --------------------------------------------------------------------------- #

def _config_com_uma_loja(tmp_path, tipo="vtex"):
    import shutil

    shutil.copy2(RAIZ / "config.padrao.json", tmp_path / "config.padrao.json")
    shutil.copy2(RAIZ / "categorias.csv", tmp_path / "categorias.csv")
    cfg = carregar_config(tmp_path)
    for nome in list(cfg.lojas):
        if nome != "SUPERNOSSO":
            cfg.lojas.pop(nome)
    cfg.lojas["SUPERNOSSO"].tipo = tipo
    cfg.busca["pausa_entre_itens_seg"] = 0
    return cfg


class ClienteQueNuncaAcha:
    """Responde sem erro e nunca encontra — o caso silencioso.

    Pior que erro: o disjuntor não abre, a busca vai até o fim, e a coluna
    chega vazia na planilha sem nenhuma explicação.
    """

    def __init__(self, *a, **k):
        self.consultas = 0

    def buscar(self, ean, descricao, *, min_score, relaxed):
        self.consultas += 1
        return None


def test_avisa_no_meio_da_busca_quando_a_loja_nao_acha_nada(tmp_path, monkeypatch):
    """Sem isso o cliente só descobre olhando a planilha no fim de meia hora."""
    from buscaprecos import busca as mod

    cfg = _config_com_uma_loja(tmp_path)
    monkeypatch.setattr(
        mod.Buscador, "_criar_cliente", lambda self, loja: ClienteQueNuncaAcha()
    )
    linhas = [
        {cfg.colunas["descricao"]: f"PRODUTO {i}",
         cfg.colunas["ean"]: "7894900010015"}
        for i in range(60)
    ]
    avisos: list[str] = []
    buscador = mod.Buscador(cfg, ao_avisar=lambda t, n: avisos.append(t))
    buscador.executar(linhas)

    precoces = [a for a in avisos if "primeiros" in a]
    assert precoces, "tem que avisar no meio, não só no fim"
    assert "SUPERNOSSO" in precoces[0]
    assert str(mod.LIMITE_AVISO_LOJA_VAZIA) in precoces[0]


def test_avisa_uma_vez_so(tmp_path, monkeypatch):
    """Repetir o aviso a cada produto viraria ruído e esconderia o resto."""
    from buscaprecos import busca as mod

    cfg = _config_com_uma_loja(tmp_path)
    monkeypatch.setattr(
        mod.Buscador, "_criar_cliente", lambda self, loja: ClienteQueNuncaAcha()
    )
    linhas = [
        {cfg.colunas["descricao"]: f"PRODUTO {i}",
         cfg.colunas["ean"]: "7894900010015"}
        for i in range(80)
    ]
    avisos: list[str] = []
    mod.Buscador(cfg, ao_avisar=lambda t, n: avisos.append(t)).executar(linhas)
    assert len([a for a in avisos if "primeiros" in a]) == 1


def test_loja_que_acha_nao_gera_aviso(tmp_path, monkeypatch):
    from buscaprecos import busca as mod
    from buscaprecos.lojas import Achado

    class ClienteQueAcha(ClienteQueNuncaAcha):
        def buscar(self, ean, descricao, *, min_score, relaxed):
            return Achado("Produto Achado", 9.99, 200.0, "SUPERNOSSO", "ean")

    cfg = _config_com_uma_loja(tmp_path)
    monkeypatch.setattr(
        mod.Buscador, "_criar_cliente", lambda self, loja: ClienteQueAcha()
    )
    linhas = [
        {cfg.colunas["descricao"]: f"PRODUTO {i}",
         cfg.colunas["ean"]: "7894900010015"}
        for i in range(30)
    ]
    avisos: list[str] = []
    resultado = mod.Buscador(cfg, ao_avisar=lambda t, n: avisos.append(t)).executar(linhas)
    assert avisos == []
    assert resultado.lojas_vazias() == []


def test_resumo_nomeia_as_lojas_que_ficaram_vazias(tmp_path, monkeypatch):
    from buscaprecos import busca as mod

    cfg = _config_com_uma_loja(tmp_path)
    monkeypatch.setattr(
        mod.Buscador, "_criar_cliente", lambda self, loja: ClienteQueNuncaAcha()
    )
    linhas = [{cfg.colunas["descricao"]: "PRODUTO",
               cfg.colunas["ean"]: "7894900010015"}]
    resultado = mod.Buscador(cfg).executar(linhas)
    assert "sem nenhum preço: SUPERNOSSO" in resultado.resumo()
