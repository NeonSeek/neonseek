"""the windows version resource of NeonSeek.exe (the details tab of its properties).

the version comes from pyproject.toml through the installed metadata, the one place for it.
usage: uv run python scripts/exe_version.py --out <file>   (prints the version)
"""

from __future__ import annotations

import argparse
import re
from importlib import metadata
from pathlib import Path

TEMPLATE = """\
VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'NeonSeek'),
      StringStruct('FileDescription', 'NeonSeek'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'NeonSeek'),
      StringStruct('LegalCopyright', 'GPL-3.0-or-later'),
      StringStruct('OriginalFilename', 'NeonSeek.exe'),
      StringStruct('ProductName', 'NeonSeek'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def numbers(version: str) -> tuple[int, int, int, int]:
    # the leading digits of each part: 1.2.3rc1 is 1.2.3.0
    parts = [int(m.group()) if (m := re.match(r"\d+", p)) else 0 for p in version.split(".")[:4]]
    return tuple((parts + [0, 0, 0, 0])[:4])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    version = metadata.version("neonseek")
    args.out.write_text(TEMPLATE.format(nums=numbers(version), version=version), encoding="utf-8")
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
