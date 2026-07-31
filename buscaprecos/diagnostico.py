"""Diagnóstico de ambiente — roda na máquina onde o problema aparece.

Existe porque validar o Araújo de um único IP e concluir que o caminho HTTP
era confiável foi generalizar de amostra de um: o WAF se comporta diferente
por rede, e só a máquina onde o programa roda pode dizer a verdade.

Fica dentro do payload de propósito — assim chega ao cliente por atualização,
sem regerar o executável, e ele consegue rodar pelo botão da interface sem
instalar Python nem nada.

Não altera nada. Só consulta e relata.
"""

from __future__ import annotations

import json
import platform
import sys
import time
from pathlib import Path

LINHAS: list[str] = []


_AO_PROGREDIR: Any = None


def diga(texto: str = "") -> None:
    print(texto, flush=True)
    LINHAS.append(texto)
    if _AO_PROGREDIR is not None:
        _AO_PROGREDIR(texto)


def secao(titulo: str) -> None:
    diga()
    diga("=" * 66)
    diga(titulo)
    diga("=" * 66)


# --------------------------------------------------------------------------- #

def ambiente(raiz: Path) -> None:
    secao("AMBIENTE")
    diga(f"sistema        : {platform.platform()}")
    diga(f"python         : {sys.version.split()[0]} ({sys.executable})")
    diga(f"congelado(.exe): {getattr(sys, 'frozen', False)}")
    diga(f"pasta          : {raiz}")
    for pacote in ("requests", "urllib3", "rapidfuzz", "openpyxl", "playwright"):
        try:
            mod = __import__(pacote)
            diga(f"{pacote:14} : {getattr(mod, '__version__', '?')}")
        except ImportError:
            diga(f"{pacote:14} : NÃO INSTALADO")


def arquivos(raiz: Path, raiz_payload: Path | None) -> None:
    secao("ARQUIVOS E CODIFICAÇÃO")
    try:
        from buscaprecos.config import carregar_config
        from buscaprecos.planilha import cabecalhos_de, codificacao_de, detectar_colunas

        cfg = carregar_config(raiz, raiz_payload)
    except Exception as exc:
        diga(f"não consegui carregar a configuração: {type(exc).__name__}: {exc}")
        return

    diga(f"config.json    : {cfg.arquivo}")
    diga(f"CEP            : {cfg.cep}")
    for chave in ("planilha", "estoque", "categorias"):
        try:
            caminho = cfg.caminho(chave)
        except KeyError:
            continue
        if not caminho.exists():
            diga(f"{chave:14} : FALTANDO — {caminho}")
            continue
        tamanho = caminho.stat().st_size // 1024
        extra = ""
        if caminho.suffix.lower() in {".csv", ".txt"}:
            extra = f", codificação {codificacao_de(caminho)}"
        diga(f"{chave:14} : {caminho.name} ({tamanho} KB{extra})")

    try:
        caminho = cfg.caminho("planilha")
        if caminho.exists():
            heads = cabecalhos_de(caminho)
            mapa, faltando = detectar_colunas(heads, cfg.colunas)
            diga(f"cabeçalhos     : {len(heads)} colunas")
            for papel, coluna in mapa.items():
                diga(f"  {papel:16} -> {coluna}")
            if faltando:
                diga(f"  NÃO ENCONTRADAS: {', '.join(faltando)}")
    except Exception as exc:
        diga(f"erro lendo a planilha: {type(exc).__name__}: {exc}")


EAN_TESTE = "7894900010015"
DESC_TESTE = "REFRIG COCA COLA LT 350ML ORIG"


def lojas(raiz: Path, raiz_payload: Path | None) -> None:
    secao("LOJAS (uma consulta cada)")
    try:
        from buscaprecos.busca import Buscador
        from buscaprecos.config import carregar_config

        cfg = carregar_config(raiz, raiz_payload)
        buscador = Buscador(cfg)
    except Exception as exc:
        diga(f"não consegui montar o buscador: {type(exc).__name__}: {exc}")
        return

    for loja in cfg.lojas.values():
        if not loja.consulta_rede:
            continue
        inicio = time.monotonic()
        try:
            cliente = buscador._criar_cliente(loja)
            achado = cliente.buscar(EAN_TESTE, DESC_TESTE, min_score=40, relaxed=True)
            decorrido = time.monotonic() - inicio
            if achado:
                diga(f"{loja.nome:18} OK   {decorrido:5.1f}s  via={achado.via:5} "
                     f"{achado.preco_formatado()}  {achado.nome[:38]}")
            else:
                diga(f"{loja.nome:18} VAZIO {decorrido:4.1f}s  respondeu mas não achou")
        except Exception as exc:
            decorrido = time.monotonic() - inicio
            diga(f"{loja.nome:18} FALHA {decorrido:4.1f}s  "
                 f"{type(exc).__name__}: {str(exc)[:70]}")
    buscador.fechar()


def araujo() -> None:
    """O ponto em aberto: qual transporte o WAF aceita nesta rede.

    O primeiro diagnóstico mostrou 403 em todas as variações de cabeçalho e
    ritmo no Windows do cliente, e 200 em todas no Linux do desenvolvedor —
    o que descarta ritmo e cabeçalho e aponta para a impressão digital do
    handshake TLS. Este bloco testa os transportes alternativos.
    """
    secao("ARAÚJO — QUAL TRANSPORTE FUNCIONA AQUI")
    import requests

    from .rede import nova_sessao, sessao_curl_cffi, sessao_tls_navegador

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    CABECALHOS = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Referer": "https://www.araujo.com.br/",
        "X-Requested-With": "XMLHttpRequest",
    }
    BASE = "https://www.araujo.com.br"
    BUSCA = (BASE + "/on/demandware.store/Sites-Araujo-Site/pt_BR/"
             "Search-UpdateGrid?q={}&start=0&sz=12")
    EANS = ["7894900010015", "7622300830151", "7896051111016"]

    def medir(rotulo: str, sessao: object) -> bool:
        codigos: list[object] = []
        achou = 0
        for ean in EANS:
            try:
                r = sessao.get(BUSCA.format(ean), headers=CABECALHOS, timeout=30)
                codigos.append(r.status_code)
                if r.status_code == 200 and "productPrice__price" in r.text:
                    achou += 1
            except Exception as exc:
                codigos.append(type(exc).__name__)
            time.sleep(1.5)
        marca = "FUNCIONA" if achou else "bloqueado"
        diga(f"  {rotulo:26} {str(codigos):34} preço: {achou}/{len(EANS)}  {marca}")
        return achou > 0

    diga("(20s de espera entre blocos, para não contaminar o seguinte)")
    diga()

    funcionou: list[str] = []

    diga("1) padrao — requests comum (o que falhou no seu Windows):")
    if medir("padrao", nova_sessao()):
        funcionou.append("padrao")

    time.sleep(20)
    diga("2) tls_navegador — TLS ajustado, só biblioteca padrão:")
    diga("   (se este funcionar, resolve por atualização, sem regerar o .exe)")
    if medir("tls_navegador", sessao_tls_navegador()):
        funcionou.append("tls_navegador")

    time.sleep(20)
    diga("3) curl_cffi — imita a impressão digital do Chrome de fato:")
    sessao_cffi = sessao_curl_cffi()
    if sessao_cffi is None:
        diga("   curl_cffi NÃO INSTALADO — instale com: pip install curl_cffi")
    elif medir("curl_cffi", sessao_cffi):
        funcionou.append("curl_cffi")

    time.sleep(20)
    diga("4) navegador — Playwright com o Edge do Windows:")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        diga("   Playwright NÃO INSTALADO — instale com: pip install playwright")
    else:
        try:
            with sync_playwright() as pw:
                nav = None
                for canal in ("msedge", "chrome", None):
                    try:
                        nav = (pw.chromium.launch(headless=True, channel=canal)
                               if canal else pw.chromium.launch(headless=True))
                        diga(f"   navegador usado: {canal or 'chromium embutido'}")
                        break
                    except Exception:
                        continue
                if nav is None:
                    diga("   nenhum navegador disponível")
                else:
                    pagina = nav.new_page()
                    pagina.goto(BASE, wait_until="domcontentloaded", timeout=60000)
                    achou = 0
                    for ean in EANS:
                        html = pagina.evaluate(
                            "async (u) => (await fetch(u, "
                            "{credentials:'include'})).text()",
                            BUSCA.format(ean),
                        )
                        if "productPrice__price" in html:
                            achou += 1
                        time.sleep(1.0)
                    diga(f"  {'navegador':26} {'':34} preço: {achou}/{len(EANS)}  "
                         f"{'FUNCIONA' if achou else 'bloqueado'}")
                    if achou:
                        funcionou.append("navegador")
                    nav.close()
        except Exception as exc:
            diga(f"   falhou: {type(exc).__name__}: {str(exc)[:90]}")

    diga()
    if funcionou:
        diga(f"CONCLUSÃO: use araujo_transporte = \"{funcionou[0]}\"")
        if funcionou[0] in ("padrao", "tls_navegador"):
            diga("           resolve por atualização de payload, sem regerar o .exe")
        else:
            diga("           precisa regerar o .exe com essa biblioteca incluída")
    else:
        diga("CONCLUSÃO: nenhum transporte disponível passou. Instale curl_cffi")
        diga("           ou playwright e rode o diagnóstico de novo.")
