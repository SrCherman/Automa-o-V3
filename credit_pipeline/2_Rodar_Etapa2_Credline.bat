@echo off
title Etapa 2 - Consulta de Credito (Credline)
echo ==============================================
echo INICIANDO ETAPA 2 - CONSULTA DE CREDITO
echo ==============================================
cd /d "%~dp0"
python processar.py --etapa2
echo.
pause
