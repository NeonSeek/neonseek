"""bottom panel: live log like a terminal, global progress and the search limit wait."""

from __future__ import annotations

import html
from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStyle,
    QStyleOptionProgressBar,
    QStylePainter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from neonseek import themes
from neonseek.i18n import tr
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets.common import LiveTexts, SectionLabel

MAX_LINES = 5000
# an open log shows at least this many lines, below that the window folds it (D-258)
LEAST_LINES = 3

# line kinds and their theme tokens
INFO = "info"
DONE = "done"
WARN = "warn"
ERROR = "error"
KIND_TOKEN = {INFO: "text.primary", DONE: "state.success", WARN: "state.warning",
              ERROR: "state.error"}  # fmt: skip


class ProgressBar(QProgressBar):
    """the text stays readable on the filled part too: there it takes the theme color that
    stands out most against the gradient, over the empty part text.primary (D-133)."""

    def __init__(self, theme: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme

    def text_colors(self) -> tuple[QColor, QColor]:
        """over the filled part, over the empty part."""
        fill = themes.progress_text_token(self.theme.theme)
        return self.theme.color(fill), self.theme.color("text.primary")

    def fill_edge(self) -> int:
        """x where the filled part ends, inside the 1 px border."""
        inner = self.rect().adjusted(1, 1, -1, -1)
        span = self.maximum() - self.minimum()
        if span <= 0:
            return inner.x()
        share = (self.value() - self.minimum()) / span
        return inner.x() + round(inner.width() * max(0.0, min(1.0, share)))

    def paintEvent(self, event) -> None:  # noqa: N802 - qt name
        option = QStyleOptionProgressBar()
        self.initStyleOption(option)
        text = option.text
        option.textVisible = False
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_ProgressBar, option)
        if not self.isTextVisible() or not text:
            return
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        rect = self.rect()
        edge = self.fill_edge()
        fill, empty = self.text_colors()
        parts = (
            (QRect(rect.x(), rect.y(), edge - rect.x(), rect.height()), fill),
            (QRect(edge, rect.y(), rect.right() - edge + 1, rect.height()), empty),
        )
        for clip, color in parts:
            if clip.width() <= 0:
                continue
            painter.save()
            painter.setClipRect(clip)
            painter.setPen(color)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
            painter.restore()


class LogPanel(QFrame):
    collapsed_changed = Signal(bool)
    cancel_clicked = Signal()

    def __init__(
        self,
        theme: ThemeManager,
        parent: QWidget | None = None,
        sink: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("role", "panel")
        self.theme = theme
        # every line also goes to the app log file
        self.sink = sink
        self._lines: list[tuple[str, str, str]] = []
        self.texts = LiveTexts(theme, self)
        # what cancel stops: the download, or a library move (set_cancel_tip)
        self._cancel_tip: Callable[[], str] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QWidget()
        bar.setProperty("role", "surface")
        self.bar = bar
        bar_layout = QHBoxLayout(bar)
        self.bar_layout = bar_layout
        self.toggle = QToolButton()
        self.toggle.setProperty("flat", True)
        self.texts.tip(self.toggle, lambda: tr("Show or hide the log"))
        self.toggle.clicked.connect(lambda: self.set_collapsed(not self.collapsed))
        bar_layout.addWidget(self.toggle)
        bar_layout.addWidget(self.texts.text(SectionLabel(""), lambda: tr("LOG")))
        self.state = QLabel()
        self.state.setProperty("role", "muted")
        bar_layout.addWidget(self.state, 1)
        self.waiting = self.texts.text(
            QLabel(), lambda: tr("WAITING FOR THE SOULSEEK SEARCH LIMIT")
        )
        self.waiting.setProperty("role", "waiting")
        self.waiting.setVisible(False)
        bar_layout.addWidget(self.waiting)
        self.progress = ProgressBar(theme)
        self.progress.setTextVisible(True)
        self.progress.setVisible(False)
        bar_layout.addWidget(self.progress)
        self.cancel = self.texts.text(QPushButton(), lambda: tr("CANCEL"))
        self.texts.add(self.cancel.setToolTip, self._cancel_text)
        self.cancel.clicked.connect(self.cancel_clicked)
        self.cancel.setVisible(False)
        bar_layout.addWidget(self.cancel)
        layout.addWidget(bar)

        self.console = QPlainTextEdit()
        self.console.setProperty("role", "console")
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(MAX_LINES)
        self.console.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.console, 1)

        self.collapsed = False
        theme.changed.connect(self._repaint)
        theme.changed.connect(self._rescale)
        self._rescale()
        self._repaint()

    def _rescale(self) -> None:
        px = self.theme.px
        self.bar_layout.setContentsMargins(px(10), px(4), px(8), px(4))
        self.bar_layout.setSpacing(px(10))
        self.progress.setFixedWidth(px(260))

    def _cancel_text(self) -> str:
        if self._cancel_tip is not None:
            return self._cancel_tip()
        return tr("Stops the download. Finished tracks are kept, the rest waits for the next one.")

    def set_cancel_tip(self, text: Callable[[], str] | None) -> None:
        """another tip for cancel while a library move runs, none for the download again."""
        self._cancel_tip = text
        self.cancel.setToolTip(self._cancel_text())

    # lines

    def add(self, text: str, kind: str = INFO) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        if self.sink is not None:
            self.sink(text, kind)
        for line in text.splitlines() or [""]:
            self._lines.append((stamp, kind, line))
            self.console.appendHtml(self._html(stamp, kind, line))
        if len(self._lines) > MAX_LINES:
            del self._lines[: len(self._lines) - MAX_LINES]

    def _html(self, stamp: str, kind: str, line: str) -> str:
        muted = self.theme.theme.colors["text.muted"]
        color = self.theme.theme.colors[KIND_TOKEN.get(kind, "text.primary")]
        return (
            f'<span style="color:{muted}">{stamp} &gt;</span> '
            f'<span style="color:{color}">{html.escape(line)}</span>'
        )

    def _repaint(self) -> None:
        # colors live in the html of each line, so a new theme writes them again
        self.console.clear()
        for stamp, kind, line in self._lines:
            self.console.appendHtml(self._html(stamp, kind, line))
        self._paint_toggle()

    def text(self) -> str:
        return "\n".join(f"{stamp} > {line}" for stamp, _, line in self._lines)

    # state, progress, waiting

    def set_state(self, text: str) -> None:
        self.state.setText(text)

    def set_waiting(self, waiting: bool) -> None:
        self.waiting.setVisible(waiting)

    def set_progress(self, done: int | None, total: int | None = None, text: str = "") -> None:
        """none hides the bar. total 0 shows an empty bar with the text, a moving busy bar
        would run under the text."""
        if done is None:
            self.progress.setVisible(False)
            self.theme.no_glow(self.progress)
            return
        self.progress.setVisible(True)
        self.progress.setRange(0, total or 1)
        self.progress.setValue(done if total else 0)
        self.progress.update()
        self.progress.setFormat(text or "%v / %m")
        # the running progress is the active element, so it may glow
        self.theme.glow(self.progress)

    # collapsing

    def folded_height(self) -> int:
        """the height of the folded log: only its bar."""
        return self.bar.sizeHint().height() + 2 * self.frameWidth()

    def least_open_height(self) -> int:
        """the bar and a few lines of the log."""
        console = self.console
        lines = LEAST_LINES * console.fontMetrics().lineSpacing()
        inside = 2 * (console.frameWidth() + round(console.document().documentMargin()))
        return self.folded_height() + lines + inside

    def set_collapsed(self, collapsed: bool) -> None:
        self.collapsed = collapsed
        self.console.setVisible(not collapsed)
        self._paint_toggle()
        self.collapsed_changed.emit(collapsed)

    def _paint_toggle(self) -> None:
        name = "chevron-up" if self.collapsed else "chevron-down"
        self.toggle.setIcon(self.theme.icon(name, "text.muted"))
