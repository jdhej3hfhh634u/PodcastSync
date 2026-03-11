#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  build_mac.sh — builds PodcastSync.app for macOS
#  Run this from the podcastsync/ folder:  bash build_mac.sh
# ─────────────────────────────────────────────────────────────
set -e

echo ""
echo "══════════════════════════════════════════"
echo "  PodcastSync — macOS Build Script"
echo "══════════════════════════════════════════"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install from https://python.org"
    exit 1
fi
PY=python3
echo "✓ Python: $($PY --version)"

# Install build dependencies
echo ""
echo "Installing dependencies…"
$PY -m pip install pyinstaller mutagen certifi --quiet --break-system-packages 2>/dev/null \
    || $PY -m pip install pyinstaller mutagen certifi --quiet

echo "✓ Dependencies installed"

# Clean previous build
echo ""
echo "Cleaning previous build…"
rm -rf build/ dist/

# Build
echo ""
echo "Building PodcastSync.app — this takes ~30 seconds…"
$PY -m PyInstaller PodcastSync.spec --noconfirm

if [ -d "dist/PodcastSync.app" ]; then
    echo ""
    echo "══════════════════════════════════════════"
    echo "  ✓ Build complete!"
    echo ""
    echo "  dist/PodcastSync.app is ready."
    echo "  Drag it to your Applications folder."
    echo "══════════════════════════════════════════"
    echo ""
    # Open the dist folder in Finder
    open dist/
else
    echo ""
    echo "ERROR: Build failed — dist/PodcastSync.app not found."
    echo "Check the output above for errors."
    exit 1
fi
