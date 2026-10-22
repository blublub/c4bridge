@echo off
rem Windows counterpart to run.sh. Settings: %USERPROFILE%\.config\c4bridge\config.env
rem Start über "python -m uvicorn" statt uvicorn.exe: die .exe-Starter im venv haben den Python-Pfad
rem fest eingebrannt und zeigen nach einem Verschieben des Projekts weiter auf den alten Ordner.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo .venv fehlt. Einmalig einrichten:
    echo   python -m venv .venv
    echo   .venv\Scripts\python -m pip install -r requirements.txt
    exit /b 1
)
if "%BRIDGE_HOST%"=="" set BRIDGE_HOST=127.0.0.1
if "%BRIDGE_PORT%"=="" set BRIDGE_PORT=8080
".venv\Scripts\python.exe" -m uvicorn c4bridge.app:app --host %BRIDGE_HOST% --port %BRIDGE_PORT% --no-access-log
