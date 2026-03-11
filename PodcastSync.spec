# -*- mode: python ; coding: utf-8 -*-
import sys
import os

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
        strip=False,
        upx=False,
        console=False,
        target_arch=target_arch,
        icon=None,
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='PodcastSync')
    app = BUNDLE(
        coll,
        name='PodcastSync.app',
        icon=None,
        bundle_identifier='com.podcastsync.app',
        info_plist={
            'NSHighResolutionCapable': True,
            'CFBundleShortVersionString': '1.0.0',
            'CFBundleName': 'PodcastSync',
        },
    )
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas,
        name='PodcastSync',
        debug=False,
        strip=False,
        upx=True,
        console=False,
        target_arch=None,
        icon=None,
    )
