# -*- mode: python ; coding: utf-8 -*-
"""Spécification PyInstaller pour empaqueter Ruche en application autonome.

    pyinstaller ruche.spec

Le résultat se trouve dans dist/Ruche/.
"""

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# Ces paquets embarquent des bibliothèques natives (FFmpeg, PortAudio, DTLS…)
# qu'il faut inclure explicitement.
for _package in (
    "aiortc",
    "av",
    "aioice",
    "pylibsrtp",
    "cryptography",
    "zeroconf",
    "sounddevice",
    "mss",
    "pygrabber",
    "comtypes",
    "numpy",
):
    try:
        _d, _b, _h = collect_all(_package)
        datas += _d
        binaries += _b
        hiddenimports += _h
    except Exception:  # paquet absent (ex. pygrabber hors Windows)
        pass

hiddenimports += ["app", "app.main", "app.rendezvous.server"]

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Ruche",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Ruche",
)
