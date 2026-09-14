# PyInstaller spec for a single file iosc.exe
# Build with: .venv\Scripts\pyinstaller.exe packaging\iosc.spec --noconfirm

from pathlib import Path

ROOT = Path(SPECPATH).parent

# The lzfse tools and the ADI native bridge are looked up beside the package,
# so they ship inside the bundle at the same relative place iosc.config.paths expects
VENDOR = [
    (str(ROOT / "vendor" / "lzfse" / name), "iosc/vendor/lzfse")
    for name in ("lzfse.exe", "lzvn_raw.exe", "LICENSE")
] + [
    (str(ROOT / "vendor" / "adi" / "adi_native.dll"), "iosc/vendor/adi"),
] + [
    (str(p), "iosc/macros/bin")
    for p in (ROOT / "macros" / "bin").glob("*")
]

analysis = Analysis(
    [str(ROOT / "src" / "iosc" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=VENDOR,
    hiddenimports=["iosc.cli.main"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc_data", "pytest"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="iosc",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
