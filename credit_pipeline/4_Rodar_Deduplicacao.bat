@echo off
title De-duplicar Planilha de Contatos
echo ==============================================
echo INICIANDO EXCLUSAO DE DUPLICIDADES DA PLANILHA
echo ==============================================
cd /d "%~dp0"
python processar.py --dedup
echo.
pause
