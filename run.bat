@echo off
setlocal

:: %~dp0 is the directory of this .bat file (with trailing \).
set "DIR=%~dp0"

:: ----------------------------------------------------------------
:: Check that setup has been run
:: ----------------------------------------------------------------

if not exist "%DIR%.venv\Scripts\python.exe" (
    echo.
    echo .venv not found.  Please run setup.bat first.
    echo.
    pause
    exit /b 1
)

:: ----------------------------------------------------------------
:: Launch the application
:: ----------------------------------------------------------------

echo.
echo Starting Live Transcriber ...
echo Logs are written to logs\live_transcriber.log
echo Close this window to stop the application.
echo.
"%DIR%.venv\Scripts\python.exe" "%DIR%main.py"

:: ----------------------------------------------------------------
:: Keep the console open so the user can see any exit message or error
:: ----------------------------------------------------------------

echo.
echo Application has exited.
pause
