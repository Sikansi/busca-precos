"""Sessão HTTP com retry, backoff e disjuntor por loja.

O app roda na máquina do cliente sem ninguém olhando: uma loja fora do ar
não pode derrubar a execução inteira nem travar por minutos. Daí duas
camadas — retry para falha momentânea, disjuntor para loja que caiu de vez.
"""

from __future__ import annotations

import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def nova_sessao(tentativas: int = 3) -> requests.Session:
    """Sessão com retry automático em erro de rede e HTTP 5xx/429."""
    sessao = requests.Session()
    sessao.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    retry = Retry(
        total=tentativas,
        connect=tentativas,
        read=tentativas,
        status=tentativas,
        backoff_factor=0.6,  # 0.6s, 1.2s, 2.4s
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
    sessao.mount("https://", adapter)
    sessao.mount("http://", adapter)
    return sessao


class LojaIndisponivel(RuntimeError):
    """Disjuntor aberto: a loja falhou demais e foi desligada nesta execução."""


class LimiteDeTaxa(RuntimeError):
    """A loja pediu para ir mais devagar (403/429/503 de WAF).

    Precisa ser distinta de "loja quebrada": limite de taxa é temporário e a
    resposta certa é esperar, não desligar a loja. Tratar os dois igual foi o
    que fez o Araújo sair do ar depois de 8 itens e devolver 0 preços.
    """

    def __init__(self, loja: str, termo: str, espera_sugerida: float = 60.0):
        super().__init__(
            f"{loja} pediu para desacelerar ao buscar {termo!r}; "
            f"aguardando {espera_sugerida:.0f}s"
        )
        self.loja = loja
        self.termo = termo
        self.espera_sugerida = espera_sugerida


class Disjuntor:
    """Depois de N falhas seguidas, para de tentar a loja e registra o motivo.

    Sem isso, uma loja que mudou de API custa `linhas × tentativas × timeout`
    — em 400 linhas isso é meia hora esperando erro conhecido.
    """

    def __init__(self, limite: int = 8):
        self.limite = limite
        self._falhas: dict[str, int] = {}
        self._motivo: dict[str, str] = {}
        self._lock = threading.Lock()

    def aberto(self, loja: str) -> bool:
        with self._lock:
            return self._falhas.get(loja, 0) >= self.limite

    def registrar_falha(self, loja: str, erro: BaseException) -> None:
        with self._lock:
            self._falhas[loja] = self._falhas.get(loja, 0) + 1
            self._motivo[loja] = f"{type(erro).__name__}: {erro}"[:200]

    def registrar_sucesso(self, loja: str) -> None:
        with self._lock:
            self._falhas[loja] = 0

    def desligar(self, loja: str, erro: BaseException) -> None:
        """Abre o disjuntor de uma vez — usado quando a loja nem autentica."""
        with self._lock:
            self._falhas[loja] = self.limite
            self._motivo[loja] = f"{type(erro).__name__}: {erro}"[:200]

    def desligadas(self) -> dict[str, str]:
        """Lojas que estouraram o limite, com o último erro observado."""
        with self._lock:
            return {
                loja: self._motivo.get(loja, "?")
                for loja, n in self._falhas.items()
                if n >= self.limite
            }


# --------------------------------------------------------------------------- #
# Transportes alternativos (para WAF que barra cliente Python)
# --------------------------------------------------------------------------- #

# Ordem de cifras que o Chrome oferece. O WAF do Araújo devolve 403 para
# `requests` no Windows e 200 no Linux, com os mesmos cabeçalhos e o mesmo
# ritmo — o que sobra é a impressão digital do handshake TLS (JA3): o servidor
# vê um "Chrome 120" cujo handshake é de Python/OpenSSL e recusa.
#
# Isto ajusta a parte da impressão que o Python deixa configurar. Não é
# equivalente a um navegador (a lista de extensões e curvas continua sendo a
# do OpenSSL), mas é o único caminho que roda sem dependência nova — ou seja,
# o único que chega ao cliente por atualização de payload, sem regerar o .exe.
CIFRAS_NAVEGADOR = ":".join([
    "ECDHE-ECDSA-AES128-GCM-SHA256",
    "ECDHE-RSA-AES128-GCM-SHA256",
    "ECDHE-ECDSA-AES256-GCM-SHA384",
    "ECDHE-RSA-AES256-GCM-SHA384",
    "ECDHE-ECDSA-CHACHA20-POLY1305",
    "ECDHE-RSA-CHACHA20-POLY1305",
    "ECDHE-RSA-AES128-SHA",
    "ECDHE-RSA-AES256-SHA",
    "AES128-GCM-SHA256",
    "AES256-GCM-SHA384",
    "AES128-SHA",
    "AES256-SHA",
])


class _AdaptadorTlsNavegador(HTTPAdapter):
    """HTTPAdapter com contexto TLS parecido com o de navegador."""

    def init_poolmanager(self, *args: object, **kwargs: object) -> object:
        import ssl

        contexto = ssl.create_default_context()
        try:
            contexto.set_ciphers(CIFRAS_NAVEGADOR)
        except ssl.SSLError:
            # OpenSSL desta máquina não conhece alguma cifra da lista. Segue
            # com o contexto padrão em vez de derrubar a busca — o transporte
            # seguinte assume se este não passar pelo WAF.
            contexto = ssl.create_default_context()
        # Navegador não manda compressão TLS nem renegociação legada.
        contexto.options |= getattr(ssl, "OP_NO_COMPRESSION", 0)
        contexto.options |= getattr(ssl, "OP_NO_TICKET", 0)
        kwargs["ssl_context"] = contexto  # type: ignore[index]
        return super().init_poolmanager(*args, **kwargs)  # type: ignore[arg-type]


def sessao_tls_navegador(tentativas: int = 3) -> requests.Session:
    """Sessão com handshake TLS ajustado. Sem dependência nova."""
    sessao = nova_sessao(tentativas)
    adaptador = _AdaptadorTlsNavegador(
        max_retries=sessao.adapters["https://"].max_retries,
        pool_connections=8,
        pool_maxsize=8,
    )
    sessao.mount("https://", adaptador)
    return sessao


def sessao_curl_cffi(impersonate: str = "chrome") -> object | None:
    """Sessão do curl_cffi, que imita a impressão digital do Chrome de fato.

    É a opção confiável para este tipo de bloqueio, mas é dependência binária:
    precisa entrar no executável, então exige regerar o `.exe` — não chega por
    atualização de payload. Devolve None se não estiver instalada.
    """
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        return None
    return curl_requests.Session(impersonate=impersonate)
