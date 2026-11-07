"""what every dialog shares: theme tracking, a title and a view for result lines."""

from __future__ import annotations

import html
from collections.abc import Callable

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QThread, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from neonseek import outcomes
from neonseek.i18n import tr
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets import spacing
from neonseek.ui.widgets.common import LiveTexts, SectionLabel

KIND_TOKEN = {
    outcomes.INFO: "text.primary",
    outcomes.DONE: "state.success",
    outcomes.WARN: "state.warning",
    outcomes.ERROR: "state.error",
}


def line_html(theme: ThemeManager, text: str, kind: str, prefix: str = "") -> str:
    color = theme.theme.colors[KIND_TOKEN.get(kind, "text.primary")]
    muted = theme.theme.colors["text.muted"]
    head = f'<span style="color:{muted}">{html.escape(prefix)}</span> ' if prefix else ""
    # pre-wrap keeps the indent of a line and still wraps at spaces
    body = html.escape(text)
    return f'{head}<span style="color:{color}; white-space:pre-wrap">{body}</span>'


def plain_table(table: QTableWidget) -> QTableWidget:
    """the look of the track table: no grid, headers on the left, no row numbers."""
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setDefaultAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    )
    table.horizontalHeader().setHighlightSections(False)
    return table


def close_when_idle(window: QWidget) -> bool:
    """false while a worker of this window still runs. cancels what can be cancelled, so a
    thread is never destroyed while it runs."""
    # a thread whose finished signal is on its way ends within moments
    running = [w for w in window.findChildren(QThread) if w.isRunning() and not w.wait(300)]
    if not running:
        return True
    for worker in running:
        cancel = getattr(worker, "cancel", None)
        if cancel is not None:
            cancel()
    QMessageBox.information(
        window,
        "NeonSeek",
        tr(
            "NeonSeek is still finishing what you started here. Close the window again in a moment."
        ),  # fmt: skip
    )
    return False


# the smallest size of a window, as a share of the size it opens with (D-252)
MIN_SHARE = 0.6
# between the parts of a dialog and above its row of buttons
BODY_SPACING = 14


def available_area(window: QWidget) -> QRect:
    """the part of the screen a window may use, without the taskbar."""
    screen = window.screen() or QGuiApplication.primaryScreen()
    return screen.availableGeometry() if screen is not None else QRect()


def fit_to_screen(window: QWidget, room: QWidget | None = None, area: QRect | None = None) -> None:
    """the whole window stays on the screen with its row of buttons: a big text size or a
    big windows scale on a small screen would push the bottom of a window off it.

    a minimum bigger than the screen gives way: on the window itself, or on room (the part
    of the window that scrolls) when the minimum comes from there (D-252)."""
    area = area if area is not None else available_area(window)
    if area.isEmpty():
        return
    frame = window.frameGeometry()
    extra_w, extra_h = frame.width() - window.width(), frame.height() - window.height()
    max_w, max_h = area.width() - extra_w, area.height() - extra_h
    over_w = max(0, window.minimumWidth() - max_w)
    over_h = max(0, window.minimumHeight() - max_h)
    if over_w or over_h:
        target = room or window
        target.setMinimumSize(max(0, target.minimumWidth() - over_w),
                              max(0, target.minimumHeight() - over_h))  # fmt: skip
        if room is not None and window.layout() is not None:
            window.layout().activate()
    width, height = min(window.width(), max_w), min(window.height(), max_h)
    width, height = max(width, window.minimumWidth()), max(height, window.minimumHeight())
    if (width, height) != (window.width(), window.height()):
        window.resize(width, height)
    frame = window.frameGeometry()
    x = min(max(frame.x(), area.x()), area.x() + area.width() - frame.width())
    y = min(max(frame.y(), area.y()), area.y() + area.height() - frame.height())
    if (x, y) != (frame.x(), frame.y()):
        window.move(x, y)


class FitContent(QWidget):
    """the content inside a FreeScroll. the scroll area sizes wrapped content by its height
    for the width, and qt answers that with the preferred heights: the window would scroll
    while everything still fits. this answers with the smallest heights instead, so it
    scrolls only when the content really does not fit (D-252)."""

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - qt name
        layout = self.layout()
        if layout is None or not layout.hasHeightForWidth():
            return super().heightForWidth(width)
        return layout.totalMinimumHeightForWidth(width)


class FreeScroll(QScrollArea):
    """the content of a window in a scroll area without a frame.

    wrapped texts right in the layout of a window tie its height to its width: qt then
    corrects every corner drag, a narrower window gets taller by itself. inside this area the
    window resizes freely in every direction, and a window smaller than its content scrolls
    instead of cutting texts (D-252)."""

    def __init__(self, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "plain")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        content.setProperty("role", "plain")
        self.setWidget(content)


class Dialog(QDialog):
    def __init__(
        self, theme: ThemeManager, title: Callable[[], str], parent: QWidget | None = None
    ) -> None:
        """the title is a function, so it follows the language like the other texts.

        the content goes to body, which scrolls when the window is smaller than it, the row
        of buttons to footer, which stays at the bottom (D-252)."""
        super().__init__(parent)
        self.theme = theme
        # fixed texts of the dialog, set again when the language changes (D-163)
        self.texts = LiveTexts(theme, self)
        theme.track_window(self)
        # margins and spacing that grow with the text size (D-194)
        self.sizes = spacing.LiveSizes(theme, self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        content = FitContent()
        self.body = QVBoxLayout(content)
        # the room between the content and the buttons is the body's own spacing
        self.sizes.layout(self.body, margins=(spacing.WINDOW, spacing.WINDOW, spacing.WINDOW,
                                              BODY_SPACING), spacing=BODY_SPACING)  # fmt: skip
        self.room = FreeScroll(content)
        outer.addWidget(self.room, 1)
        self.footer = QVBoxLayout()
        self.sizes.layout(self.footer, margins=(spacing.WINDOW, 0, spacing.WINDOW,
                                                spacing.WINDOW), spacing=BODY_SPACING)  # fmt: skip
        outer.addLayout(self.footer)
        self.title = SectionLabel("")
        self.body.addWidget(self.title)
        self.texts.call(lambda: self._set_title(title()))

    def _set_title(self, title: str) -> None:
        self.setWindowTitle(f"NeonSeek - {title}")
        self.title.setText(title.upper())

    def resize_scaled(self, width: int, height: int) -> None:
        """a size in pixels at 100 %, bigger with a bigger text size. the window may get
        smaller than that down to MIN_SHARE of it, and its content scrolls then (D-252)."""
        self.resize(self.theme.px(width), self.theme.px(height))
        # the row of buttons below comes on top of the room for the content
        self.room.setMinimumSize(self.theme.px(width * MIN_SHARE),
                                   self.theme.px(height * (MIN_SHARE - 0.1)))  # fmt: skip

    def available_area(self) -> QRect:
        return available_area(self)

    def showEvent(self, event) -> None:  # noqa: N802 - qt name
        super().showEvent(event)
        # once windows has added the frame, so the frame counts too
        QTimer.singleShot(0, self, self.fit_screen)

    def fit_screen(self) -> None:
        fit_to_screen(self, self.room, self.available_area())

    def done(self, result: int) -> None:
        if close_when_idle(self):
            super().done(result)


class LineView(QPlainTextEdit):
    """result lines from core in their colors, like the log panel.

    its size is counted in lines of text, not pixels, so it follows the text size: at least
    min_lines, about hint_lines where the window has no room to give, and with a stretch
    factor in its layout it takes the free room of the window (D-251)."""

    def __init__(
        self,
        theme: ThemeManager,
        parent: QWidget | None = None,
        min_lines: int = 3,
        hint_lines: int = 5,
        max_lines: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.theme = theme
        self.max_lines = max_lines
        self.min_lines = min(min_lines, max_lines or min_lines)
        self.hint_lines = min(hint_lines, max_lines or hint_lines)
        self._cap()
        self.setProperty("role", "console")
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._lines: list[outcomes.Line] = []
        theme.changed.connect(self._repaint)
        # takes no room until there is something to say
        self.setVisible(False)

    def _lines_height(self, lines: int) -> int:
        margins = self.contentsMargins()
        text = self.fontMetrics().lineSpacing() * lines + 2 * self.document().documentMargin()
        return round(text) + margins.top() + margins.bottom()

    def _cap(self) -> None:
        # a log next to a table stays short, in lines of the current text size
        if self.max_lines is not None:
            self.setMaximumHeight(self._lines_height(self.max_lines))

    def changeEvent(self, event) -> None:  # noqa: N802 - qt name
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._cap()
            self.updateGeometry()

    def sizeHint(self) -> QSize:  # noqa: N802 - qt name
        return QSize(super().sizeHint().width(), self._lines_height(self.hint_lines))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - qt name
        return QSize(super().minimumSizeHint().width(), self._lines_height(self.min_lines))

    def add_lines(self, lines: list[outcomes.Line]) -> None:
        if lines:
            self.setVisible(True)
        for line in lines:
            self._lines.append(line)
            self.appendHtml(line_html(self.theme, line.text, line.kind))

    def add(self, text: str, kind: str = outcomes.INFO) -> None:
        self.add_lines([outcomes.Line(text, kind)])

    def clear_lines(self) -> None:
        self._lines = []
        self.clear()
        self.setVisible(False)

    def _repaint(self) -> None:
        lines, self._lines = self._lines, []
        self.clear()
        self.add_lines(lines)
