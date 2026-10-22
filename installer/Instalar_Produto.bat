@echo off
setlocal enableextensions enabledelayedexpansion

echo ========================================================
echo   INSTALADOR AUTOMATIZADO - CREDITOBOT PRO V2
echo ========================================================
echo.

cd /d "%~dp0.."

echo [1/4] Verificando instalacao do Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao foi encontrado no seu sistema.
    echo Por favor, instale o Python 3.10 ou superior e marque "Add Python to PATH".
    pause
    exit /b 1
)

echo [2/4] Criando ambiente virtual Python (venv)...
if not exist "venv" (
    python -m venv venv
)

echo [3/4] Instalando dependencias de producao...
call "venv\Scripts\activate.bat"
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt

echo [4/4] Inicializando Banco de Dados e estrutura...
python -c "from src.database.connection import init_db; init_db(); print('Banco de dados inicializado com sucesso!')"

echo.
echo ========================================================
echo   INSTALACAO CONCLUIDA COM SUCESSO!
echo   Para iniciar o produto, execute: Iniciar_Produto.bat
echo ========================================================
echo.
pause
