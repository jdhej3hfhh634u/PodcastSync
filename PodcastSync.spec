# -*- mode: python ; coding: utf-8 -*-
import sys
import os

target_arch = os.environ.get('TARGET_ARCH', None)

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('icon_16.png',  '.'),
        ('icon_32.png',  '.'),
        ('icon_64.png',  '.'),
        ('icon_128.png', '.'),
        ('icon_256.png', '.'),
        ('icon_512.png', '.'),
        ('icon.ico',     '.'),
    ],
    hiddenimports=[
        'mutagen', 'mutagen.id3', 'certifi',
        'webview', 'webview.platforms.cocoa',
        'webview.platforms.winforms',
        'AppKit', 'Foundation',
    ],
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
        icon='icon_512.png',
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='PodcastSync')
    app = BUNDLE(
        coll,
        name='PodcastSync.app',
        icon='icon_512.png',
        bundle_identifier='com.podcastsync.app',
        info_plist={
            'NSHighResolutionCapable': True,
            'NSRequiresAquaSystemAppearance': False,
            'CFBundleShortVersionString': '1.0.8',
            'CFBundleName': 'PodcastSync',
            'CFBundleDisplayName': 'PodcastSync',
            'LSMinimumSystemVersion': '10.13.0',
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
        icon='icon.ico',
    )
