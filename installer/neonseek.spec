# pyinstaller spec, onedir (PLAN.md phase 4, D-215). run by scripts/build.ps1:
#   pyinstaller --noconfirm --distpath <dist> --workpath <work> installer/neonseek.spec
# NeonSeek.exe is the window without a console, neonseek-cli.exe the same program on the
# command line. both share one _internal folder. the manual, the licenses and the readme
# are copied next to the exe by build.ps1, sockseek goes to _internal\vendor\sockseek.
# ruff: noqa

import os
from importlib import metadata
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src" / "neonseek"
SOCKSEEK = Path(os.environ.get("NEONSEEK_SOCKSEEK_DIR", ROOT / "vendor" / "sockseek"))
ICON = str(ROOT / "installer" / "neonseek.ico")
VERSION_FILE = os.environ.get("NEONSEEK_VERSION_FILE")  # written by build.ps1

# qt parts the app does not use: smaller package, fewer files to scan
QT_EXCLUDES = [
    f"PySide6.{name}"
    for name in (
        "QtNetwork", "QtQml", "QtQuick", "QtQuickWidgets", "QtQuickControls2", "QtOpenGL",
        "QtOpenGLWidgets", "QtPdf", "QtPdfWidgets", "QtDBus", "QtDesigner", "QtHelp",
        "QtMultimedia", "QtWebEngineCore", "QtUiTools", "QtXml", "QtTest", "QtSql",
        "QtConcurrent", "QtPrintSupport", "QtSvgWidgets", "QtStateMachine", "QtLottie",
    )
]
EXCLUDES = [
    "tkinter", "_tkinter", "unittest", "pydoc", "pytest", "_pytest", "pytestqt", "pygments",
    "packaging", "setuptools", "pkg_resources",
    "distutils", *QT_EXCLUDES,
]


def neonseek_metadata():
    # METADATA, so importlib.metadata knows the version
    dist = metadata.distribution("neonseek")
    meta = Path(dist.locate_file(f"neonseek-{dist.version}.dist-info/METADATA"))
    return [(str(meta), f"neonseek-{dist.version}.dist-info")]


datas = collect_data_files("neonseek", excludes=["**/__pycache__/**"])
datas += neonseek_metadata()
# about lists the version and license of these from their metadata
for name in ("PySide6-Essentials", "shiboken6", "httpx", "keyring", "mutagen", "platformdirs",
             "yt-dlp"):
    datas += copy_metadata(name)
datas += [
    (str(SOCKSEEK / "sockseek.exe"), "vendor/sockseek"),
    (str(SOCKSEEK / "LICENSE"), "vendor/sockseek"),
    (str(SOCKSEEK / "SOURCE.txt"), "vendor/sockseek"),
]


def shipped(entry):
    # pyinstaller also collects the rest of neonseek's .dist-info by itself. direct_url.json
    # there holds the folder neonseek was installed from, so only METADATA ships (D-219)
    dest = entry[0].replace("\\", "/")
    return not (dest.startswith("neonseek-") and ".dist-info/" in dest
                and not dest.endswith("/METADATA"))


def analysis(script):
    a = Analysis(
        [str(script)],
        pathex=[str(ROOT / "src")],
        datas=datas,
        hiddenimports=["keyring.backends.Windows"],
        excludes=EXCLUDES,
        noarchive=False,
        optimize=0,
    )
    a.datas = [entry for entry in a.datas if shipped(entry)]
    return a


gui = analysis(SRC / "ui" / "__main__.py")
cli = analysis(SRC / "__main__.py")


def exe(a, name, console):
    return EXE(
        PYZ(a.pure),
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        strip=False,
        upx=False,
        console=console,
        icon=ICON,
        version=VERSION_FILE,
    )


COLLECT(
    exe(gui, "NeonSeek", console=False),
    gui.binaries,
    gui.datas,
    exe(cli, "neonseek-cli", console=True),
    cli.binaries,
    cli.datas,
    strip=False,
    upx=False,
    name="NeonSeek",
)
