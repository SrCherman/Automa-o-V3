@echo off
setlocal enableextensions enabledelayedexpansion

cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo [AVISO] Ambiente virtual nao encontrado. Executando instalador...
    call "installer\Instalar_Produto.bat"
)

call "venv\Scripts\activate.bat"

echo ========================================================
echo   INICIANDO CREDITOBOT PRO V2 - PRODUTO EM PRODUCAO
echo ========================================================
echo.
echo Painel Web: http://localhost:8000
echo.

start "" "http://localhost:8000"
python -m uvicorn src.app:app --host 0.0.0.0 --port 8000

pause
