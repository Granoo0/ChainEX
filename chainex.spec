# chainex.spec — PyInstaller one-dir build spec for ChainEX
#
# Usage:
#   pip install pyinstaller
#   pyinstaller chainex.spec
#
# Output: dist/ChainEX/ChainEX.exe   (plus all required DLLs in the same folder)
#
# Notes:
#   • The 'templates/' directory must exist before building.
#     Any .png/.bmp files in it are bundled automatically.
#   • 'config.json' is intentionally NOT bundled — it is user-specific and
#     generated at first run.  Drop your config.json next to ChainEX.exe.
#   • Set `icon` below to 'assets/icon.ico' once you have an icon file.

import os
import sys

block_cipher = None

# ── Resolve project root (same directory as this .spec file) ──────────────────
_HERE = os.path.dirname(os.path.abspath(SPEC))

# ── Collect data files ────────────────────────────────────────────────────────
_datas = []

# Templates directory — required at build time.  Warn clearly if missing.
_templates_src = os.path.join(_HERE, "templates")
if os.path.isdir(_templates_src):
    _datas.append((_templates_src, "templates"))
else:
    print(
        "\n[chainex.spec] WARNING: 'templates/' directory not found at:\n"
        f"  {_templates_src}\n"
        "The built app will load no templates.  Create the directory\n"
        "and add your .png/.bmp template images before distributing.\n",
        file=sys.stderr,
    )


# ── Analysis ───────────────────────────────────────────────────────────────────
a = Analysis(
    [os.path.join(_HERE, "launcher.py")],
    pathex=[_HERE],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        # ── pywin32 ──────────────────────────────────────────────────────
        "win32api",
        "win32con",
        "win32gui",
        "win32process",
        "win32ui",
        "win32clipboard",
        "pywintypes",
        # ── pynput ───────────────────────────────────────────────────────
        "pynput.keyboard",
        "pynput.mouse",
        "pynput._util.win32",
        "pynput._util.win32_vks",
        # ── Pillow ───────────────────────────────────────────────────────
        "PIL._imaging",
        "PIL.Image",
        "PIL.ImageGrab",
        "PIL.ImageTk",
        # ── OpenCV ───────────────────────────────────────────────────────
        "cv2",
        # ── misc ─────────────────────────────────────────────────────────
        "pkg_resources.py2_warn",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # EasyOCR is optional (Phase 4+) — don't pull in PyTorch at build time
        "torch",
        "torchvision",
        "torchaudio",
        "easyocr",
        # Test dependencies
        "pytest",
        "unittest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── Executable ────────────────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ChainEX",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,                   # no terminal window in production
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                       # set to 'assets/icon.ico' when ready
    version=None,                    # set to a version_info.txt for EXE metadata
)

# ── One-dir bundle ────────────────────────────────────────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ChainEX",
)
