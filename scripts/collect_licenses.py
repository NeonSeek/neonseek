"""license and readme files of the package (phase 4, D-215).

writes into OUT (the folder next to NeonSeek.exe):
  LICENSE.txt                neonseek's own license (gpl-3.0-or-later)
  THIRD_PARTY_LICENSES.txt   the list from the repository plus the full text of every license:
                             sockseek, the font, qt and pyside6 (lgpl-3.0), every python package
                             that ships (from its .dist-info), python itself and the libraries
                             built into it, the pyinstaller bootloader
  README.txt                 what is where, and where the source code of this version is

runs in the build environment, which has exactly the packages that ship plus build tools.
usage: uv run python scripts/collect_licenses.py --out <folder> [--sockseek vendor/sockseek]
"""

from __future__ import annotations

import argparse
import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from neonseek import project  # noqa: E402

RULE = "=" * 78

# qt for python sources of the exact version, and qt itself (lgpl-3.0 section 4 and 6)
QT_SOURCES = """\
PySide6 and shiboken6 source: https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v{version}
Qt {version} source:          https://download.qt.io/official_releases/qt/{minor}/{version}/
Libraries Qt itself contains: https://doc.qt.io/qt-6/licenses-used-in-qt.html"""


def runtime_packages(root: str = "neonseek") -> list[metadata.Distribution]:
    """every installed distribution neonseek needs at run time, without the project itself."""
    seen: dict[str, metadata.Distribution] = {}
    todo = [root]
    while todo:
        name = todo.pop()
        dist = metadata.distribution(name)
        key = dist.metadata["Name"].lower().replace("_", "-").replace(".", "-")
        if key in seen:
            continue
        seen[key] = dist
        for line in dist.requires or []:
            req = Requirement(line)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                todo.append(req.name)
    seen.pop(root, None)
    return sorted(seen.values(), key=lambda d: d.metadata["Name"].lower())


def license_files(dist: metadata.Distribution) -> list[tuple[str, str]]:
    """(file name, text) of the license files inside a .dist-info folder."""
    found = []
    for file in dist.files or []:
        parts = Path(str(file)).parts
        if not parts or not parts[0].endswith(".dist-info"):
            continue
        name = parts[-1].upper()
        if any(word in name for word in ("LICEN", "COPYING", "NOTICE", "AUTHORS")):
            text = dist.read_text(str(Path(*parts[1:]))) or ""
            if text.strip():
                found.append(("/".join(parts[1:]), text))
    return found


def section(title: str, body: str) -> str:
    return f"\n\n{RULE}\n{title}\n{RULE}\n\n{body.strip()}\n"


def third_party_text(sockseek_dir: Path) -> str:
    out = [(ROOT / "THIRD_PARTY_LICENSES").read_text(encoding="utf-8").rstrip()]
    out.append(f"\n\n\nFULL LICENSE TEXTS\n{RULE}")
    out.append(
        section(
            "Sockseek (AGPL-3.0), sockseek.exe in _internal\\vendor\\sockseek",
            (sockseek_dir / "SOURCE.txt").read_text(encoding="utf-8-sig")
            + "\n\n"
            + (sockseek_dir / "LICENSE").read_text(encoding="utf-8"),
        )
    )
    fonts = ROOT / "src" / "neonseek" / "ui" / "theme" / "fonts"
    out.append(
        section(
            "JetBrains Mono (OFL-1.1)",
            (fonts / "SOURCE.txt").read_text(encoding="utf-8")
            + "\n\n"
            + (fonts / "OFL.txt").read_text(encoding="utf-8"),
        )
    )
    pyside = metadata.version("PySide6-Essentials")
    major_minor = ".".join(pyside.split(".")[:2])
    out.append(
        section(
            f"Qt {pyside}, PySide6-Essentials {pyside}, shiboken6 {pyside} (LGPL-3.0-only)",
            "Used under the GNU Lesser General Public License, version 3. The Qt libraries are "
            "the Qt6*.dll files and the plugins in _internal\\PySide6. They are separate files "
            "that you can replace with your own build of the same version. The LGPL-3.0 adds "
            "permissions to the GNU General Public License, version 3, whose full text is in "
            "LICENSE.txt.\n\n"
            + QT_SOURCES.format(version=pyside, minor=major_minor)
            + "\n\n"
            + (ROOT / "licenses" / "LGPL-3.0.txt").read_text(encoding="utf-8"),
        )
    )
    for dist in runtime_packages():
        name, version = dist.metadata["Name"], dist.version
        if name.lower().startswith(("pyside6", "shiboken6")):
            continue  # the lgpl section above, their wheels carry only a commercial notice
        texts = license_files(dist)
        if not texts:
            raise SystemExit(f"{name} {version}: no license file in its .dist-info")
        body = "\n\n".join(f"--- {file} ---\n\n{text.strip()}" for file, text in texts)
        expression = dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
        title = f"{name} {version}" + (f" ({expression})" if 0 < len(expression) < 60 else "")
        out.append(section(title, body))
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    out.append(
        section(
            f"Python {sys.version.split()[0]} (PSF-2.0) and the libraries built into it",
            python_license.read_text(encoding="utf-8")
            + "\n\n"
            + (ROOT / "licenses" / "python-incorporated-software.txt").read_text(encoding="utf-8"),
        )
    )
    pyinstaller = metadata.distribution("pyinstaller")
    out.append(
        section(
            f"PyInstaller {pyinstaller.version} bootloader (GPL-2.0-or-later with the "
            "bootloader exception)",
            "NeonSeek.exe and neonseek-cli.exe start through the PyInstaller bootloader.\n\n"
            + "\n\n".join(text for _, text in license_files(pyinstaller)),
        )
    )
    out.append(
        section(
            "Microsoft Visual C++ runtime",
            "vcruntime140.dll, vcruntime140_1.dll, msvcp140*.dll, ucrtbase.dll and the "
            "api-ms-win-*.dll files in _internal are Microsoft's redistributable runtime, "
            "shipped as part of the Python runtime and of Qt (see the "
            '"Additional Conditions for this Windows binary build" above). They are system '
            "libraries of Windows in the sense of section 1 of the GPL-3.0.",
        )
    )
    return "\n".join(out) + "\n"


def readme_text(version: str) -> str:
    url = project.source_url(version)
    return f"""\
NeonSeek {version}
{"=" * (9 + len(version))}

NeonSeek downloads your track lists from Soulseek in lossless quality for
rekordbox. Start it with NeonSeek.exe. neonseek-cli.exe is the same program
on the command line (neonseek-cli --help).

The manual: the HELP button in NeonSeek, or the PDF files in the folder
manual\\pdf (NeonSeek-manual-en.pdf, NeonSeek-manual-pl.pdf).

NeonSeek is free software under the GNU General Public License, version 3
or later (GPL-3.0-or-later), and comes without any warranty. The full text
is in LICENSE.txt.

Source code of this version: {url}

The licenses and the full license texts of all parts (Sockseek, Qt and
PySide6, Python and its packages, the font) are in THIRD_PARTY_LICENSES.txt.


NeonSeek {version} (po polsku)
{"-" * (21 + len(version))}

NeonSeek pobiera Twoje listy utworów z Soulseeka w jakości bezstratnej dla
rekordboxa. Uruchamiasz go plikiem NeonSeek.exe. neonseek-cli.exe to ten
sam program w wierszu poleceń (neonseek-cli --help).

Instrukcja: przycisk POMOC w NeonSeek albo pliki PDF w folderze
manual\\pdf (NeonSeek-manual-pl.pdf, NeonSeek-manual-en.pdf).

NeonSeek jest wolnym oprogramowaniem na licencji GNU General Public
License, wersja 3 lub późniejsza (GPL-3.0-or-later), i nie ma żadnej
gwarancji. Pełny tekst jest w pliku LICENSE.txt.

Kod źródłowy tej wersji: {url}

Licencje i ich pełne teksty dla wszystkich części (Sockseek, Qt i PySide6,
Python i jego pakiety, czcionka) są w pliku THIRD_PARTY_LICENSES.txt.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sockseek", type=Path, default=ROOT / "vendor" / "sockseek")
    args = parser.parse_args()
    version = metadata.version("neonseek")
    args.out.mkdir(parents=True, exist_ok=True)
    files = {
        "LICENSE.txt": (ROOT / "LICENSE").read_text(encoding="utf-8"),
        "THIRD_PARTY_LICENSES.txt": third_party_text(args.sockseek),
        "README.txt": readme_text(version),
    }
    for name, text in files.items():
        # windows line endings, so notepad of any age shows the lines
        (args.out / name).write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode())
        print(f"{args.out / name} ({len(text) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
