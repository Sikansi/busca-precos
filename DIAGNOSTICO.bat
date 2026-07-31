@echo off
REM Dois cliques aqui gera o diagnostico.txt para mandar ao desenvolvedor.
REM Funciona tanto na pasta do fonte quanto na pasta do programa instalado.
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  === Diagnostico Busca de Precos ===
echo  Isto leva uns 2 minutos (tem pausas de proposito).
echo.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" diagnostico.py
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo  [X] Python nao encontrado. Rode este arquivo na pasta do
        echo      codigo-fonte, onde existe a pasta .venv.
        pause
        exit /b 1
    )
    python diagnostico.py
)
echo.
echo  Pronto. Mande o arquivo diagnostico.txt gerado nesta pasta.
pause
