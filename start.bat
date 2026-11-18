@echo off
REM ===========================================================
REM  Jira Time Tracker 3 - unico ponto de entrada.
REM
REM  Primeira vez na maquina: chama o instalador, que pergunta
REM  antes de instalar qualquer coisa e cria o atalho.
REM  Depois disso: abre o programa e sai, sem deixar janela.
REM
REM  No dia a dia nem e preciso vir aqui - use o atalho
REM  "Jira Time Tracker 3" na area de trabalho.
REM ===========================================================
setlocal
title Jira Time Tracker 3
cd /d "%~dp0"

REM Ambiente isolado, fora do OneDrive. pythonw = sem janela de console.
set "VENV_DIR=%USERPROFILE%\.jira-time-tracker\venv\Scripts"
set "PYW=%VENV_DIR%\pythonw.exe"
set "PY=%VENV_DIR%\python.exe"

REM -SeNecessario sai calado quando ja esta tudo certo. Ele cobre tanto a
REM primeira instalacao quanto a atualizacao: se esta copia for mais nova que
REM a instalada (dependencias novas, ou pasta diferente), o instalador roda.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1" -SeNecessario
if errorlevel 2 exit /b 0
if not exist "%PY%" (
    echo.
    echo   A instalacao nao terminou. Veja as mensagens acima.
    echo.
    pause
    exit /b 1
)

REM start "" solta o processo: este .bat termina e a janela some.
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0app.py"
) else (
    start "" "%PY%" "%~dp0app.py"
)

exit /b 0
