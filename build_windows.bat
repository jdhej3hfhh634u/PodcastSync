@echo off
REM ─────────────────────────────────────────────────────────────
REM  build_windows.bat — builds PodcastSync.exe for Windows
REM  Run this from the podcastsync\ folder
REM ─────────────────────────────────────────────────────────────

echo.
echo ══════════════════════════════════════════
echo   PodcastSync — Windows Build Script
echo ══════════════════════════════════════════
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Download from https://python.org - make sure to tick "Add to PATH"
    pause
    exit /b 1
)
echo Found Python:
python --version

REM Install build dependencies
echo.
echo Installing dependencies...
python -m pip install pyinstaller mutagen certifi --quiet
if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)
echo Dependencies installed.

REM Clean previous build
echo.
echo Cleaning previous build...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

REM Build
echo.
echo Building PodcastSync.exe - this takes ~60 seconds...
python -m PyInstaller PodcastSync.spec --noconfirm

if exist "dist\PodcastSync.exe" (
    echo.
    echo ══════════════════════════════════════════
    echo   Build complete!
    echo.
    echo   dist\PodcastSync.exe is ready.
    echo   Double-click it to run — no install needed.
    echo ══════════════════════════════════════════
    echo.
    explorer dist
) else (
    echo.
    echo ERROR: Build failed - dist\PodcastSync.exe not found.
    echo Check the output above for errors.
    pause
    exit /b 1
)

pause
