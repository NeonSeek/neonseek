"""small widgets shared by the window parts."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from PySide6.QtCore import QObject, QRect, QSize, Qt
from PySide6.QtGui import QFont, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from neonseek.ui.covers import CoverLoader
from neonseek.ui.theme.loader import ThemeManager

# letter spacing in percent of the font, so it grows with the text size
SECTION_SPACING = 116.0


W = TypeVar("W")


class LiveTexts(QObject):
    """the fixed texts of a window part, set again when the language changes (D-163). each
    text is a function, so its tr() runs again in the new language. a child of its owner, so it
    goes away with it and stops listening."""

    def __init__(self, theme: ThemeManager, owner: QObject) -> None:
        super().__init__(owner)
        self._items: list[Callable[[], object]] = []
        theme.language_changed.connect(self.apply)

    def add(self, setter: Callable[[str], object], text: Callable[[], str]) -> None:
        setter(text())
        self._items.append(lambda: setter(text()))

    def call(self, fn: Callable[[], object]) -> None:
        """anything else with text, like a list of choices: run now and at every change."""
        fn()
        self._items.append(fn)

    def text(self, widget: W, text: Callable[[], str]) -> W:
        self.add(widget.setText, text)  # type: ignore[attr-defined]
        return widget

    def tip(self, widget: W, text: Callable[[], str]) -> W:
        self.add(widget.setToolTip, text)  # type: ignore[attr-defined]
        return widget

    def placeholder(self, widget: W, text: Callable[[], str]) -> W:
        self.add(widget.setPlaceholderText, text)  # type: ignore[attr-defined]
        return widget

    def apply(self) -> None:
        for item in self._items:
            item()


def set_role(widget: QWidget, role: str) -> None:
    widget.setProperty("role", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class SectionLabel(QLabel):
    """uppercase label with wide letter spacing (PLAN.md section 9)."""

    def __init__(self, text: str, parent: QWidget | None = None, role: str = "section") -> None:
        super().__init__(text, parent)
        self.setProperty("role", role)
        font = self.font()
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, SECTION_SPACING)
        self.setFont(font)


class ElidedSectionLabel(SectionLabel):
    """a section label cut with an ellipsis, for texts that can be long."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._full = text

    def setText(self, text: str) -> None:  # noqa: N802 - qt name
        self._full = text
        self.setToolTip(text)
        super().setText(text)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - qt name
        return QSize(10, super().minimumSizeHint().height())

    def paintEvent(self, event) -> None:  # noqa: N802 - qt name
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, rect.width())
        align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.drawText(rect, int(align), text)


class ElidedLabel(QLabel):
    """one line of text cut with an ellipsis instead of widening the layout."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text
        # the full text is the tooltip, unless a tooltip of its own is set
        self._own_tip = False
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - qt name
        self._full = text
        if not self._own_tip:
            super().setToolTip(text)
        super().setText(text)
        self.update()

    def setToolTip(self, text: str) -> None:  # noqa: N802 - qt name
        self._own_tip = bool(text)
        super().setToolTip(text or self._full)

    def full_text(self) -> str:
        return self._full

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - qt name
        hint = super().minimumSizeHint()
        return QSize(10, hint.height())

    def paintEvent(self, event) -> None:  # noqa: N802 - qt name
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(rect, int(self.alignment() | Qt.AlignmentFlag.AlignVCenter), text)


def draw_thumb(
    painter: QPainter,
    rect: QRect,
    theme: ThemeManager,
    covers: CoverLoader | None,
    url: str | None,
    fallback_icon: str = "cover",
) -> None:
    """a cover thumbnail, or the placeholder icon on a chip square in theme colors."""
    dpr = painter.device().devicePixelRatioF() if painter.device() else 1.0
    pixmap = covers.pixmap(url, rect.width(), dpr) if covers is not None else None
    if pixmap is not None:
        painter.drawPixmap(rect, pixmap)
        return
    painter.fillRect(rect, theme.color("chip.bg"))
    inner = max(8, round(rect.width() * 0.6))
    icon = theme.pixmap(fallback_icon, "text.muted", inner, dpr)
    x = rect.x() + (rect.width() - inner) // 2
    y = rect.y() + (rect.height() - inner) // 2
    painter.drawPixmap(QRect(x, y, inner, inner), icon)


class Thumb(QWidget):
    """a square cover that repaints when its cover arrives. size is in pixels at 100 %."""

    def __init__(
        self,
        size: int,
        theme: ThemeManager,
        covers: CoverLoader | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.base_size = size
        self.theme = theme
        self.covers = covers
        self.url: str | None = None
        self.icon = "cover"
        self._rescale()
        theme.changed.connect(self._rescale)
        if covers is not None:
            covers.ready.connect(self._ready)

    def _rescale(self) -> None:
        side = self.theme.px(self.base_size)
        self.setFixedSize(side, side)
        self.update()

    def set_cover(self, url: str | None, icon: str = "cover") -> None:
        self.url = url
        self.icon = icon
        self.update()

    def _ready(self, url: str) -> None:
        if url == self.url:
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - qt name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        draw_thumb(painter, self.rect(), self.theme, self.covers, self.url, self.icon)


def icon_pixmap(theme: ThemeManager, name: str, token: str, size: int, widget: QWidget) -> QPixmap:
    return theme.pixmap(name, token, size, widget.devicePixelRatioF())
