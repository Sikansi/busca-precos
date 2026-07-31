"""Testes de configuração e de leitura de planilha.

Cobrem duas coisas que o cliente faz sozinho e que, quebradas, o deixam sem
saída: ajustar o programa pela interface (e o ajuste sobreviver) e apontar
colunas quando a planilha dele não tem os nomes que eu esperava.
"""

from __future__ import annotations

import json
import shutil

import pytest

from buscaprecos.config import TIPOS_LOJA, carregar_config, salvar_config
from buscaprecos.planilha import detectar_colunas

RAIZ = __import__("pathlib").Path(__file__).resolve().parent.parent


@pytest.fixture
def projeto(tmp_path):
    """Cópia mínima do projeto, no formato de dados do cliente."""
    shutil.copy2(RAIZ / "config.json", tmp_path / "config.padrao.json")
    shutil.copy2(RAIZ / "categorias.csv", tmp_path / "categorias.csv")
    return tmp_path


# --------------------------------------------------------------------------- #
# Persistência
# --------------------------------------------------------------------------- #

def test_primeira_abertura_cria_config_do_padrao(projeto):
    assert not (projeto / "config.json").exists()
    cfg = carregar_config(projeto)
    assert cfg.arquivo == projeto / "config.json"
    assert (projeto / "config.json").exists()


def test_config_do_cliente_fica_fora_do_payload(tmp_path):
    """O `config.json` não pode nascer dentro do payload.

    Se nascer, a atualização — que substitui a pasta do payload — apaga CEP,
    lojas e mapeamento de colunas do cliente.
    """
    dados = tmp_path / "app"
    payload = tmp_path / "app" / "payload" / "1.0.0"
    payload.mkdir(parents=True)
    shutil.copy2(RAIZ / "config.json", payload / "config.padrao.json")
    shutil.copy2(RAIZ / "categorias.csv", payload / "categorias.csv")

    cfg = carregar_config(dados, payload)
    assert cfg.arquivo == dados / "config.json"
    assert not (payload / "config.json").exists()
    # categorias.csv não está nos dados do cliente, então vem do payload
    assert cfg.caminho("categorias").parent == payload


def test_alteracoes_da_interface_sobrevivem_ao_reabrir(projeto):
    cfg = carregar_config(projeto)
    cfg.cep = "31000-000"
    cfg.lojas["ATACADAO"].ativa = False
    cfg.colunas["descricao"] = "PRODUTOS"
    salvar_config(cfg)

    recarregado = carregar_config(projeto)
    assert recarregado.cep == "31000-000"
    assert recarregado.lojas["ATACADAO"].ativa is False
    assert recarregado.colunas["descricao"] == "PRODUTOS"


def test_salvar_config_guarda_copia_de_seguranca(projeto):
    cfg = carregar_config(projeto)
    salvar_config(cfg)
    assert list(projeto.glob("config.bak-*.json")), "config quebrado tranca o app"


# --------------------------------------------------------------------------- #
# Cadastro de lojas
# --------------------------------------------------------------------------- #

def test_cliente_pode_incluir_loja_de_plataforma_conhecida(projeto):
    cfg = carregar_config(projeto)
    loja = cfg.adicionar_loja("supermercados bh", "vtex", "https://www.superbh.com.br")
    assert loja.nome == "SUPERMERCADOS BH"  # normaliza para maiúscula
    assert loja.consulta_rede
    assert "SUPERMERCADOS BH" in cfg.colunas_estatistica()
    salvar_config(cfg)
    assert "SUPERMERCADOS BH" in carregar_config(projeto).lojas


def test_nao_aceita_plataforma_desconhecida(projeto):
    cfg = carregar_config(projeto)
    with pytest.raises(ValueError):
        cfg.adicionar_loja("LOJA X", "shopify", "https://x.com")


def test_nao_aceita_loja_repetida_nem_sem_nome(projeto):
    cfg = carregar_config(projeto)
    with pytest.raises(ValueError):
        cfg.adicionar_loja("VERDEMAR", "vip", "x.com.br")
    with pytest.raises(ValueError):
        cfg.adicionar_loja("   ", "vtex", "https://x.com")


def test_loja_removida_sai_da_estatistica(projeto):
    """Senão a média de mercado segue contando uma coluna que ninguém preenche."""
    cfg = carregar_config(projeto)
    cfg.remover_loja("ATACADAO")
    assert "ATACADAO" not in cfg.lojas
    assert "ATACADAO" not in cfg.colunas_estatistica()


def test_tipos_suportados_tem_cliente_implementado():
    from buscaprecos import lojas

    implementados = {"vip": lojas.ClienteVip, "vtex": lojas.ClienteVtex,
                     "araujo": lojas.ClienteAraujo, "estoque": lojas.ClienteEstoque}
    for tipo in TIPOS_LOJA:
        if tipo == "manual":
            continue
        assert tipo in implementados, f"{tipo} está no menu mas não tem cliente"


# --------------------------------------------------------------------------- #
# Detecção de colunas
# --------------------------------------------------------------------------- #

def test_detecta_o_formato_oficial():
    colunas = ["Local da compra", "Código Interno", "EAN/GTIN",
               "Descrição do Produto", "MARKUP", "Qtde", "Vl Unit. (R$)"]
    mapa, faltando = detectar_colunas(colunas)
    assert faltando == []
    assert mapa["descricao"] == "Descrição do Produto"
    assert mapa["custo"] == "Vl Unit. (R$)"


@pytest.mark.parametrize("cabecalho", [
    "Descrição do Produto", "Descrição Produto", "PRODUTOS", "Produto",
    "Item", "Nome do Produto", "DESCRICAO",
])
def test_reconhece_apelidos_de_descricao(cabecalho):
    mapa, _ = detectar_colunas([cabecalho, "Qtde"])
    assert mapa.get("descricao") == cabecalho


@pytest.mark.parametrize("cabecalho", [
    "EAN/GTIN", "EAN", "GTIN", "Código de barras", "Cód. Barras", "EAN13",
])
def test_reconhece_apelidos_de_ean(cabecalho):
    mapa, _ = detectar_colunas(["Produto", cabecalho])
    assert mapa.get("ean") == cabecalho


def test_aponta_o_que_falta_em_vez_de_quebrar():
    """Planilha sem EAN e sem custo tem que rodar, avisando o que perdeu."""
    mapa, faltando = detectar_colunas(["PRODUTOS", "CATEGORIA", "MARKUP"])
    assert mapa["descricao"] == "PRODUTOS"
    assert set(faltando) == {"ean", "codigo_interno", "custo"}


def test_coluna_ja_configurada_tem_prioridade():
    """Se o cliente apontou à mão, a detecção automática não desfaz."""
    colunas = ["Produto", "Descrição do Produto"]
    mapa, _ = detectar_colunas(colunas, {"descricao": "Produto"})
    assert mapa["descricao"] == "Produto"


def test_consequencia_documentada_para_cada_coluna_opcional():
    from buscaprecos.planilha import APELIDOS, CONSEQUENCIA_SE_FALTAR

    opcionais = set(APELIDOS) - {"descricao"}
    assert opcionais <= set(CONSEQUENCIA_SE_FALTAR), (
        "toda coluna opcional precisa explicar na tela o que se perde sem ela"
    )


# --------------------------------------------------------------------------- #
# Instalação limpa (sem config.json)
# --------------------------------------------------------------------------- #

def test_sem_config_json_o_ota_continua_configurado(tmp_path):
    """A pasta enviada ao cliente não leva `config.json`.

    Ele tem os caminhos das planilhas da máquina de desenvolvimento, que não
    existem na do cliente. Mas a URL de atualização precisa sobreviver a essa
    remoção, senão a instalação nasce sem OTA e nunca recebe correção.
    """
    import json
    import shutil

    dados = tmp_path / "app"
    payload = dados / "payload" / "1.0.5"
    payload.mkdir(parents=True)
    shutil.copy2(RAIZ / "config.padrao.json", payload / "config.padrao.json")
    shutil.copy2(RAIZ / "categorias.csv", payload / "categorias.csv")

    assert not (dados / "config.json").exists()
    cfg = carregar_config(dados, payload)
    assert (dados / "config.json").is_file(), "config.json tem que nascer do padrão"
    assert cfg.atualizacao["url_version_json"].startswith("http"), (
        "sem URL, a instalação nova nunca se atualiza"
    )
    gravado = json.loads((dados / "config.json").read_text(encoding="utf-8"))
    assert gravado["lojas"], "o padrão tem que trazer as lojas cadastradas"


def test_padrao_nao_carrega_caminho_da_maquina_de_desenvolvimento():
    """`planilha` e `estoque` em branco no seed.

    Com os nomes dos meus arquivos, o diagnóstico do cliente reportaria
    "FALTANDO — Compras_Consolidadas…", que é ruído confuso: o arquivo não
    está faltando, ele nunca foi escolhido.
    """
    import json

    padrao = json.loads((RAIZ / "config.padrao.json").read_text(encoding="utf-8"))
    assert padrao["arquivos"]["planilha"] == ""
    assert padrao["arquivos"]["estoque"] == ""


def test_caminho_vazio_nao_vira_a_pasta_do_programa(tmp_path):
    """Caminho em branco resolve para a pasta, e pasta "existe".

    Sem `is_file()`, o campo da tela apareceria preenchido com o caminho de uma
    pasta e o leitor de estoque tentaria abri-la como planilha.
    """
    import shutil

    shutil.copy2(RAIZ / "config.padrao.json", tmp_path / "config.padrao.json")
    shutil.copy2(RAIZ / "categorias.csv", tmp_path / "categorias.csv")
    cfg = carregar_config(tmp_path)
    assert not cfg.caminho("planilha").is_file()
    assert not cfg.caminho("estoque").is_file()


def test_build_leva_o_seed_curado_e_nao_o_config_de_trabalho():
    """O que o `build.py` empacota é o que chega ao cliente.

    Ele copiava `config.json` — o config de trabalho desta máquina, com os
    caminhos das minhas planilhas — em vez de `config.padrao.json`. O teste
    anterior conferia o arquivo curado na raiz, que o build nem usava: validei
    o artefato errado. Este confere a origem declarada.
    """
    import ast

    fonte = (RAIZ / "build.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    mapa = None
    for no in ast.walk(arvore):
        if isinstance(no, ast.Assign) and any(
            getattr(alvo, "id", "") == "ARQUIVOS_PAYLOAD" for alvo in no.targets
        ):
            mapa = ast.literal_eval(no.value)
    assert mapa is not None, "não achei ARQUIVOS_PAYLOAD em build.py"
    assert "config.json" not in mapa, (
        "config.json é o config de trabalho e está no .gitignore: "
        "num clone limpo o payload sairia sem seed"
    )
    assert mapa.get("config.padrao.json") == "config.padrao.json"


def test_seed_curado_esta_versionado():
    """Se o seed estiver no .gitignore, um clone limpo gera app quebrado."""
    import subprocess

    r = subprocess.run(
        ["git", "check-ignore", "config.padrao.json"],
        cwd=RAIZ, capture_output=True, text=True,
    )
    assert r.returncode != 0, "config.padrao.json não pode estar ignorado pelo git"
