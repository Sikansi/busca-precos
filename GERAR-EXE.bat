@echo off
REM ===================================================================
REM  Gera o BuscaPrecos.exe. Dois cliques neste arquivo, no Windows.
REM
REM  Isto e para VOCE (desenvolvedor), nao para o cliente. O cliente
REM  recebe a pasta pronta e nunca abre terminal.
REM
REM  Precisa de Python 3.10+ instalado, com "Add python.exe to PATH"
REM  marcado na instalacao. Se nao tiver, o script avisa e para.
REM ===================================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo  === Gerar BuscaPrecos.exe ===
echo.

REM --- 1. Python instalado? -----------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo  [X] Python nao encontrado no PATH.
    echo.
    echo      Instale de https://www.python.org/downloads/
    echo      IMPORTANTE: marque "Add python.exe to PATH" na instalacao.
    echo.
    echo      Alternativa sem instalar nada: suba o projeto no GitHub e
    echo      use o workflow "Build Windows" em Actions - ele gera o .exe
    echo      na nuvem e voce so baixa o resultado.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  [1/5] Python %PYVER% encontrado.

REM --- 2. Versao a gerar --------------------------------------------
set VERSAO=%1
if "%VERSAO%"=="" set /p VERSAO=  Versao a gerar (ex: 1.0.0):
if "%VERSAO%"=="" (
    echo  [X] Sem versao, nao da para continuar.
    pause
    exit /b 1
)

REM --- 3. Ambiente virtual ------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo  [2/5] Criando ambiente virtual...
    python -m venv .venv
    if errorlevel 1 goto :erro
) else (
    echo  [2/5] Ambiente virtual ja existe.
)

echo  [3/5] Instalando dependencias...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt pyinstaller pytest
if errorlevel 1 goto :erro

REM --- 4. Testes antes de empacotar ---------------------------------
echo  [4/5] Rodando os testes...
".venv\Scripts\python.exe" -m pytest tests\ -q
if errorlevel 1 (
    echo.
    echo  [X] Os testes falharam. Nao vou empacotar codigo quebrado.
    echo      Corrija primeiro, ou me mande a saida acima.
    pause
    exit /b 1
)

REM --- 5. Empacotar -------------------------------------------------
echo  [5/5] Empacotando (isto leva alguns minutos)...
".venv\Scripts\python.exe" build.py %VERSAO%
if errorlevel 1 goto :erro

echo.
echo  === Pronto ===
echo.
echo  O programa esta em:  dist\BuscaPrecos\
echo  Teste dando dois cliques em:  dist\BuscaPrecos\BuscaPrecos.exe
echo.
echo  Para mandar ao cliente: compacte a pasta dist\BuscaPrecos inteira
echo  (a pasta toda, nao so o .exe - ele precisa de _internal e payload).
echo.
echo  Abrindo a pasta...
start "" "dist\BuscaPrecos"
pause
exit /b 0

:erro
echo.
echo  [X] Algo falhou acima. Copie a mensagem e me mande.
pause
exit /b 1
