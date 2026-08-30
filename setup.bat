@echo off
setlocal EnableDelayedExpansion

:: %~dp0 is the directory that contains this .bat file (with trailing \).
:: All paths are anchored to it so the script works from any location,
:: including directories that contain spaces.
set "DIR=%~dp0"

echo.
echo ============================================================
echo  Live Transcriber -- Setup
echo ============================================================
echo.

:: ----------------------------------------------------------------
:: 1. Find Python 3.12+
::    Prefer the Windows Python Launcher (py) which ships with all
::    official Python installers and picks the right version.
:: ----------------------------------------------------------------

py --version >nul 2>&1
if errorlevel 1 goto :try_python_cmd

py -c "import sys; exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1
if errorlevel 1 (
    echo Python was found but the version is too old.
    echo Python 3.12 or newer is required.
    echo.
    py --version
    echo.
    echo Download Python 3.12+ from:
    echo   https://www.python.org/downloads/
    goto :error_exit
)
set "PYTHON=py"
goto :found_python

:try_python_cmd
python --version >nul 2>&1
if errorlevel 1 goto :no_python

python -c "import sys; exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1
if errorlevel 1 (
    echo Python was found but the version is too old.
    echo Python 3.12 or newer is required.
    echo.
    python --version
    echo.
    echo Download Python 3.12+ from:
    echo   https://www.python.org/downloads/
    goto :error_exit
)
set "PYTHON=python"
goto :found_python

:no_python
echo Python 3.12 or newer was not found on this computer.
echo.
echo Download and install Python from:
echo   https://www.python.org/downloads/
echo.
echo During installation, tick "Add Python to PATH".
goto :error_exit

:found_python
for /f "tokens=*" %%v in ('%PYTHON% --version 2^>^&1') do set "PY_VER=%%v"
echo Python: !PY_VER!
echo.

:: ----------------------------------------------------------------
:: 2. Create virtual environment
:: ----------------------------------------------------------------

if exist "%DIR%.venv\Scripts\python.exe" (
    echo Virtual environment already exists -- reusing it.
) else (
    echo Creating virtual environment in .venv ...
    %PYTHON% -m venv "%DIR%.venv"
    if !errorlevel! neq 0 (
        echo.
        echo ERROR: Failed to create virtual environment.
        goto :error_exit
    )
    echo Done.
)
echo.

:: ----------------------------------------------------------------
:: 3. Upgrade pip
:: ----------------------------------------------------------------

echo Upgrading pip ...
"%DIR%.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
echo Done.
echo.

:: ----------------------------------------------------------------
:: 4. Install dependencies
:: ----------------------------------------------------------------

echo Installing dependencies from requirements.txt ...
echo (This may take a minute on the first run)
echo.
"%DIR%.venv\Scripts\pip.exe" install -r "%DIR%requirements.txt"
if !errorlevel! neq 0 (
    echo.
    echo ERROR: Dependency installation failed.
    echo See the output above for details.
    echo.
    echo Tip: run setup.bat from a Command Prompt to see full output.
    goto :error_exit
)
echo.

:: ----------------------------------------------------------------
:: 5. Verify sounddevice / PortAudio
::    sounddevice bundles PortAudio DLLs for Windows -- no separate
::    install is needed.  The check below catches the uncommon case
::    where the Microsoft Visual C++ Redistributable is missing.
:: ----------------------------------------------------------------

echo Verifying audio (sounddevice / PortAudio) ...
"%DIR%.venv\Scripts\python.exe" -c "import sounddevice; sounddevice.query_devices()" >nul 2>&1
if !errorlevel! neq 0 (
    echo.
    echo WARNING: sounddevice could not load PortAudio.
    echo This usually means the Microsoft Visual C++ Redistributable
    echo is missing.  Download and install it from:
    echo   https://aka.ms/vs/17/release/vc_redist.x64.exe
    echo.
    echo Setup will continue.  Audio capture may not work until the
    echo redistributable is installed.
    echo.
) else (
    echo Audio: OK
)
echo.

:: ----------------------------------------------------------------
:: 6. Create .env (copy from template only if it does not exist)
:: ----------------------------------------------------------------

if exist "%DIR%.env" (
    echo .env already exists -- keeping your existing configuration.
) else (
    copy "%DIR%.env.template" "%DIR%.env" >nul
    echo Created .env from template.
    echo.
    echo  ^>^> Open .env and enter your API key(s) before starting the app.
)
echo.

:: ----------------------------------------------------------------
:: 7. Create required directories
::    main.py also creates logs\ at runtime, but we create both here
::    so the user can see them immediately after setup.
:: ----------------------------------------------------------------

if not exist "%DIR%logs\"     mkdir "%DIR%logs"
if not exist "%DIR%sessions\" mkdir "%DIR%sessions"
echo Directories: OK
echo.

:: ----------------------------------------------------------------
:: Done
:: ----------------------------------------------------------------

echo ============================================================
echo  Setup complete!
echo ============================================================
echo.
echo Next steps:
echo   1. Open .env and add your Deepgram and/or OpenAI API key(s).
echo   2. Double-click run.bat to start Live Transcriber.
echo.
pause
exit /b 0

:: ----------------------------------------------------------------
:error_exit
echo.
echo Setup did not complete successfully.
echo.
pause
exit /b 1
