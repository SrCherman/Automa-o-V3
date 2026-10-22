@echo off
title Etapa 1 - Busca de CPFs (Mind7)
echo ==============================================
echo INICIANDO ETAPA 1 - BUSCA DE CPFs (MIND7)
echo ==============================================
cd /d "%~dp0"
python processar.py --etapa1
echo.
pause
