"""Interface gráfica — Tkinter.

Tkinter e não Qt/Flet por três razões: vem na biblioteca padrão (nada a
instalar), o PyInstaller empacota sem configuração extra, e o executável fica
em ~15 MB em vez de ~120 MB.

A regra que organiza este arquivo: **a busca nunca roda na thread da
interface**. Se rodar, a janela congela, o Windows escreve "não está
respondendo" e o cliente conclui que travou. Então a busca vai para uma thread
de trabalho e conversa com a interface por uma fila, que o Tk consome com
`after()` — o único jeito seguro de tocar em widget a partir de outra thread.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import VERSION
from .busca import Buscador, Progresso
from .cache import CachePrecos
from .config import Config, carregar_config, salvar_config
from .planilha import Planilha
from .regras import COLUNAS_DERIVADAS, calcular_derivadas

PADDING = 10


# --------------------------------------------------------------------------- #
# Mensagens da thread de trabalho para a interface
# --------------------------------------------------------------------------- #

@dataclass
class MsgLog:
    texto: str
    nivel: str = "info"  # info | aviso | erro | ok


@dataclass
class MsgProgresso:
    loja: str
    feitas: int
    total: int
    preenchidos: int


@dataclass
class MsgDiagnostico:
    destino: Path | None
    erro: str | None = None


@dataclass
class MsgFim:
    saida: Path | None
    resumo: str
    avisos: list[str]
    erro: str | None = None


def abrir_no_sistema(caminho: Path) -> None:
    """Abre arquivo ou pasta no programa padrão do sistema."""
    try:
        if sys.platform == "win32":
            import os

            os.startfile(str(caminho))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(caminho)])
        else:
            subprocess.Popen(["xdg-open", str(caminho)])
    except Exception as exc:
        messagebox.showwarning("Não consegui abrir", f"{caminho}\n\n{exc}")


class Janela:
    def __init__(self, raiz_dados: Path, raiz_payload: Path | None = None):
        self.raiz_projeto = raiz_dados
        self.raiz_payload = raiz_payload
        self.cfg: Config = carregar_config(raiz_dados, raiz_payload)
        self.fila: queue.Queue[Any] = queue.Queue()
        self.cancelar = threading.Event()
        self.trabalhando = False
        self.ultima_saida: Path | None = None
        self.barras: dict[str, tuple[ttk.Progressbar, ttk.Label]] = {}

        self.root = tk.Tk()
        self.root.title(f"Busca de Preços {VERSION}")
        self.root.minsize(720, 560)
        self._montar()
        self.root.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self.root.after(100, self._consumir_fila)

    # ------------------------------------------------------------------ #
    # Montagem
    # ------------------------------------------------------------------ #

    def _montar(self) -> None:
        principal = ttk.Frame(self.root, padding=PADDING)
        principal.pack(fill="both", expand=True)
        principal.columnconfigure(0, weight=1)

        self._montar_planilha(principal)
        self._montar_opcoes(principal)
        self._montar_lojas(principal)
        self._montar_acoes(principal)
        self._montar_progresso(principal)
        self._montar_log(principal)
        principal.rowconfigure(5, weight=1)

    def _montar_planilha(self, pai: ttk.Frame) -> None:
        caixa = ttk.LabelFrame(pai, text="Planilha", padding=PADDING)
        caixa.grid(row=0, column=0, sticky="ew", pady=(0, PADDING))
        caixa.columnconfigure(0, weight=1)

        padrao = self.cfg.caminho("planilha")
        self.var_planilha = tk.StringVar(value=str(padrao) if padrao.is_file() else "")
        ttk.Entry(caixa, textvariable=self.var_planilha).grid(
            row=0, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Button(caixa, text="Escolher…", command=self._escolher_planilha).grid(
            row=0, column=1
        )
        ttk.Button(caixa, text="Colunas…", command=self._mapear_colunas).grid(
            row=0, column=2, padx=(8, 0)
        )

        # A planilha de estoque alimenta a coluna da própria loja. Sem um campo
        # aqui, o caminho vinha do config e na máquina do cliente não existia:
        # a coluna ficava vazia sem nenhum aviso.
        ttk.Label(caixa, text="Estoque próprio:").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        estoque = self.cfg.caminho("estoque")
        self.var_estoque = tk.StringVar(value=str(estoque) if estoque.is_file() else "")
        ttk.Entry(caixa, textvariable=self.var_estoque).grid(
            row=2, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Button(caixa, text="Escolher…", command=self._escolher_estoque).grid(
            row=2, column=1
        )

    def _montar_opcoes(self, pai: ttk.Frame) -> None:
        caixa = ttk.LabelFrame(pai, text="Opções", padding=PADDING)
        caixa.grid(row=1, column=0, sticky="ew", pady=(0, PADDING))

        ttk.Label(caixa, text="CEP:").grid(row=0, column=0, sticky="w")
        self.var_cep = tk.StringVar(value=self.cfg.cep)
        ttk.Entry(caixa, textvariable=self.var_cep, width=12).grid(
            row=0, column=1, sticky="w", padx=(4, 20)
        )

        self.var_pass2 = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            caixa,
            text="Só preencher o que está vazio (busca mais flexível)",
            variable=self.var_pass2,
        ).grid(row=0, column=2, sticky="w", padx=(0, 20))

        self.var_cache = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            caixa, text="Aproveitar consultas recentes", variable=self.var_cache
        ).grid(row=1, column=2, sticky="w", pady=(6, 0))

    def _montar_lojas(self, pai: ttk.Frame) -> None:
        caixa = ttk.LabelFrame(pai, text="Onde buscar", padding=PADDING)
        caixa.grid(row=2, column=0, sticky="ew", pady=(0, PADDING))

        self.caixa_onde = caixa
        self.vars_loja: dict[str, tk.BooleanVar] = {}
        for i, loja in enumerate(self.cfg.lojas.values()):
            var = tk.BooleanVar(value=loja.ativa)
            self.vars_loja[loja.nome] = var
            rotulo = loja.nome
            if loja.tipo == "estoque":
                rotulo += "  (planilha de estoque)"
            ttk.Checkbutton(caixa, text=rotulo, variable=var).grid(
                row=i // 3, column=i % 3, sticky="w", padx=(0, 24), pady=2
            )
        linhas = (len(self.cfg.lojas) + 2) // 3
        ttk.Button(caixa, text="Cadastrar lojas…", command=self._abrir_lojas).grid(
            row=linhas, column=0, sticky="w", pady=(8, 0)
        )

    def _montar_acoes(self, pai: ttk.Frame) -> None:
        caixa = ttk.Frame(pai)
        caixa.grid(row=3, column=0, sticky="ew", pady=(0, PADDING))

        self.bt_buscar = ttk.Button(caixa, text="Buscar preços", command=self._iniciar)
        self.bt_buscar.pack(side="left")

        self.bt_cancelar = ttk.Button(
            caixa, text="Cancelar", command=self._cancelar, state="disabled"
        )
        self.bt_cancelar.pack(side="left", padx=(8, 0))

        self.bt_abrir = ttk.Button(
            caixa, text="Abrir planilha", command=self._abrir_saida, state="disabled"
        )
        self.bt_abrir.pack(side="left", padx=(8, 0))

        self.bt_pasta = ttk.Button(
            caixa, text="Abrir pasta", command=self._abrir_pasta, state="disabled"
        )
        self.bt_pasta.pack(side="left", padx=(8, 0))

        self.bt_diagnostico = ttk.Button(
            caixa, text="Diagnóstico", command=self._diagnosticar
        )
        self.bt_diagnostico.pack(side="left", padx=(8, 0))

        self.var_status = tk.StringVar(value="Pronto.")
        ttk.Label(caixa, textvariable=self.var_status).pack(side="right")

    def _montar_progresso(self, pai: ttk.Frame) -> None:
        self.caixa_progresso = ttk.LabelFrame(pai, text="Progresso", padding=PADDING)
        self.caixa_progresso.grid(row=4, column=0, sticky="ew", pady=(0, PADDING))
        self.caixa_progresso.columnconfigure(1, weight=1)

    def _garantir_barra(self, loja: str) -> tuple[ttk.Progressbar, ttk.Label]:
        if loja in self.barras:
            return self.barras[loja]
        linha = len(self.barras)
        ttk.Label(self.caixa_progresso, text=loja).grid(
            row=linha, column=0, sticky="w", padx=(0, 8)
        )
        barra = ttk.Progressbar(self.caixa_progresso, maximum=100)
        barra.grid(row=linha, column=1, sticky="ew", pady=2)
        rotulo = ttk.Label(self.caixa_progresso, text="—", width=22)
        rotulo.grid(row=linha, column=2, sticky="w", padx=(8, 0))
        self.barras[loja] = (barra, rotulo)
        return self.barras[loja]

    def _montar_log(self, pai: ttk.Frame) -> None:
        caixa = ttk.LabelFrame(pai, text="Detalhes", padding=PADDING)
        caixa.grid(row=5, column=0, sticky="nsew")
        caixa.columnconfigure(0, weight=1)
        caixa.rowconfigure(0, weight=1)

        self.texto = tk.Text(caixa, height=10, wrap="word", state="disabled")
        self.texto.grid(row=0, column=0, sticky="nsew")
        barra = ttk.Scrollbar(caixa, orient="vertical", command=self.texto.yview)
        barra.grid(row=0, column=1, sticky="ns")
        self.texto.configure(yscrollcommand=barra.set)

        self.texto.tag_configure("aviso", foreground="#b06000")
        self.texto.tag_configure("erro", foreground="#b00020")
        self.texto.tag_configure("ok", foreground="#006400")

    # ------------------------------------------------------------------ #
    # Interações
    # ------------------------------------------------------------------ #

    def _escolher_planilha(self) -> None:
        caminho = filedialog.askopenfilename(
            title="Escolha a planilha de compras",
            filetypes=[("Planilhas", "*.xlsx *.xlsm *.csv"), ("Todos", "*.*")],
            initialdir=str(self.raiz_projeto),
        )
        if caminho:
            self.var_planilha.set(caminho)
            # Guarda o caminho: sem isso o config seguia apontando para a
            # planilha da máquina de desenvolvimento e o diagnóstico dizia
            # "planilha: FALTANDO" mesmo com o programa funcionando.
            self.cfg.arquivos["planilha"] = caminho
            self._conferir_colunas(Path(caminho))
            self._persistir()

    def _remontar_lojas(self) -> None:
        """Redesenha os checkboxes depois de mexer no cadastro."""
        for filho in self.caixa_onde.winfo_children():
            filho.destroy()
        self.vars_loja.clear()
        for i, loja in enumerate(self.cfg.lojas.values()):
            var = tk.BooleanVar(value=loja.ativa)
            self.vars_loja[loja.nome] = var
            rotulo = loja.nome
            if loja.tipo == "estoque":
                rotulo += "  (planilha de estoque)"
            ttk.Checkbutton(self.caixa_onde, text=rotulo, variable=var).grid(
                row=i // 3, column=i % 3, sticky="w", padx=(0, 24), pady=2
            )
        linhas = (len(self.cfg.lojas) + 2) // 3
        ttk.Button(
            self.caixa_onde, text="Cadastrar lojas…", command=self._abrir_lojas
        ).grid(row=linhas, column=0, sticky="w", pady=(8, 0))

    def _abrir_lojas(self) -> None:
        if self.trabalhando:
            messagebox.showinfo(
                "Busca em andamento", "Termine ou cancele a busca antes de mexer nas lojas."
            )
            return
        # Aplica o que está marcado na tela antes de abrir, para não perder.
        for nome, var in self.vars_loja.items():
            if nome in self.cfg.lojas:
                self.cfg.lojas[nome].ativa = var.get()
        dialogo = DialogoLojas(self.root, self.cfg)
        if dialogo.alterou:
            self._remontar_lojas()
            self._persistir("Cadastro de lojas salvo.")

    def _mapear_colunas(self) -> None:
        caminho = Path(self.var_planilha.get().strip())
        if not caminho.is_file():
            messagebox.showerror("Planilha", "Escolha uma planilha primeiro.")
            return
        from .planilha import cabecalhos_de

        try:
            cabecalhos = cabecalhos_de(caminho)
        except Exception as exc:
            messagebox.showerror("Não consegui ler", f"{type(exc).__name__}: {exc}")
            return
        dialogo = DialogoColunas(self.root, cabecalhos, self.cfg.colunas)
        if dialogo.resultado:
            self.cfg.colunas.update(dialogo.resultado)
            self._persistir("Colunas salvas.")
            self._log("Colunas em uso: " + ", ".join(
                f"{ROTULO_PAPEL.get(k, k)}={v}" for k, v in dialogo.resultado.items()
            ))

    def _conferir_colunas(self, caminho: Path) -> bool:
        """Valida a planilha escolhida. Abre o mapeamento se faltar o essencial."""
        from .planilha import CONSEQUENCIA_SE_FALTAR, cabecalhos_de, detectar_colunas

        try:
            cabecalhos = cabecalhos_de(caminho)
        except Exception as exc:
            self._log(f"Não consegui ler os cabeçalhos: {exc}", "erro")
            return False
        mapa, faltando = detectar_colunas(cabecalhos, self.cfg.colunas)

        if "descricao" in faltando:
            self._log(
                "Não reconheci a coluna de descrição do produto — aponte na tela.",
                "aviso",
            )
            dialogo = DialogoColunas(self.root, cabecalhos, self.cfg.colunas)
            if not dialogo.resultado:
                return False
            self.cfg.colunas.update(dialogo.resultado)
            self._persistir()
            return True

        self.cfg.colunas.update(mapa)
        for papel in faltando:
            self._log(
                f"Sem coluna de {ROTULO_PAPEL.get(papel, papel).lower()}: "
                f"{CONSEQUENCIA_SE_FALTAR.get(papel, 'função reduzida')}.",
                "aviso",
            )
        return True

    def _persistir(self, mensagem: str = "") -> None:
        """Grava config.json. Falha aqui não pode impedir a busca."""
        self.cfg.cep = self.var_cep.get().strip() or self.cfg.cep
        for nome, var in self.vars_loja.items():
            if nome in self.cfg.lojas:
                self.cfg.lojas[nome].ativa = var.get()
        try:
            salvar_config(self.cfg)
            if mensagem:
                self._log(mensagem, "ok")
        except Exception as exc:
            self._log(f"Não consegui salvar as preferências: {exc}", "aviso")

    def _escolher_estoque(self) -> None:
        caminho = filedialog.askopenfilename(
            title="Planilha de estoque da própria loja (planograma)",
            filetypes=[("Planilhas", "*.csv *.xlsx"), ("Todos", "*.*")],
            initialdir=str(self.raiz_projeto),
        )
        if caminho:
            self.var_estoque.set(caminho)
            self.cfg.arquivos["estoque"] = caminho
            self._persistir("Planilha de estoque salva.")

    def _conferir_estoque(self) -> None:
        """Avisa se a coluna da própria loja vai sair vazia."""
        loja = self.cfg.loja_estoque()
        if loja is None or not loja.ativa:
            return
        caminho = self.var_estoque.get().strip()
        if caminho and Path(caminho).is_file():
            self.cfg.arquivos["estoque"] = caminho
            return
        self._log(
            f"Sem a planilha de estoque, a coluna {loja.nome} vai ficar vazia. "
            "Aponte o arquivo em 'Estoque próprio' ou desmarque a loja.",
            "aviso",
        )

    def _log(self, texto: str, nivel: str = "info") -> None:
        self.texto.configure(state="normal")
        self.texto.insert("end", texto + "\n", nivel if nivel != "info" else ())
        self.texto.see("end")
        self.texto.configure(state="disabled")

    def _diagnosticar(self) -> None:
        """Gera o relatório de ambiente para mandar ao desenvolvedor.

        Fica aqui, e não num script separado, porque o script exigiria Python
        e as bibliotecas instaladas — coisa que o cliente não tem. Dentro do
        executável tudo já está disponível.
        """
        if self.trabalhando:
            messagebox.showinfo(
                "Busca em andamento",
                "Termine ou cancele a busca antes de rodar o diagnóstico.",
            )
            return
        if not messagebox.askyesno(
            "Diagnóstico",
            "Vou testar a conexão com cada loja e gerar um relatório para o "
            "desenvolvedor.\n\nLeva cerca de 3 minutos (tem esperas de "
            "propósito). Rodar agora?",
        ):
            return

        self.trabalhando = True
        self.bt_buscar.configure(state="disabled")
        self.bt_diagnostico.configure(state="disabled")
        self.var_status.set("Rodando diagnóstico… (1 a 2 min)")
        self._log("--- diagnóstico: leva 1 a 2 minutos, com esperas "
                  "propositais entre os testes ---")

        def trabalhar() -> None:
            # TUDO dentro do try, inclusive o import. Com o import fora, um
            # ImportError matava a thread antes do primeiro `put`: nada chegava
            # à interface e a janela ficava "Rodando diagnóstico…" para sempre,
            # com os botões desabilitados. Thread de trabalho que não avisa que
            # morreu é pior que erro na tela.
            try:
                from .diagnostico import executar

                destino = executar(
                    self.raiz_projeto,
                    self.raiz_payload,
                    ao_progredir=lambda linha: self.fila.put(MsgLog(linha)),
                )
                self.fila.put(MsgDiagnostico(destino))
            except BaseException as exc:
                self.fila.put(MsgDiagnostico(
                    None, erro=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
                ))

        threading.Thread(target=trabalhar, daemon=True).start()

    def _cancelar(self) -> None:
        self.cancelar.set()
        self.var_status.set("Cancelando… salvando o que já foi encontrado.")
        self.bt_cancelar.configure(state="disabled")

    def _abrir_saida(self) -> None:
        if self.ultima_saida:
            abrir_no_sistema(self.ultima_saida)

    def _abrir_pasta(self) -> None:
        if self.ultima_saida:
            abrir_no_sistema(self.ultima_saida.parent)

    def _ao_fechar(self) -> None:
        if self.trabalhando:
            if not messagebox.askyesno(
                "Busca em andamento",
                "A busca ainda está rodando. Fechar agora perde o que não foi salvo.\n\n"
                "Fechar mesmo assim?",
            ):
                return
            self.cancelar.set()
        self.root.destroy()

    # ------------------------------------------------------------------ #
    # Execução
    # ------------------------------------------------------------------ #

    def _iniciar(self) -> None:
        caminho = Path(self.var_planilha.get().strip())
        if not caminho.is_file():
            messagebox.showerror("Planilha", "Escolha uma planilha existente.")
            return
        if not any(v.get() for v in self.vars_loja.values()):
            messagebox.showerror("Onde buscar", "Marque pelo menos uma loja.")
            return

        if not self._conferir_colunas(caminho):
            return
        self._conferir_estoque()
        # Aplica as escolhas da tela e as guarda para a próxima abertura.
        self._persistir()

        self.trabalhando = True
        self.cancelar.clear()
        self.ultima_saida = None
        self.bt_buscar.configure(state="disabled")
        self.bt_cancelar.configure(state="normal")
        self.bt_abrir.configure(state="disabled")
        self.bt_pasta.configure(state="disabled")
        for barra, rotulo in self.barras.values():
            barra["value"] = 0
            rotulo.configure(text="—")
        self.var_status.set("Buscando…")

        def envelope() -> None:
            try:
                self._trabalhar(caminho)
            except BaseException as exc:  # rede de segurança da thread
                self.fila.put(MsgFim(
                    saida=None, resumo="", avisos=[],
                    erro=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                ))

        threading.Thread(target=envelope, daemon=True).start()

    def _trabalhar(self, caminho: Path) -> None:
        """Roda na thread de trabalho. Só fala com a interface pela fila."""
        buscador: Buscador | None = None
        cache: CachePrecos | None = None
        planilha: Planilha | None = None
        try:
            self.fila.put(MsgLog(f"Lendo {caminho.name}…"))
            planilha = Planilha.carregar(
                caminho, coluna_chave=self.cfg.colunas["descricao"]
            )
            planilha.limpar_erros_excel(self.cfg.colunas["custo"])
            planilha.garantir_colunas(
                self.cfg.colunas_de_preco(),
                depois_de=["Observações", "Vl Total (R$)"],
            )
            planilha.garantir_colunas(COLUNAS_DERIVADAS)
            self.fila.put(MsgLog(f"{len(planilha.linhas)} produtos na planilha."))
            for aviso in planilha.avisos:
                self.fila.put(MsgLog(aviso, "aviso"))

            if self.var_cache.get():
                cache = CachePrecos(
                    self.cfg.caminho("cache"),
                    validade_horas=float(
                        self.cfg.busca.get("cache_validade_horas", 24)
                    ),
                )

            buscador = Buscador(
                self.cfg,
                cache=cache,
                ao_progredir=lambda p: self.fila.put(
                    MsgProgresso(p.loja, p.feitas, p.total, p.preenchidos)
                ),
                cancelar=self.cancelar,
            )

            lojas = [lj.nome for lj in self.cfg.lojas_para_buscar()]
            if lojas:
                self.fila.put(MsgLog("Consultando: " + ", ".join(lojas)))
                resultado = buscador.executar(
                    planilha.linhas,
                    somente_vazias=self.var_pass2.get(),
                    passe=2 if self.var_pass2.get() else 1,
                )
                self.fila.put(MsgLog(resultado.resumo(), "ok"))
                for loja, motivo in resultado.lojas_desligadas.items():
                    self.fila.put(
                        MsgLog(f"{loja} ficou fora do ar: {motivo}", "erro")
                    )
            else:
                resultado = None

            # Daqui para baixo nada pode descartar o que a busca achou. Uma
            # planilha de estoque ilegível já custou uma execução inteira de
            # 1363 preços: o erro subiu, o `except` engoliu tudo e nenhum
            # arquivo foi salvo. Cada etapa falha sozinha e avisa.
            categorias: dict[str, str] = {}
            estoque = self.cfg.loja_estoque()
            if estoque is not None and estoque.ativa:
                try:
                    n = buscador.aplicar_estoque(planilha.linhas)
                    categorias = buscador.categorias_estoque()
                    self.fila.put(
                        MsgLog(f"{estoque.nome}: {n} preços do estoque próprio.")
                    )
                except Exception as exc:
                    self.fila.put(MsgLog(
                        f"Não consegui ler a planilha de estoque, então a coluna "
                        f"{estoque.nome} ficou vazia — o resto foi salvo normalmente. "
                        f"({type(exc).__name__}: {exc})",
                        "aviso",
                    ))

            try:
                calcular_derivadas(planilha.linhas, self.cfg, categorias)
            except Exception as exc:
                self.fila.put(MsgLog(
                    f"Falhei ao calcular as colunas derivadas; os preços foram "
                    f"salvos sem elas. ({type(exc).__name__}: {exc})",
                    "erro",
                ))

            saida = planilha.salvar(self.cfg.colunas_de_preco() + COLUNAS_DERIVADAS)
            self.fila.put(
                MsgFim(
                    saida=saida,
                    resumo=resultado.resumo() if resultado else "Cálculo concluído.",
                    avisos=list(planilha.avisos),
                )
            )
        except Exception as exc:
            # Última linha de defesa: tenta salvar o que houver em memória.
            saida_emergencia: Path | None = None
            try:
                if planilha is not None:
                    saida_emergencia = planilha.salvar(
                        self.cfg.colunas_de_preco() + COLUNAS_DERIVADAS
                    )
            except Exception:
                pass
            self.fila.put(
                MsgFim(
                    saida=saida_emergencia,
                    resumo="",
                    avisos=[],
                    erro=f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}",
                )
            )
        finally:
            if buscador is not None:
                buscador.fechar()
            if cache is not None:
                cache.close()

    # ------------------------------------------------------------------ #
    # Fila → interface (roda na thread do Tk)
    # ------------------------------------------------------------------ #

    def _consumir_fila(self) -> None:
        try:
            while True:
                msg = self.fila.get_nowait()
                if isinstance(msg, MsgLog):
                    self._log(msg.texto, msg.nivel)
                elif isinstance(msg, MsgProgresso):
                    barra, rotulo = self._garantir_barra(msg.loja)
                    pct = 100 * msg.feitas / msg.total if msg.total else 100
                    barra["value"] = pct
                    rotulo.configure(
                        text=f"{msg.feitas}/{msg.total} — {msg.preenchidos} preços"
                    )
                elif isinstance(msg, MsgDiagnostico):
                    self._fim_diagnostico(msg)
                elif isinstance(msg, MsgFim):
                    self._finalizar(msg)
        except queue.Empty:
            pass
        self.root.after(100, self._consumir_fila)

    def _fim_diagnostico(self, msg: MsgDiagnostico) -> None:
        self.trabalhando = False
        self.bt_buscar.configure(state="normal")
        self.bt_diagnostico.configure(state="normal")
        if msg.erro:
            self.var_status.set("Diagnóstico falhou.")
            self._log(msg.erro, "erro")
            return
        self.var_status.set("Diagnóstico pronto.")
        self._log(f"Relatório salvo em: {msg.destino}", "ok")
        if msg.destino and messagebox.askyesno(
            "Diagnóstico pronto",
            f"Relatório salvo em:\n{msg.destino}\n\n"
            "Mande este arquivo para o desenvolvedor.\n\nAbrir a pasta agora?",
        ):
            abrir_no_sistema(msg.destino.parent)

    def _finalizar(self, msg: MsgFim) -> None:
        self.trabalhando = False
        self.bt_buscar.configure(state="normal")
        self.bt_cancelar.configure(state="disabled")

        if msg.erro:
            self.var_status.set("Falhou.")
            self._log(msg.erro, "erro")
            if msg.saida:
                self.ultima_saida = msg.saida
                self.bt_abrir.configure(state="normal")
                self.bt_pasta.configure(state="normal")
                self._log(f"Mesmo assim salvei o que havia em: {msg.saida.name}", "ok")
            messagebox.showerror(
                "Deu erro",
                "A busca não terminou."
                + (f"\n\nO que já tinha sido encontrado foi salvo em "
                   f"{msg.saida.name}." if msg.saida else "")
                + "\n\nO detalhe está na área de Detalhes — copie e me mande.",
            )
            return

        self.ultima_saida = msg.saida
        for aviso in msg.avisos:
            self._log(aviso, "aviso")
        if msg.saida:
            self._log(f"Salvo em: {msg.saida.name}", "ok")
            self.bt_abrir.configure(state="normal")
            self.bt_pasta.configure(state="normal")
        self.var_status.set(
            "Cancelado — resultado parcial salvo."
            if self.cancelar.is_set()
            else "Concluído."
        )

    # ------------------------------------------------------------------ #

    def executar(self) -> None:
        self._log(f"Busca de Preços {VERSION}")
        self._log(
            "Escolha a planilha, confira as lojas e clique em Buscar preços. "
            "A planilha original não é alterada: o resultado sai em um arquivo novo."
        )
        self.root.mainloop()


def main(
    raiz_dados: Path | None = None, raiz_payload: Path | None = None
) -> int:
    raiz = raiz_dados or Path.cwd()
    try:
        Janela(raiz, raiz_payload).executar()
    except Exception as exc:
        try:
            messagebox.showerror(
                "Não consegui abrir", f"{type(exc).__name__}: {exc}"
            )
        except Exception:
            print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------------------------------- #
# Diálogo: mapear colunas da planilha
# --------------------------------------------------------------------------- #

ROTULO_PAPEL = {
    "descricao": "Descrição do produto",
    "ean": "Código de barras (EAN)",
    "codigo_interno": "Código interno / SKU",
    "custo": "Custo unitário",
    "markup_planilha": "Markup",
}

SEM_COLUNA = "— não tem —"


class DialogoColunas(tk.Toplevel):
    """Aponta qual coluna da planilha é o quê.

    Existe porque exigir os nomes exatos ("Descrição do Produto", "Vl Unit.
    (R$)") faz o programa quebrar com qualquer exportação diferente. A
    detecção automática acerta na maioria dos casos; esta tela é a saída para
    o resto, sem depender de mim para editar o config.
    """

    def __init__(self, pai: tk.Misc, cabecalhos: list[str], atual: dict[str, str]):
        super().__init__(pai)
        self.title("Colunas da planilha")
        self.resultado: dict[str, str] | None = None
        self.transient(pai)
        self.resizable(False, False)

        from .planilha import CONSEQUENCIA_SE_FALTAR, detectar_colunas

        detectado, _faltando = detectar_colunas(cabecalhos, atual)
        opcoes = [SEM_COLUNA] + list(cabecalhos)

        corpo = ttk.Frame(self, padding=PADDING)
        corpo.pack(fill="both", expand=True)
        ttk.Label(
            corpo,
            text="Confira o que o programa reconheceu. Só a descrição é obrigatória.",
            wraplength=520,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, PADDING))

        self.vars: dict[str, tk.StringVar] = {}
        for i, papel in enumerate(ROTULO_PAPEL, start=1):
            obrigatorio = papel == "descricao"
            texto = ROTULO_PAPEL[papel] + (" *" if obrigatorio else "")
            ttk.Label(corpo, text=texto).grid(row=i, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=detectado.get(papel, SEM_COLUNA))
            self.vars[papel] = var
            ttk.Combobox(
                corpo, textvariable=var, values=opcoes, state="readonly", width=38
            ).grid(row=i, column=1, sticky="ew", padx=(8, 0), pady=3)
            if not obrigatorio and papel in CONSEQUENCIA_SE_FALTAR:
                ttk.Label(
                    corpo,
                    text=CONSEQUENCIA_SE_FALTAR[papel],
                    foreground="#666666",
                    wraplength=340,
                ).grid(row=i, column=2, sticky="w", padx=(8, 0))

        acoes = ttk.Frame(corpo)
        acoes.grid(row=len(ROTULO_PAPEL) + 1, column=0, columnspan=3,
                   sticky="e", pady=(PADDING, 0))
        ttk.Button(acoes, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(acoes, text="Usar estas colunas", command=self._confirmar).pack(
            side="right", padx=(0, 8)
        )

        self.grab_set()
        self.wait_window()

    def _confirmar(self) -> None:
        escolhido = {
            papel: var.get() for papel, var in self.vars.items()
            if var.get() and var.get() != SEM_COLUNA
        }
        if "descricao" not in escolhido:
            messagebox.showerror(
                "Falta a descrição",
                "Sem a coluna de descrição do produto não há o que buscar.",
                parent=self,
            )
            return
        self.resultado = escolhido
        self.destroy()


# --------------------------------------------------------------------------- #
# Diálogo: cadastro de lojas
# --------------------------------------------------------------------------- #

EAN_TESTE = "7894900010015"  # Coca-Cola lata 350ml — existe em qualquer mercado
DESC_TESTE = "REFRIG COCA COLA LT 350ML"


class DialogoLojas(tk.Toplevel):
    """Cadastro de lojas.

    O cliente pode incluir lojas das plataformas já suportadas (VipCommerce,
    VTEX) sem depender de atualização — é só nome, tipo e endereço. Uma
    plataforma nova precisa de código.

    O botão Testar existe porque cadastrar errado falha silenciosamente: a
    loja fica sempre vazia e parece que o produto não existe. O teste faz uma
    consulta real antes de salvar.
    """

    def __init__(self, pai: tk.Misc, cfg: Config):
        super().__init__(pai)
        self.title("Lojas")
        self.cfg = cfg
        self.alterou = False
        self.transient(pai)
        self.minsize(640, 420)

        from .config import TIPOS_LOJA

        self.tipos = TIPOS_LOJA

        corpo = ttk.Frame(self, padding=PADDING)
        corpo.pack(fill="both", expand=True)
        corpo.columnconfigure(0, weight=1)
        corpo.rowconfigure(1, weight=1)

        ttk.Label(
            corpo,
            text=("Você pode incluir lojas VipCommerce e VTEX aqui mesmo. "
                  "Outras plataformas precisam de atualização do programa."),
            wraplength=600,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, PADDING))

        self.lista = ttk.Treeview(
            corpo, columns=("tipo", "endereco", "ativa"), show="tree headings", height=9
        )
        self.lista.heading("#0", text="Loja")
        self.lista.heading("tipo", text="Plataforma")
        self.lista.heading("endereco", text="Endereço")
        self.lista.heading("ativa", text="Buscar?")
        self.lista.column("#0", width=150)
        self.lista.column("tipo", width=80)
        self.lista.column("endereco", width=250)
        self.lista.column("ativa", width=70, anchor="center")
        self.lista.grid(row=1, column=0, sticky="nsew")
        self.lista.bind("<<TreeviewSelect>>", self._ao_selecionar)

        botoes = ttk.Frame(corpo)
        botoes.grid(row=1, column=1, sticky="n", padx=(PADDING, 0))
        ttk.Button(botoes, text="Remover", command=self._remover).pack(
            fill="x", pady=(0, 4)
        )
        ttk.Button(botoes, text="Ativar/Desativar", command=self._alternar).pack(
            fill="x", pady=(0, 4)
        )
        self.bt_testar = ttk.Button(botoes, text="Testar", command=self._testar)
        self.bt_testar.pack(fill="x")

        self._montar_formulario(corpo)

        self.var_teste = tk.StringVar(value="")
        ttk.Label(corpo, textvariable=self.var_teste, wraplength=600).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(PADDING, 0)
        )

        rodape = ttk.Frame(corpo)
        rodape.grid(row=4, column=0, columnspan=2, sticky="e", pady=(PADDING, 0))
        ttk.Button(rodape, text="Fechar", command=self.destroy).pack(side="right")

        self._recarregar()
        self.grab_set()
        self.wait_window()

    def _montar_formulario(self, pai: ttk.Frame) -> None:
        caixa = ttk.LabelFrame(pai, text="Incluir loja", padding=PADDING)
        caixa.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(PADDING, 0))
        caixa.columnconfigure(1, weight=1)

        ttk.Label(caixa, text="Nome:").grid(row=0, column=0, sticky="w")
        self.var_nome = tk.StringVar()
        ttk.Entry(caixa, textvariable=self.var_nome).grid(
            row=0, column=1, sticky="ew", padx=(4, 8)
        )

        ttk.Label(caixa, text="Plataforma:").grid(row=0, column=2, sticky="w")
        self.var_tipo = tk.StringVar(value="vtex")
        combo = ttk.Combobox(
            caixa, textvariable=self.var_tipo,
            values=["vip", "vtex"], state="readonly", width=8,
        )
        combo.grid(row=0, column=3, padx=(4, 0))
        combo.bind("<<ComboboxSelected>>", lambda _e: self._dica())

        ttk.Label(caixa, text="Endereço:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.var_endereco = tk.StringVar()
        ttk.Entry(caixa, textvariable=self.var_endereco).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=(4, 8), pady=(6, 0)
        )
        ttk.Button(caixa, text="Incluir", command=self._incluir).grid(
            row=1, column=3, pady=(6, 0)
        )

        self.var_dica = tk.StringVar()
        ttk.Label(caixa, textvariable=self.var_dica, foreground="#666666").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )
        self._dica()

    def _dica(self) -> None:
        self.var_dica.set(self.tipos.get(self.var_tipo.get(), ""))

    def _recarregar(self) -> None:
        self.lista.delete(*self.lista.get_children())
        for loja in self.cfg.lojas.values():
            self.lista.insert(
                "", "end", iid=loja.nome, text=loja.nome,
                values=(loja.tipo, loja.endereco or "—", "sim" if loja.ativa else "não"),
            )

    def _selecionada(self) -> str | None:
        sel = self.lista.selection()
        return sel[0] if sel else None

    def _ao_selecionar(self, _evento: object = None) -> None:
        nome = self._selecionada()
        pode = bool(nome) and self.cfg.lojas[nome].consulta_rede
        self.bt_testar.configure(state="normal" if pode else "disabled")

    def _incluir(self) -> None:
        try:
            loja = self.cfg.adicionar_loja(
                self.var_nome.get(), self.var_tipo.get(), self.var_endereco.get()
            )
        except ValueError as exc:
            messagebox.showerror("Não deu", str(exc), parent=self)
            return
        self.alterou = True
        self.var_nome.set("")
        self.var_endereco.set("")
        self._recarregar()
        self.var_teste.set(
            f"{loja.nome} incluída. Clique em Testar antes de rodar a busca — "
            "endereço errado deixa a coluna sempre vazia."
        )

    def _remover(self) -> None:
        nome = self._selecionada()
        if not nome:
            return
        if not messagebox.askyesno(
            "Remover",
            f"Remover {nome}? A coluna continua na planilha, mas deixa de ser "
            "preenchida e sai da média de mercado.",
            parent=self,
        ):
            return
        self.cfg.remover_loja(nome)
        self.alterou = True
        self._recarregar()

    def _alternar(self) -> None:
        nome = self._selecionada()
        if not nome:
            return
        self.cfg.lojas[nome].ativa = not self.cfg.lojas[nome].ativa
        self.alterou = True
        self._recarregar()
        self.lista.selection_set(nome)

    def _testar(self) -> None:
        nome = self._selecionada()
        if not nome:
            return
        loja = self.cfg.lojas[nome]
        self.bt_testar.configure(state="disabled")
        self.var_teste.set(f"Consultando {nome}…")

        def trabalhar() -> None:
            from .busca import Buscador

            try:
                buscador = Buscador(self.cfg)
                cliente = buscador._criar_cliente(loja)
                achado = cliente.buscar(
                    EAN_TESTE, DESC_TESTE, min_score=40, relaxed=True
                )
                buscador.fechar()
                if achado:
                    msg = (f"{nome} respondeu: {achado.nome} — "
                           f"{achado.preco_formatado()} (via {achado.via}).")
                else:
                    msg = (f"{nome} respondeu, mas não achou o produto de teste. "
                           "O endereço pode estar certo e o catálogo diferente.")
            except Exception as exc:
                msg = f"{nome} falhou: {type(exc).__name__}: {exc}"
            self.after(0, lambda: self._fim_teste(msg))

        threading.Thread(target=trabalhar, daemon=True).start()

    def _fim_teste(self, msg: str) -> None:
        self.var_teste.set(msg)
        self._ao_selecionar()
