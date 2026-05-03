@echo off
title ChainEX — Launcher
color 0A
echo ============================================
echo   ChainEX — Macro Automation Platform
echo ============================================
echo.

:: ── Check Python is installed ───────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    echo         Download from https://www.python.org/downloads/
    echo         Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

:: ── Install / upgrade dependencies ──────────────────────────────────────────
echo [1/3] Installing Python packages...
python -m pip install --upgrade pip --quiet
python -m pip install -r "%~dp0requirements.txt" --quiet
if errorlevel 1 (
    echo [ERROR] pip install failed. Check your internet connection.
    pause
    exit /b 1
)
echo       Done.

:: ── Run the pywin32 post-install hook (needed once after fresh install) ──────
echo [2/3] Configuring pywin32...
python -c "import win32api" >nul 2>&1
if errorlevel 1 (
    python Scripts\pywin32_postinstall.py -install >nul 2>&1
)
echo       Done.

:: ── Launch ChainEX ───────────────────────────────────────────────────────────
echo [3/3] Opening ChainEX...
echo.
cd /d "%~dp0"

python launcher.py
if errorlevel 1 (
    echo.
    echo [ERROR] ChainEX closed unexpectedly. See above for details.
    pause
)

exit /b 0
