#!/bin/bash
# PodcastSync launcher for macOS and Linux

echo ""
echo " ============================================"
echo "   PodcastSync - Rockbox iPod Podcast Syncer"
echo " ============================================"
echo ""

# Find python3
if command -v python3 &>/dev/null; then
    PY=python3
elif command -v python &>/dev/null; then
    PY=python
else
    echo " ERROR: Python is not installed!"
    echo ""
    echo " macOS:  brew install python   (or download from python.org)"
    echo " Linux:  sudo apt install python3   (Ubuntu/Debian)"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi

echo " Python found: $($PY --version)"
echo ""
echo " Checking dependencies..."
$PY -c "import certifi" 2>/dev/null || {
    echo " Installing certifi (fixes SSL on macOS)..."
    $PY -m pip install certifi --quiet 2>/dev/null || $PY -m pip install certifi --quiet --break-system-packages 2>/dev/null
}
$PY -c "import mutagen" 2>/dev/null || {
    echo " Installing mutagen (for cover art + ID3 tags)..."
    $PY -m pip install mutagen --quiet 2>/dev/null || $PY -m pip install mutagen --quiet --break-system-packages 2>/dev/null
}
$PY -c "import webview" 2>/dev/null || {
    echo " Installing pywebview (for native app window)..."
    $PY -m pip install pywebview --quiet 2>/dev/null || $PY -m pip install pywebview --quiet --break-system-packages 2>/dev/null
}

echo " Starting PodcastSync..."
echo " Your browser will open automatically."
echo " If it doesn't, open: http://localhost:5000"
echo ""
echo " To stop PodcastSync, press Ctrl+C"
echo ""

cd "$(dirname "$0")"
$PY app.py
