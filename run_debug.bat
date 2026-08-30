@echo off
setlocal

:: %~dp0 is the directory of this .bat file (with trailing \).
set "DIR=%~dp0"

if not exist "%DIR%.venv\Scripts\python.exe" (
    echo.
    echo .venv not found.  Please run setup.bat first.
    echo.
    pause
    exit /b 1
)

:: Force verbose logging regardless of what .env says
set LOG_LEVEL=DEBUG

echo.
echo Starting Live Transcriber (DEBUG mode)
echo LOG_LEVEL=DEBUG  --  all log output is shown below and in logs\live_transcriber.log
echo.
"%DIR%.venv\Scripts\python.exe" "%DIR%main.py"

echo.
echo Application has exited.
pause
