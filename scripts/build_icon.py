"""build the exe and installer icon (installer/neonseek.ico) from the logo svgs.

the sizes and which of them use the simplified logo (no grooves, thicker lines) come from
LOGO_SIZES and LOGO_FULL_FROM in neonseek/ui/theme/loader.py, the same split as the window
icon. every size is a png inside the ico, which windows reads since vista. the svgs are
rendered with qt, so no new dependency.
usage: uv run python scripts/build_icon.py [--out installer/neonseek.ico] [--preview x.jpg]
--preview also writes one jpg with every size from the written ico, scaled up without
smoothing, on a dark and on a light background.
"""

from __future__ import annotations

import argparse
import os
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QImage,
    QImageReader,
    QPainter,
)

from neonseek.ui.theme.loader import LOGO_SIZES, logo_image  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / "src" / "neonseek" / "ui" / "theme" / "fonts" / "JetBrainsMono-Medium.ttf"


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def ico_bytes() -> bytes:
    images = {size: png_bytes(logo_image(size)) for size in LOGO_SIZES}
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, payload = b"", b""
    for size, png in images.items():
        side = 0 if size >= 256 else size  # 0 means 256 in an ico entry
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset)
        payload += png
        offset += len(png)
    return header + entries + payload


def read_ico(path: Path) -> list[QImage]:
    reader = QImageReader(str(path), b"ico")
    images = []
    for index in range(reader.imageCount()):
        reader.jumpToImage(index)
        images.append(reader.read())
    if not images:
        raise SystemExit(f"{path}: qt could not read the ico")
    return sorted(images, key=lambda image: image.width())


def preview(images: list[QImage], out: Path) -> None:
    # each size scaled up by a whole factor to about 256 px, nearest neighbour
    cell, gap, label, real = 256, 24, 28, 72
    width = gap + len(images) * (cell + gap)
    height = 2 * (gap + label + cell + real + gap)
    sheet = QImage(width, height, QImage.Format.Format_RGB32)
    # the offscreen platform has no system fonts, the app font is in the package
    font_id = QFontDatabase.addApplicationFont(str(FONT))
    painter = QPainter(sheet)
    painter.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 14))
    for row, (background, ink) in enumerate((("#202020", "#E0E0E0"), ("#F3F3F3", "#202020"))):
        top = row * height // 2
        painter.fillRect(QRect(0, top, width, height // 2), QColor(background))
        painter.setPen(QColor(ink))
        for column, image in enumerate(images):
            size = image.width()
            factor = max(1, cell // size)
            scaled = image.scaled(size * factor, size * factor,
                                  Qt.AspectRatioMode.IgnoreAspectRatio,
                                  Qt.TransformationMode.FastTransformation)  # fmt: skip
            left = gap + column * (cell + gap)
            painter.drawText(QRect(left, top + gap, cell, label), Qt.AlignmentFlag.AlignCenter,
                             f"{size} px  x{factor}")  # fmt: skip
            painter.drawImage(left + (cell - scaled.width()) // 2, top + gap + label, scaled)
            # the real size under the scaled one, 256 px is already shown as it is
            if size < cell:
                real_top = top + gap + label + cell
                painter.drawImage(left + (cell - size) // 2, real_top + (real - size) // 2,
                                  image)  # fmt: skip
    painter.end()
    sheet.save(str(out), "JPG", 92)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "installer" / "neonseek.ico")
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
    _app = QGuiApplication([])
    args.out.write_bytes(ico_bytes())
    print(f"{args.out}: {', '.join(str(s) for s in LOGO_SIZES)} px")
    if args.preview:
        preview(read_ico(args.out), args.preview)
        print(f"preview: {args.preview}")


if __name__ == "__main__":
    main()
