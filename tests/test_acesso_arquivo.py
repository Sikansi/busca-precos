"""Testes da explicação de erro de acesso a arquivo.

Vindo de um caso real: o cliente escolheu uma planilha em
`C:\\Users\\ADM\\OneDrive\\Documentos\\teste.xlsx` e o programa respondeu
`[Errno 13] Permission denied`. Isso não diz nada a quem não programa, e as
três causas prováveis têm soluções completamente diferentes:

* arquivo aberto no Excel → fechar
* arquivo só na nuvem (OneDrive sob demanda) → "sempre manter neste dispositivo"
* pasta protegida pela Proteção contra Ransomware → liberar o executável

Chutar a causa errada custa uma rodada de suporte. O programa passa a
investigar os sinais em vez de adivinhar.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from buscaprecos.planilha import (
    arquivo_de_trava,
    esta_somente_na_nuvem,
    explicar_erro_de_arquivo,
)

NEGADO = PermissionError(13, "Permission denied")


@pytest.fixture
def planilha(tmp_path):
    caminho = tmp_path / "teste.xlsx"
    caminho.write_bytes(b"conteudo")
    return caminho


# --------------------------------------------------------------------------- #
# Trava do Excel
# --------------------------------------------------------------------------- #

def test_detecta_arquivo_de_trava_do_excel(planilha):
    assert arquivo_de_trava(planilha) is None
    (planilha.parent / "~$teste.xlsx").write_bytes(b"")
    assert arquivo_de_trava(planilha) is not None


def test_trava_do_excel_e_a_causa_apontada(planilha):
    """É a causa mais comum, então tem que vir primeiro."""
    (planilha.parent / "~$teste.xlsx").write_bytes(b"")
    recado = explicar_erro_de_arquivo(planilha, NEGADO)
    assert "ABERTO no Excel" in recado
    assert "Feche a planilha" in recado


def test_ordem_coloca_a_causa_mais_provavel_primeiro(tmp_path):
    """Com trava e pasta protegida, a trava vem antes."""
    pasta = tmp_path / "OneDrive" / "Documentos"
    pasta.mkdir(parents=True)
    arquivo = pasta / "teste.xlsx"
    arquivo.write_bytes(b"x")
    (pasta / "~$teste.xlsx").write_bytes(b"")
    recado = explicar_erro_de_arquivo(arquivo, NEGADO)
    assert recado.index("Excel") < recado.index("Ransomware")


# --------------------------------------------------------------------------- #
# Pasta protegida
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("pedaco", [
    "OneDrive/Documentos", "Documents", "Desktop", "Área de trabalho",
])
def test_pasta_protegida_pelo_defender_e_mencionada(tmp_path, pedaco):
    pasta = tmp_path.joinpath(*pedaco.split("/"))
    pasta.mkdir(parents=True)
    arquivo = pasta / "teste.xlsx"
    arquivo.write_bytes(b"x")
    assert "Ransomware" in explicar_erro_de_arquivo(arquivo, NEGADO)


def test_pasta_comum_nao_menciona_ransomware(tmp_path):
    """Falar de tudo sempre é o mesmo que não falar de nada."""
    pasta = tmp_path / "BuscaPrecos"
    pasta.mkdir()
    arquivo = pasta / "teste.xlsx"
    arquivo.write_bytes(b"x")
    recado = explicar_erro_de_arquivo(arquivo, NEGADO)
    assert "Ransomware" not in recado
    assert "aberta em outro programa" in recado


# --------------------------------------------------------------------------- #
# Arquivo só na nuvem
# --------------------------------------------------------------------------- #

def test_arquivo_local_nao_e_tratado_como_nuvem(planilha):
    assert esta_somente_na_nuvem(planilha) is False


def test_atributo_de_nuvem_gera_instrucao_do_onedrive(planilha, monkeypatch):
    """No Windows o atributo existe; em Linux, não — daí o monkeypatch."""
    import buscaprecos.planilha as mod

    monkeypatch.setattr(mod, "esta_somente_na_nuvem", lambda c: True)
    recado = mod.explicar_erro_de_arquivo(planilha, NEGADO)
    assert "apenas na nuvem" in recado
    assert "Sempre manter neste dispositivo" in recado


def test_fora_do_windows_nao_quebra(planilha):
    """`st_file_attributes` só existe no Windows."""
    assert esta_somente_na_nuvem(planilha) in (True, False)


# --------------------------------------------------------------------------- #
# Outros erros
# --------------------------------------------------------------------------- #

def test_erro_que_nao_e_de_permissao_passa_direto(planilha):
    recado = explicar_erro_de_arquivo(planilha, ValueError("planilha corrompida"))
    assert "ValueError" in recado
    assert "Ransomware" not in recado


def test_recado_nao_expoe_errno_cru(planilha):
    """"[Errno 13] Permission denied" é jargão; o cliente precisa de ação."""
    recado = explicar_erro_de_arquivo(planilha, NEGADO)
    assert "Errno" not in recado
    assert "negou o acesso" in recado


def test_gui_mostra_o_recado_na_tela_e_nao_so_no_log():
    """Só no log o cliente não vê e liga para o desenvolvedor."""
    fonte = (Path(__file__).resolve().parent.parent
             / "buscaprecos" / "gui.py").read_text(encoding="utf-8")
    inicio = fonte.index("def _conferir_colunas")
    corpo = fonte[inicio:inicio + 1600]
    assert "explicar_erro_de_arquivo" in corpo
    assert "showerror" in corpo


def test_leitura_da_planilha_na_thread_tambem_explica():
    fonte = (Path(__file__).resolve().parent.parent
             / "buscaprecos" / "gui.py").read_text(encoding="utf-8")
    inicio = fonte.index("Planilha.carregar")
    corpo = fonte[max(0, inicio - 400):inicio + 700]
    assert "explicar_erro_de_arquivo" in corpo, (
        "abrir a planilha na busca também pode dar acesso negado"
    )
