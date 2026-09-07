# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Vayren desktop app.

Build:  .venv/Scripts/pyinstaller scripts/assets/vayren.spec --noconfirm
Output: dist/Vayren/Vayren.exe
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]

# Rust native kernels (constitution §8): the execution/backtest/market
# bridges load `<root>/rust/target/release/vayren_core.*` at import. Bundle
# the built library into the same relative location so frozen paths resolve.
# Fail LOUDLY when absent — a silent EXE without the kernels is forbidden.
_native_candidates = [
    ROOT / "rust" / "target" / "release" / name
    for name in ("vayren_core.dll", "libvayren_core.so", "libvayren_core.dylib")
]
_native_lib = next((p for p in _native_candidates if p.is_file()), None)
if _native_lib is None:
    raise SystemExit(
        "PyInstaller: Rust native library missing — run `python scripts/build_rust.py` first."
    )

a = Analysis(
    [str(ROOT / "00_app" / "app" / "__main__.py")],
    pathex=[
        str(ROOT / "00_app"),
        str(ROOT / "01_core"),
        str(ROOT / "02_data"),
        str(ROOT / "03_market"),
        str(ROOT / "04_chart"),
        str(ROOT / "05_strategy"),
        str(ROOT / "06_backtest"),
    ],
    binaries=[(str(_native_lib), "rust/target/release")],
    # Chart SVG assets (indicator toolbar icons) — panel resolves them as
    # chart/assets/indicator_bar relative to its own module location.
    datas=[
        (str(ROOT / "04_chart" / "chart" / "assets"), "chart/assets"),
    ],
    hiddenimports=[
        "app",
        "core",
        "data",
        "market",
        "chart",
        "strategy",
        "strategy.strategies",
        "strategy.strategies.base",
        "strategy.strategies.indicators",
        "strategy.strategies.obr",
        "strategy.strategies.sma",
        "backtest",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtCore",
        "PySide6.QtSvg",
        # Strategy code is exec()'d at runtime (compile_strategy) — these
        # dynamic imports are invisible to PyInstaller's static analysis:
        "zoneinfo",
        "tzdata",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "pyright",
        "pre_commit",
        "tkinter",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3D",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtQuick",
        "PySide6.QtMultimedia",
        "PySide6.QtPdf",
    ],
    noarchive=False,
    module_collection_mode=None,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Vayren",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "scripts" / "assets" / "vayren.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Vayren",
)
