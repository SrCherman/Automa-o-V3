@echo off
title Etapa 3 - Enriquecimento de Dados (Mind7 CPF)
echo ==============================================
echo INICIANDO ETAPA 3 - ENRIQUECIMENTO DE DADOS
echo ==============================================
cd /d "%~dp0"
python processar.py --etapa3
echo.
pause
