@echo off
chcp 65001 > NUL
title Comparador e Filtrador de Planilhas WhatsApp

echo ============================================================
echo   COMPARADOR E FILTRADOR DE PLANILHAS WHATSAPP
echo ============================================================
echo.

set PYTHON_EXE="C:\Program Files\Python314\python.exe"

if not exist %PYTHON_EXE% (
    set PYTHON_EXE=python
)

echo Executando o comparador de planilhas...
echo.

%PYTHON_EXE% "Comparar planilhas\comparar_planilhas.py" %*

echo.
echo ============================================================
echo Processo finalizado! Pressione qualquer tecla para sair.
echo ============================================================
pause > NUL
