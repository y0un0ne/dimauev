from pathlib import Path

from PyInstaller.utils.hooks import collect_all


root = Path(SPECPATH).parent
datas = [
    (str(root / "packaging" / ".frontend-build" / "dist"), "Frontend/client/dist"),
    (str(root / "packaging" / ".cache" / "ms-playwright"), "browsers"),
    (str(root / "docs"), "docs"),
]
binaries = []
hiddenimports = [
    "dimensions_pipeline",
    "dimensions_extractor_mvp1_5_2",
    "dimensions_ai_evaluator_mvp2_6",
    "dimensions_excel_generator_mvp3",
    "dimensions_extraction_status",
    "dimensions_evaluation_status",
    "Frontend.server.app",
]

for package in (
    "playwright",
    "google.genai",
    "openai",
    "openpyxl",
    "PIL",
    "uvicorn",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    [str(root / "production_entry.py")],
    pathex=[str(root), str(root / "Frontend" / "server")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DimensionsAutoEvaluate",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(root / "packaging" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DimensionsAutoEvaluate",
)
