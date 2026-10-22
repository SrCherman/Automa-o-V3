@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "BASE_DIR=%~dp0"
set "PIPELINE_DIR=%BASE_DIR%credit_pipeline"
set "PYTHON_EXE=%BASE_DIR%venv\Scripts\python.exe"
set "EDGE_EXE=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
set "MIND7_PROFILE=%BASE_DIR%edge_profile_mind7"
set "ITAU_PROFILE=%BASE_DIR%edge_profile_itau"
set "MIND7_PORT=9223"
set "ITAU_PORT=9224"
set "CREDLINE_START_DELAY=45"

if not exist "%PIPELINE_DIR%\processar.py" (
  echo [ERRO] Arquivo nao encontrado: "%PIPELINE_DIR%\processar.py"
  pause
  exit /b 1
)

if not exist "%PYTHON_EXE%" (
  set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

if not exist "%EDGE_EXE%" (
  echo [ERRO] Microsoft Edge nao encontrado em "%EDGE_EXE%"
  pause
  exit /b 1
)

echo ========================================================
echo   AUTOMACAO MINIMALISTA - PLANILHA MANUAL
echo ========================================================
echo.

echo Escolha o modo:
echo [1] Rodar tudo (Telefone + Enriquecimento CPF + Itau + C6)
echo [2] Somente Etapa 1 (Coleta Mind7)
echo [3] Consulta Itau (com enriquecimento obrigatorio)
echo [4] Apenas abrir navegadores (nao executa Python)
echo [5] Etapa 02 - Enriquecimento por CPF
echo [6] Consulta C6 (com enriquecimento obrigatorio)
echo.
set /p MODO=Digite a opcao e pressione ENTER: 

if "%MODO%"=="1" goto RUN_ALL
if "%MODO%"=="2" goto RUN_MIND7
if "%MODO%"=="3" goto RUN_CREDLINE
if "%MODO%"=="4" goto OPEN_ONLY
if "%MODO%"=="5" goto RUN_ENRIQUECIMENTO
if "%MODO%"=="6" goto RUN_C6

echo [ERRO] Opcao invalida.
pause
exit /b 1

:OPEN_BROWSERS
echo.
echo Abrindo Edge (Mind7 + Credline)...
if not exist "%MIND7_PROFILE%" mkdir "%MIND7_PROFILE%"
if not exist "%ITAU_PROFILE%" mkdir "%ITAU_PROFILE%"
start "Edge Mind7" "%EDGE_EXE%" --remote-debugging-port=%MIND7_PORT% --user-data-dir="%MIND7_PROFILE%" "https://mind-7.org/acesso/"
start "Edge Credline" "%EDGE_EXE%" --remote-debugging-port=%ITAU_PORT% --user-data-dir="%ITAU_PROFILE%" "https://credlineitau.com.br/"
echo Aguardando carregamento inicial (30s)...
timeout /t 30 /nobreak >nul
goto :eof

:RUN_ALL
call :OPEN_BROWSERS
echo.
echo Iniciando todas as etapas...
cd /d "%PIPELINE_DIR%"
"%PYTHON_EXE%" -u processar.py
goto END

:RUN_MIND7
call :OPEN_BROWSERS
echo.
echo Iniciando ETAPA 1...
cd /d "%PIPELINE_DIR%"
"%PYTHON_EXE%" -u processar.py --etapa1
goto END

:RUN_CREDLINE
call :OPEN_BROWSERS
echo.
echo Iniciando ETAPA 2...
cd /d "%PIPELINE_DIR%"
"%PYTHON_EXE%" -u processar.py --etapa2
goto END

:OPEN_ONLY
call :OPEN_BROWSERS
echo.
echo Navegadores abertos. Execucao Python nao iniciada.
set "EXIT_CODE=0"
goto SUMMARY

:RUN_ENRIQUECIMENTO
call :OPEN_BROWSERS
cd /d "%PIPELINE_DIR%"
"%PYTHON_EXE%" -u processar.py --enriquecimento
goto END

:RUN_C6
call :OPEN_BROWSERS
cd /d "%PIPELINE_DIR%"
"%PYTHON_EXE%" -u processar.py --c6
goto END

:END
set "EXIT_CODE=%ERRORLEVEL%"

:SUMMARY
echo.
if "%EXIT_CODE%"=="0" (
  echo [OK] Execucao finalizada.
) else (
  echo [ERRO] Execucao finalizada com codigo %EXIT_CODE%.
)

echo.
pause
exit /b %EXIT_CODE%
