"""builds the pdf of the user manual, one per language, from docs/manual/<language>.md (D-164).

uv run python scripts/build_manual.py [--out docs/manual/pdf] [--lang pl]

qt reads the markdown and writes the pdf, so nothing new is needed. no window opens.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from neonseek import i18n, manual  # noqa: E402
from neonseek.ui import manual_view  # noqa: E402


def build(out_dir: Path, languages: list[str]) -> list[Path]:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    _ = app
    built = []
    for language in languages:
        source = manual.manual_dir() / f"{language}.md"
        if not source.exists():
            raise SystemExit(f"no manual for {language}: {source}")
        built.append(manual_view.build_pdf(source, out_dir / manual.pdf_name(language), language))
    return built


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=manual.manual_dir() / "pdf")
    parser.add_argument("--lang", action="append", choices=list(i18n.LANGUAGES))
    args = parser.parse_args()
    for path in build(args.out, args.lang or list(i18n.LANGUAGES)):
        print(f"{path} ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
