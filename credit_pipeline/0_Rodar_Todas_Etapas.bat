@echo off
title Rodar Todas as Etapas Automaticamente
echo ==============================================
echo INICIANDO PIPELINE COMPLETO DE AUTOMACAO
echo As etapas 1, 2 e 3 serao executadas em sequencia.
echo ==============================================
cd /d "%~dp0"
python processar.py
echo.
echo ==============================================
echo PIPELINE COMPLETO FINALIZADO!
echo ==============================================
pause
