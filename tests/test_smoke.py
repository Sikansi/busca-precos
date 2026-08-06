"""Testes de fumaça: o pacote importa e os pontos de entrada existem.

Escritos depois de um erro que os 123 testes anteriores não pegariam: uma
edição que reescreveu uma função truncou o arquivo dali para baixo, e
`diagnostico.executar` desapareceu. As versões 1.0.3 a 1.0.7 foram publicadas
sem ela — nenhum teste importava o módulo, então nada acusou.

Testes de unidade verificam comportamento de função que existe. Estes
verificam que a função existe.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
PACOTE = RAIZ / "buscaprecos"

MODULOS = sorted(p.stem for p in PACOTE.glob("*.py") if p.stem != "__init__")

# Assinatura pública de cada módulo: o que o resto do programa chama. Se algo
# aqui desaparecer, alguma tela ou comando quebra em runtime.
ENTRADAS = {
    "atualizacao": ["checar", "baixar_e_instalar", "ativar", "versao_ativa",
                    "versoes_instaladas", "mais_nova", "gerar_version_json",
                    "url_do_asset", "limpar_antigas"],
    "busca": ["Buscador", "Progresso", "Resultado"],
    "cache": ["CachePrecos", "chave_produto"],
    "config": ["carregar_config", "salvar_config", "Config", "Loja", "TIPOS_LOJA"],
    "diagnostico": ["executar", "ambiente", "arquivos", "lojas", "araujo"],
    "gui": ["Janela", "main", "DialogoColunas", "DialogoLojas"],
    "lojas": ["ClienteVip", "ClienteVtex", "ClienteAraujo", "ClienteEstoque",
              "Achado"],
    "planilha": ["Planilha", "detectar_colunas", "cabecalhos_de", "abrir_texto",
                 "codificacao_de", "explicar_erro_de_arquivo", "arquivo_de_trava",
                 "esta_somente_na_nuvem"],
    "precos": ["format_price", "parse_price_br", "parse_markup", "clean_ean",
               "is_valid_ean", "fmt_pct"],
    "rede": ["nova_sessao", "Disjuntor", "LimiteDeTaxa", "sessao_tls_navegador",
             "sessao_curl_cffi"],
    "regras": ["calcular_derivadas", "COLUNAS_DERIVADAS", "detectar_discrepantes",
               "resolver_markup"],
    "texto": ["normalize", "score_match", "validate_candidate",
              "measures_compatible", "fully_compatible", "brand_tokens"],
}


@pytest.mark.parametrize("nome", MODULOS)
def test_modulo_importa(nome):
    """Arquivo truncado no meio de uma função nem compila."""
    importlib.import_module(f"buscaprecos.{nome}")


@pytest.mark.parametrize("nome", MODULOS)
def test_modulo_nao_esta_truncado(nome):
    """Compila como Python válido e a última definição está completa.

    A primeira versão daqui checava com que caractere o arquivo termina, o que
    é heurística: acusou um módulo íntegro que acabava em f-string. `ast.parse`
    já rejeita arquivo cortado no meio de qualquer construção — é a garantia de
    verdade. O resto confirma que sobrou conteúdo executável.
    """
    fonte = (PACOTE / f"{nome}.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)  # SyntaxError se cortado no meio
    definicoes = [
        n for n in arvore.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                          ast.Assign, ast.AnnAssign))
    ]
    assert definicoes, f"buscaprecos.{nome} não tem nada além de imports"


@pytest.mark.parametrize("modulo,nomes", sorted(ENTRADAS.items()))
def test_pontos_de_entrada_existem(modulo, nomes):
    mod = importlib.import_module(f"buscaprecos.{modulo}")
    faltando = [n for n in nomes if not hasattr(mod, n)]
    assert not faltando, f"buscaprecos.{modulo} perdeu: {', '.join(faltando)}"


def test_todo_modulo_do_pacote_esta_coberto():
    """Módulo novo tem que entrar em ENTRADAS, senão a rede tem buraco."""
    assert set(MODULOS) == set(ENTRADAS), (
        f"sem cobertura: {set(MODULOS) ^ set(ENTRADAS)}"
    )


def test_executar_do_diagnostico_tem_a_assinatura_que_a_gui_usa():
    """A GUI chama executar(raiz, raiz_payload, ao_progredir=…)."""
    from buscaprecos.diagnostico import executar

    parametros = inspect.signature(executar).parameters
    assert "raiz" in parametros
    assert "raiz_payload" in parametros
    assert "ao_progredir" in parametros


def test_gui_importa_executar_dentro_do_try():
    """Import fora do try mata a thread antes do primeiro aviso.

    Foi assim que a janela ficou "Rodando diagnóstico…" para sempre: o
    ImportError subiu, a thread morreu, nada chegou à fila e os botões
    continuaram desabilitados.
    """
    fonte = (PACOTE / "gui.py").read_text(encoding="utf-8")
    inicio = fonte.index("def trabalhar() -> None:")
    corpo = fonte[inicio:inicio + 700]
    pos_try = corpo.index("try:")
    pos_import = corpo.index("from .diagnostico import executar")
    assert pos_import > pos_try, "o import tem que estar dentro do try"


@pytest.mark.parametrize("script", ["app.py", "main.py", "build.py",
                                    "diagnostico.py"])
def test_scripts_da_raiz_compilam(script):
    ast.parse((RAIZ / script).read_text(encoding="utf-8"))
