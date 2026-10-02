# -*- mode: python ; coding: utf-8 -*-
"""Сборка приложения через PyInstaller.

Запускать на той системе, под которую собираешь: кросс-компиляции у PyInstaller
нет, exe получается только на Windows.

    pyinstaller --noconfirm --clean lead-finder.spec
"""

import sys

block_cipher = None

# интерфейс и образцы настроек должны попасть внутрь сборки
datas = [
    ("ui", "ui"),
    ("leadgen/data", "leadgen/data"),
    ("config.example.yaml", "."),
    ("messages.example.yaml", "."),
]

hiddenimports = [
    "leadgen.sources.osm",
    "leadgen.sources.google_places",
    "leadgen.sources.directory",
    "leadgen.sources.social",
    "leadgen.stages.filter",
    "leadgen.stages.write",
]

if sys.platform == "win32":
    # оконный слой pywebview на Windows
    hiddenimports += ["clr", "webview.platforms.edgechromium", "webview.platforms.winforms"]
elif sys.platform == "darwin":
    hiddenimports += ["webview.platforms.cocoa"]
else:
    hiddenimports += ["webview.platforms.gtk", "webview.platforms.qt"]

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PIL", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ПоискКлиентов",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # окно консоли не нужно
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ПоискКлиентов",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Поиск клиентов.app",
        icon=None,
        bundle_identifier="local.leadfinder.app",
        info_plist={
            "CFBundleName": "Поиск клиентов",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
