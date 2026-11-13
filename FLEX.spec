# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files


root = Path(SPECPATH)
browser_root = Path(
    os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH",
        str(root / "build" / "playwright-browsers"),
    )
).resolve()
if not browser_root.is_dir() or not any(browser_root.iterdir()):
    raise SystemExit(
        "Bundled Playwright Chromium is missing. Run "
        "'python -m playwright install chromium' with "
        "PLAYWRIGHT_BROWSERS_PATH set to build/playwright-browsers."
    )

browser_datas = []
for browser_file in browser_root.rglob("*"):
    relative_path = browser_file.relative_to(browser_root)
    if (
        not browser_file.is_file()
        or ".links" in relative_path.parts
        or any(part.startswith("chromium_headless_shell-") for part in relative_path.parts)
    ):
        continue
    destination = Path("playwright-browsers") / relative_path.parent
    browser_datas.append((str(browser_file), destination.as_posix()))

datas = [
    (str(root / "flex" / "ui" / "assets"), "flex/ui/assets"),
    *browser_datas,
    *collect_data_files("tzdata"),
]
binaries = []
hiddenimports = ["tzdata"]
for package in (
    "av",
    "comtypes",
    "ctranslate2",
    "faster_whisper",
    "playwright",
    "pycaw",
    "screen_brightness_control",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    if package == "comtypes":
        package_datas = [
            (source, destination)
            for source, destination in package_datas
            if "/comtypes/test/" not in Path(source).as_posix().casefold()
        ]
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hiddenimports)

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(root / "pyinstaller_hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "_pytest", "comtypes.test"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FLEX",
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
    icon=str(root / "flex" / "ui" / "assets" / "flex_icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="FLEX",
)
