@echo off
title ChainEX — Build
color 0A
echo ============================================
echo   ChainEX — PyInstaller Build
echo ============================================
echo.

:: ── Check Python ─────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    pause
    exit /b 1
)

:: ── Install / upgrade PyInstaller ────────────────────────────────────────────
echo [1/3] Installing PyInstaller...
python -m pip install --upgrade pyinstaller --quiet
if errorlevel 1 (
    echo [ERROR] Could not install PyInstaller.
    pause
    exit /b 1
)
echo       Done.

:: ── Clean previous build ─────────────────────────────────────────────────────
echo [2/3] Cleaning previous build artefacts...
cd /d "%~dp0"
if exist dist\ChainEX rmdir /s /q dist\ChainEX
if exist build\ChainEX rmdir /s /q build\ChainEX
echo       Done.

:: ── Run PyInstaller ──────────────────────────────────────────────────────────
echo [3/3] Building ChainEX...
echo.
pyinstaller ChainEX.spec --noconfirm
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. See output above for details.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Build complete!  Output: dist\ChainEX\
echo ============================================
pause
exit /b 0
