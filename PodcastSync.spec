# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for PodcastSync
# Usage: pyinstaller PodcastSync.spec

import sys
import os

# TARGET_ARCH env var controls cross-compilation on macOS
# Set to 'arm64' or 'x86_64' in the GitHub Actions workflow
target_arch = os.environ.get('TARGET_ARCH', None)

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['mutagen', 'mutagen.id3', 'certifi'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'email.mime'],
    noarchive=False,
    target_arch=target_arch,
)

pyz = PYZ(a.pure)

if sys.platform == 'darwin':
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name='PodcastSync',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        target_arch=target_arch,
        icon=None,
    )
    coll = COLLECT(
        exe, a.binaries, a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name='PodcastSync',
    )
    app = BUNDLE(
        coll,
        name='PodcastSync.app',
        icon=None,
        bundle_identifier='com.podcastsync.app',
        info_plist={
            'NSHighResolutionCapable': True,
            'CFBundleShortVersionString': '1.0.7',
            'CFBundleName': 'PodcastSync',
            'LSUIElement': False,
        },
    )
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas,
        name='PodcastSync',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        target_arch=None,
        icon=None,
    )
