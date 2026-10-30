# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification.

Build from the repository root:
    pyinstaller installer/AzureDevOpsWikiIndexer.spec --noconfirm --clean

Produces dist/AzureDevOpsWikiIndexer/ containing:
    AzureDevOpsWikiIndexer.exe     – the desktop application (windowed)
    AzureDevOpsWikiIndexerCli.exe  – the headless command line tool (console)
plus the bundled runtime, default config and icon. No Python installation is required.
"""

import re
import sys
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
ICON = ROOT / "assets" / "app.ico"

version = re.search(r'__version__\s*=\s*"([^"]+)"', (SRC / "wiki_indexer" / "__init__.py").read_text()).group(1)
numeric = tuple(int(part) for part in re.findall(r"\d+", version)[:3]) + (0,)
numeric = (numeric + (0, 0, 0, 0))[:4]


def version_info(description: str, internal_name: str) -> VSVersionInfo:
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numeric, prodvers=numeric),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "ivesma"),
                            StringStruct("FileDescription", description),
                            StringStruct("FileVersion", version),
                            StringStruct("InternalName", internal_name),
                            StringStruct("LegalCopyright", "MIT License"),
                            StringStruct("OriginalFilename", f"{internal_name}.exe"),
                            StringStruct("ProductName", "Azure DevOps Wiki Indexer"),
                            StringStruct("ProductVersion", version),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )


datas = [
    (str(ROOT / "config" / "appsettings.json"), "config"),
    (str(ROOT / "config" / "keywords.json"), "config"),
    (str(ICON), "assets"),
]
hiddenimports = [
    "keyring.backends.Windows",
    "win32ctypes.core",
    "nltk.stem.snowball",
]
excludes = [
    "tkinter", "matplotlib", "scipy", "sklearn", "pandas", "IPython", "pytest", "PIL",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
]


def analysis(script: Path) -> Analysis:
    return Analysis(
        [str(script)],
        pathex=[str(SRC)],
        datas=datas,
        hiddenimports=hiddenimports,
        excludes=excludes,
        noarchive=False,
    )


gui = analysis(SRC / "wiki_indexer" / "__main__.py")
cli = analysis(SRC / "wiki_indexer" / "cli.py")

gui_exe = EXE(
    PYZ(gui.pure),
    gui.scripts,
    [],
    exclude_binaries=True,
    name="AzureDevOpsWikiIndexer",
    icon=str(ICON),
    version=version_info("Azure DevOps Wiki Indexer", "AzureDevOpsWikiIndexer"),
    console=False,
    upx=False,
)
cli_exe = EXE(
    PYZ(cli.pure),
    cli.scripts,
    [],
    exclude_binaries=True,
    name="AzureDevOpsWikiIndexerCli",
    icon=str(ICON),
    version=version_info("Azure DevOps Wiki Indexer (command line)", "AzureDevOpsWikiIndexerCli"),
    console=True,
    upx=False,
)

coll = COLLECT(
    gui_exe,
    gui.binaries,
    gui.datas,
    cli_exe,
    cli.binaries,
    cli.datas,
    upx=False,
    name="AzureDevOpsWikiIndexer",
)
