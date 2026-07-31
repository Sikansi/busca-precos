# -*- mode: python ; coding: utf-8 -*-
"""Receita do PyInstaller.

Decisões que importam:

* **`--onedir`, não `--onefile`.** O onefile descompacta tudo num temporário a
  cada abertura (uns 10s de espera) e é o padrão que mais dispara antivírus.
  O onedir abre na hora.
* **`console=False`.** Sem isso o Windows abre um prompt preto atrás da janela.
* **O payload NÃO entra aqui.** `buscaprecos/` é copiado pelo `build.py` para
  `payload/<versão>/`, fora do executável, porque é justamente o que a
  atualização precisa substituir com o app aberto. Se estivesse embutido, cada
  correção exigiria gerar e reenviar o .exe.
* **`hiddenimports`** cobre o que o PyInstaller não vê por análise estática:
  os módulos do payload são importados por caminho em runtime, então as
  dependências deles precisam ser declaradas na mão.

Build:  pyinstaller BuscaPrecos.spec --noconfirm
"""

bloco_cifra = None

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Usadas pelo payload, que é importado em runtime.
        "requests",
        "urllib3",
        "rapidfuzz",
        "rapidfuzz.fuzz",
        "rapidfuzz.process",
        "openpyxl",
        "openpyxl.workbook",
        "openpyxl.reader.excel",
        "sqlite3",
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        # Todo o pacote, para o analisador não deixar nada de fora.
        "buscaprecos",
        "buscaprecos.atualizacao",
        "buscaprecos.busca",
        "buscaprecos.cache",
        "buscaprecos.config",
        "buscaprecos.gui",
        "buscaprecos.lojas",
        "buscaprecos.planilha",
        "buscaprecos.precos",
        "buscaprecos.rede",
        "buscaprecos.regras",
        "buscaprecos.texto",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Playwright é opcional (modo navegador do Araújo) e pesa ~150 MB.
    # Fica fora do executável; quem precisar instala por fora.
    excludes=[
        "playwright",
        "pytest",
        "matplotlib",
        "numpy",
        "pandas",
        "PIL",
        "PyQt5",
        "PySide6",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=bloco_cifra)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BuscaPrecos",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX aumenta muito o falso-positivo de antivírus
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # coloque "icone.ico" aqui quando tiver
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BuscaPrecos",
)
