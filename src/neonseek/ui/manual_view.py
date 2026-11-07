"""the manual as a qt text document: one chapter in the help window, the whole manual in the
pdf (D-164, D-182, D-183). the html comes from neonseek.manual_html, qt only lays it out."""

from __future__ import annotations

import html
import os
import re
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt, QUrl
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QFont,
    QFontDatabase,
    QImageReader,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
    QTextDocument,
)

from neonseek import i18n, manual, manual_html
from neonseek.i18n import N_
from neonseek.ui.theme.loader import FONT_FAMILY, load_fonts

# a proportional font for reading. qt without a screen does not see windows fonts, so the files
# are loaded by hand, and the bundled monospace font stands in when they are missing
READING_FONT = "Segoe UI"
READING_FONT_FILES = ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf", "seguisb.ttf")
PDF_POINT_SIZE = 10.5
PDF_DPI = 300
# millimeters: left, top, right, bottom of the page, and the room for the page number
PDF_MARGINS = (24, 20, 24, 16)
PDF_FOOTER_MM = 8
# qt sizes images and html pixel attributes in pixels of a 96 dpi screen
SCREEN_DPI = 96

_loaded: list[str] = []


def reading_fonts() -> tuple[str, str]:
    """the families for text and for code: segoe ui and the bundled jetbrains mono."""
    mono = (load_fonts() or [FONT_FAMILY])[0]
    if not _loaded:
        families = set(QFontDatabase.families())
        if READING_FONT not in families:
            folder = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
            for name in READING_FONT_FILES:
                if (folder / name).exists():
                    font_id = QFontDatabase.addApplicationFont(str(folder / name))
                    if font_id >= 0:
                        families.update(QFontDatabase.applicationFontFamilies(font_id))
        _loaded.append(READING_FONT if READING_FONT in families else mono)
    return _loaded[0], mono


def image_sizes(base: Path, column: float) -> Callable[[str], tuple[int, int] | None]:
    """an image as wide as the column at most, never wider than it is, height in proportion."""

    def size(source: str) -> tuple[int, int] | None:
        found = QImageReader(str(base / source)).size()
        if not found.isValid() or found.width() <= 0:
            return None
        width = min(found.width(), column)
        return int(width), int(width * found.height() / found.width())

    return size


def show_html(document: QTextDocument, base: Path, style: manual_html.Style, body: str) -> None:
    document.setBaseUrl(QUrl.fromLocalFile(str(base) + "/"))
    document.setDefaultStyleSheet(manual_html.stylesheet(style))
    document.setHtml(body)


def headings(document: QTextDocument, deepest: int = 3) -> list[tuple[int, str, int]]:
    """level, text and position of the headings of a laid out document."""
    found = []
    block = document.begin()
    while block.isValid():
        level = block.blockFormat().headingLevel()
        if 2 <= level <= deepest:
            found.append((level, block.text(), block.position()))
        block = block.next()
    return found


# the pdf


def _pdf_writer(path: Path, language: str) -> QPdfWriter:
    writer = QPdfWriter(str(path))
    writer.setResolution(PDF_DPI)
    writer.setTitle(i18n.translate(N_("NeonSeek user manual"), language))
    writer.setCreator("NeonSeek")
    left, top, right, bottom = PDF_MARGINS
    writer.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Portrait,
            QMarginsF(left, top, right, bottom),
            QPageLayout.Unit.Millimeter,
        )
    )
    return writer


def _inner(page: str) -> str:
    """the body of an html page from manual_html.to_html."""
    return re.sub(r"^.*?<body[^>]*>|</body></html>$", "", page, flags=re.S)


def pdf_html(book: manual.Manual, style: manual_html.Style, widths, language: str,
             pages: list[int] | None) -> str:  # fmt: skip
    """the title, the intro, the contents with pages, every chapter from a new page."""
    page_word = i18n.translate(N_("page {number}"), language)
    rows = []
    for number, part in enumerate(book.parts, start=1):
        page = pages[number - 1] if pages and number <= len(pages) else 0
        rows.append(
            f"<tr><td>{number}.&nbsp;&nbsp;{html.escape(part.title)}</td>"
            f'<td align="right">{page_word.format(number=page)}</td></tr>'
        )
    contents = (
        f"<h3>{i18n.translate(N_('Contents'), language)}</h3>"
        f'<table width="100%" cellspacing="0" cellpadding="0" border="0" '
        f'style="line-height: 100%;">{"".join(rows)}</table>'
    )
    head = manual_html.to_html(f"# {book.title}\n\n{book.intro}", style, widths)
    opening = re.search(r"<body[^>]*>", head).group(0)
    chapters = "".join(_inner(manual_html.to_html(p.markdown, style, widths)) for p in book.parts)
    text = f"<html>{opening}{_inner(head)}{contents}{chapters}</body></html>"
    # every chapter on a new page
    return text.replace("<h2>", '<h2 style="page-break-before: always;">')


def _pdf_document(writer: QPdfWriter, source: Path, book: manual.Manual, language: str,
                  pages: list[int] | None) -> tuple[QTextDocument, QSizeF]:  # fmt: skip
    text_font, mono = reading_fonts()
    style = manual_html.print_style(text_font, mono, PDF_POINT_SIZE * PDF_DPI / 72)
    document = QTextDocument()
    font = QFont(text_font)
    font.setPointSizeF(PDF_POINT_SIZE)
    document.setDefaultFont(font)
    document.documentLayout().setPaintDevice(writer)
    document.setDocumentMargin(0)
    footer = PDF_FOOTER_MM / 25.4 * PDF_DPI
    page = QSizeF(writer.width(), writer.height() - footer)
    document.setPageSize(page)
    widths = image_sizes(source.parent, writer.width() * SCREEN_DPI / PDF_DPI)
    show_html(document, source.parent, style, pdf_html(book, style, widths, language, pages))
    # lay out everything now: qt lays out lazily, and the pages are counted from the layout
    document.setPageSize(QSizeF(page.width(), page.height() + 1))
    document.setPageSize(page)
    document.documentLayout().documentSize()
    return document, page


def _chapter_pages(document: QTextDocument, page: QSizeF) -> list[int]:
    layout = document.documentLayout()
    return [
        int(layout.blockBoundingRect(document.findBlock(pos)).top() // page.height()) + 1
        for level, _, pos in headings(document, deepest=2)
        if level == 2
    ]


def build_pdf(source: Path, out: Path, language: str) -> Path:
    """the manual as a pdf: a4, the contents with pages, every chapter on a new page, page
    numbers at the bottom. written next to the target first, so a failed build leaves no half
    file."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    book = manual.parse(source.read_text(encoding="utf-8"))
    writer = _pdf_writer(tmp, language)
    # twice: the second pass knows the pages, the numbers do not move the chapters
    document, page = _pdf_document(writer, source, book, language, None)
    pages = _chapter_pages(document, page)
    document, page = _pdf_document(writer, source, book, language, pages)
    painter = QPainter(writer)
    try:
        number_font = QFont(reading_fonts()[0])
        number_font.setPointSizeF(PDF_POINT_SIZE - 1.5)
        layout = document.documentLayout()
        for index in range(document.pageCount()):
            if index:
                writer.newPage()
            painter.save()
            # a hair inside the page, the first line of the next page starts right at its edge
            painter.setClipRect(QRectF(0, 0, page.width(), page.height() - 2))
            painter.translate(0, -index * page.height())
            context = QAbstractTextDocumentLayout.PaintContext()
            context.clip = QRectF(0, index * page.height(), page.width(), page.height())
            layout.draw(painter, context)
            painter.restore()
            painter.setFont(number_font)
            painter.setPen(Qt.GlobalColor.darkGray)
            painter.drawText(
                QRectF(0, page.height(), page.width(), writer.height() - page.height()),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom),
                str(index + 1),
            )
    finally:
        painter.end()
    del writer
    tmp.replace(out)
    return out


def pdf_for(data_dir: Path, language: str) -> Path:
    """the pdf of the manual in this language: the one in the package when it is there and as
    new as the manual, else one built now into the data folder (D-164)."""
    source = manual.source_path(language)
    built = manual.built_pdf(language)
    if manual.pdf_is_current(built, source):
        return built
    cached = manual.cached_pdf(data_dir, language)
    if not manual.pdf_is_current(cached, source):
        build_pdf(source, cached, language)
    return cached
