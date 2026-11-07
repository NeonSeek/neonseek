"""unpack what the built exes carry inside, for checking what ships (phase 4, D-220).

PyInstaller packs the startup scripts and the compiled python code (.pyc) into an archive at
the end of each exe, compressed, so a text search of the exe does not see them. this writes
every entry of those archives as a plain file into OUT: the startup scripts, and every module
of the embedded PYZ as its uncompressed .pyc data. base_library.zip and the other files in
the program folder are already plain files.
usage: uv run --group build python scripts/unpack_build.py <program folder> <out folder>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader, NotAnArchiveError
from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader

PYZ = "z"


def unpack(exe: Path, out: Path) -> int:
    count = 0
    try:
        pkg = CArchiveReader(str(exe))
    except Exception:  # noqa: BLE001 - not a pyinstaller exe (sockseek.exe, qt tools)
        return 0
    target = out / exe.name
    for name, entry in pkg.toc.items():
        typecode = entry[-1]
        if typecode == PYZ:
            try:
                pyz: ZlibArchiveReader = pkg.open_embedded_archive(name)
            except NotAnArchiveError:
                continue
            for module in pyz.toc:
                data = pyz.extract(module, raw=True)
                if data is None:
                    continue  # a package folder entry without code
                path = target / name / (module.replace(".", "/") + ".pyc")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                count += 1
        else:
            data = pkg.extract(name)
            if data is None:
                continue
            path = target / "pkg" / name.replace(":", "_")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("program", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    for exe in sorted(args.program.rglob("*.exe")):
        count = unpack(exe, args.out)
        if count:
            print(f"{exe.relative_to(args.program)}: {count} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
