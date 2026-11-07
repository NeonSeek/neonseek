"""room around texts (D-194): spacing in pixels at 100 %, scaled with the text size, and the
parts that use it: descriptions with room between their lines, sections with a title and a
help button, and forms with their labels in one column."""

from __future__ import annotations

import html
from collections.abc import Callable

from PySide6.QtCore import QObject, QSize, Qt
from PySide6.QtGui import QFont, QFontMetricsF
from PySide6.QtWidgets import (
    QBoxLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from neonseek.i18n import tr
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets.common import LiveTexts, SectionLabel

# pixels at 100 %
WINDOW = 20  # inner margin of a window
SECTION = 24  # between two sections
HEADER = 10  # a section title and its content
ROW = 10  # rows of a form
HINT = 6  # a description and the field it belongs to
BUTTONS = 8  # buttons in a row
LABEL = 16  # a field label and its field
UNDERLINE = 6  # a section title and the line under it
HELP_ICON = 14
# line height of descriptions, times the text size like css line-height
HINT_LINE = 1.4

Margins = int | tuple[int, int, int, int]


def line_percent(font: QFont, factor: float) -> int:
    """the qt line height in percent for a line factor times the text size: qt counts in
    the natural line of the font, which is already about 1.33 times the size."""
    metrics = QFontMetricsF(font)
    size = font.pixelSize() if font.pixelSize() > 0 else font.pointSizeF() * 96 / 72
    natural = metrics.lineSpacing()
    if size <= 0 or natural <= 0:
        return 100
    return max(100, round(factor * size / natural * 100))


class LiveSizes(QObject):
    """margins and spacing of the layouts of a window part, set again when the text size or
    the language changes. a child of its owner, so it goes away with it."""

    def __init__(self, theme: ThemeManager, owner: QObject) -> None:
        super().__init__(owner)
        self.theme = theme
        self._items: list[Callable[[], object]] = []
        theme.changed.connect(self.apply)
        theme.language_changed.connect(self.apply)

    def px(self, size: int) -> int:
        return self.theme.px(size) if size else 0

    def call(self, fn: Callable[[], object]) -> None:
        fn()
        self._items.append(fn)

    def layout(
        self,
        layout: QLayout,
        margins: Margins | None = None,
        spacing: int | None = None,
        horizontal: int | None = None,
        vertical: int | None = None,
    ) -> QLayout:
        def apply() -> None:
            if margins is not None:
                sides = margins if isinstance(margins, tuple) else (margins,) * 4
                layout.setContentsMargins(*(self.px(side) for side in sides))
            if spacing is not None:
                layout.setSpacing(self.px(spacing))
            if isinstance(layout, QGridLayout):
                if horizontal is not None:
                    layout.setHorizontalSpacing(self.px(horizontal))
                if vertical is not None:
                    layout.setVerticalSpacing(self.px(vertical))

        self.call(apply)
        return layout

    def apply(self) -> None:
        alive = []
        for item in self._items:
            try:
                item()
            except RuntimeError:  # its layout went away with a page that was replaced
                continue
            alive.append(item)
        self._items = alive


def button_row(sizes: LiveSizes, stretch_first: bool = True) -> QHBoxLayout:
    """a row of buttons with room between them, right aligned unless told otherwise."""
    row = QHBoxLayout()
    sizes.layout(row, margins=0, spacing=BUTTONS)
    if stretch_first:
        row.addStretch(1)
    return row


class Hint(QLabel):
    """a description: muted, wrapped, with room between its lines (D-194). text() gives the
    plain text back, the label shows it as rich text for the line height."""

    def __init__(self, text: str = "", parent: QWidget | None = None, role: str = "muted"):
        super().__init__(parent)
        self._plain = ""
        if role:
            self.setProperty("role", role)
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.RichText)
        # at the top of its room: qt may guess one line too many for a long path
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - qt name
        self._plain = text
        if not text:
            super().setText("")
            return
        body = html.escape(text).replace("\n", "<br>")
        line = line_percent(self.font(), HINT_LINE)
        super().setText(f'<div style="line-height: {line}%;">{body}</div>')

    def text(self) -> str:
        return self._plain


class HelpButton(QPushButton):
    """a HELP button with the book icon: opens a chapter of the manual (D-195). small next to
    a section title, of the usual size in a row of buttons."""

    def __init__(
        self,
        theme: ThemeManager,
        tip: Callable[[], str] | None = None,
        small: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.theme = theme
        self.small = small
        if small:
            self.setProperty("role", "help")
        texts = LiveTexts(theme, self)
        texts.text(self, lambda: tr("HELP"))
        texts.tip(self, tip or (lambda: tr("Opens the chapter of the manual about this section")))
        theme.changed.connect(self._paint)
        self._paint()

    def _paint(self) -> None:
        self.setIcon(self.theme.icon("manual", "text.muted" if self.small else "text.primary"))
        side = self.theme.px(HELP_ICON)
        self.setIconSize(QSize(side, side))


class Section(QWidget):
    """a titled part of a page: the title on a thin line, an optional HELP button at its right
    end, then the rows of the section."""

    def __init__(
        self,
        theme: ThemeManager,
        title: Callable[[], str],
        on_help: Callable[[], object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("role", "clear")
        self.sizes = LiveSizes(theme, self)
        texts = LiveTexts(theme, self)
        outer = self.sizes.layout(QVBoxLayout(self), margins=0, spacing=HEADER)
        self.head = QWidget()
        self.head.setProperty("role", "sectionhead")
        self.head.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        head = self.sizes.layout(QHBoxLayout(self.head), margins=(0, 0, 0, UNDERLINE), spacing=8)
        self.title = texts.text(SectionLabel(""), title)
        head.addWidget(self.title, 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        self.help: HelpButton | None = None
        if on_help is not None:
            self.help = HelpButton(theme)
            self.help.clicked.connect(on_help)
            head.addWidget(self.help, 0, Qt.AlignmentFlag.AlignBottom)
        outer.addWidget(self.head)
        self.rows = self.sizes.layout(QVBoxLayout(), margins=0, spacing=ROW)
        outer.addLayout(self.rows)

    def add(self, *items: QWidget | QLayout) -> QBoxLayout:
        """one row: a field, or a field with its description, kept close together."""
        if len(items) == 1:
            self._add(self.rows, items[0])
            return self.rows
        group = self.sizes.layout(QVBoxLayout(), margins=0, spacing=HINT)
        for item in items:
            self._add(group, item)
        self.rows.addLayout(group)
        return group

    @staticmethod
    def _add(layout: QBoxLayout, item: QWidget | QLayout) -> None:
        if isinstance(item, QLayout):
            layout.addLayout(item)
        else:
            layout.addWidget(item)


class FormGrid(QGridLayout):
    """labels in one column with room to their fields, a description under its field. the
    label stands at the height of the field, not of the field and its description."""

    def __init__(self, sizes: LiveSizes) -> None:
        super().__init__()
        self.sizes = sizes
        self.labels: list[tuple[QLabel, QWidget]] = []
        sizes.layout(self, margins=0, horizontal=LABEL, vertical=ROW)
        self.setColumnStretch(1, 1)
        sizes.call(self._match)

    def add_row(
        self, label: QLabel, field: QWidget, hint: QLabel | None = None, width: int = 0
    ) -> None:
        """width: a narrow field of this many pixels at 100 %, like a number."""
        row = self.rowCount()
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
        cell = self.sizes.layout(QVBoxLayout(), margins=0, spacing=HINT)
        if width:
            cell.addWidget(field, 0, Qt.AlignmentFlag.AlignLeft)
            self.sizes.call(lambda: field.setFixedWidth(self.sizes.px(width)))
        else:
            cell.addWidget(field)
        if hint is not None:
            cell.addWidget(hint)
        self.addLayout(cell, row, 1)
        self.labels.append((label, field))
        self._match()

    def label_width(self) -> int:
        return max((label.sizeHint().width() for label, _ in self.labels), default=0)

    def _match(self) -> None:
        for label, field in self.labels:
            label.setMinimumHeight(field.sizeHint().height())


def same_label_column(sizes: LiveSizes, *grids: FormGrid) -> None:
    """several forms of one window with their fields starting at the same place."""

    def match() -> None:
        width = max(grid.label_width() for grid in grids)
        for grid in grids:
            grid.setColumnMinimumWidth(0, width)

    sizes.call(match)
