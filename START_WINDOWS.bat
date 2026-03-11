@echo off
title PodcastSync
echo.
echo  ============================================
echo    PodcastSync - Rockbox iPod Podcast Syncer
echo  ============================================
echo.

:: Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python is not installed!
    echo.
    echo  Please install Python from https://python.org
    echo  Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

echo  Python found. Checking dependencies...
python -c "import certifi" >nul 2>&1
if errorlevel 1 (
    echo  Installing certifi (fixes SSL errors)...
    python -m pip install certifi --quiet
)
python -c "import mutagen" >nul 2>&1
if errorlevel 1 (
    echo  Installing mutagen (for cover art + ID3 tags)...
    python -m pip install mutagen --quiet
)

echo  Starting PodcastSync...
echo  Your browser will open automatically.
echo  If it doesn't, open: http://localhost:5000
echo.
echo  To stop PodcastSync, close this window or press Ctrl+C
echo.

python "%~dp0app.py"
pause
