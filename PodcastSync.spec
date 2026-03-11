# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for PodcastSync
# Usage: pyinstaller PodcastSync.spec

import sys

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
)

pyz = PYZ(a.pure)

if sys.platform == 'darwin':
    # macOS: build a .app bundle
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name='PodcastSync',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,   # no terminal window
        icon=None,
    )
    coll = COLLECT(
        exe, a.binaries, a.datas,
        strip=False,
        upx=True,
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
            'CFBundleShortVersionString': '1.0.0',
            'CFBundleName': 'PodcastSync',
            'LSUIElement': False,
        },
    )
else:
    # Windows: single .exe file
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas,
        name='PodcastSync',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,   # no terminal window
        disable_windowed_traceback=False,
        target_arch=None,
        icon=None,
    )
