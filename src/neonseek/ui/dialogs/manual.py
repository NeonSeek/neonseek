"""the user manual in the language of the window (D-164, D-182): one chapter at a time, the
contents and a search on the left, the pdf. it does not block the window that opened it."""

from __future__ import annotations

import base64
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QKeySequence,
    QPixmap,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neonseek import actions, config, i18n, manual, manual_html, outcomes
from neonseek.errors import NeonSeekError
from neonseek.i18n import tr
from neonseek.textfold import find_all
from neonseek.ui import manual_view
from neonseek.ui.dialogs.base import Dialog, LineView
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets import spacing

CHAPTER_ROLE = Qt.ItemDataRole.UserRole
ANCHOR_ROLE = Qt.ItemDataRole.UserRole + 1
# the text of the manual in pixels at 100 %, and the longest line in characters
READING_PX = 15
# line height of the text, times the text size (D-194)
READING_LINE = 1.5
TOC_PX = 13
LINE_CHARS = 80
# the room left and right of the text column at 100 %
SIDE_PX = 28
# share of the screen a new help window takes
SCREEN_SHARE = 0.85
STATE_KEY = "manual"


class ImageViewer(QDialog):
    """a screenshot of the manual at its full size, scrolling when it is bigger than the
    screen."""

    def __init__(self, path: Path, theme: ThemeManager, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"NeonSeek - {path.name}")
        theme.track_window(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel()
        label.setPixmap(QPixmap(str(path)))
        scroll = QScrollArea()
        scroll.setWidget(label)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(scroll)
        screen = (parent.screen() if parent else None) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        size = label.pixmap().size()
        self.resize(min(size.width() + 24, int(area.width() * 0.95)),
                    min(size.height() + 24, int(area.height() * 0.95)))  # fmt: skip


class ManualWindow(Dialog):
    def __init__(
        self,
        ctx: actions.AppContext,
        theme: ThemeManager,
        parent=None,
        chapter: str | None = None,
    ) -> None:
        super().__init__(theme, lambda: tr("Manual"), parent)
        self.ctx = ctx
        self.setModal(False)
        # a window of its own with the buttons to make it big or small (D-182)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        state = config.load_ui_state(ctx.data_dir).get(STATE_KEY, {})
        self.state = state if isinstance(state, dict) else {}
        self.chapter: str = chapter or self.state.get("chapter") or ""
        self.query = ""
        self.manual = manual.Manual("", "", [])
        self.source = manual.source_path(i18n.language())
        texts = self.texts

        self.split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        texts.placeholder(self.search, lambda: tr("Search the manual (Ctrl+F)"))
        left_layout.addWidget(self.search)
        self.hits = QLabel()
        self.hits.setProperty("role", "muted")
        self.hits.setWordWrap(True)
        self.hits.setVisible(False)
        left_layout.addWidget(self.hits)
        self.contents = QTreeWidget()
        self.contents.setProperty("role", "toc")
        self.contents.setHeaderHidden(True)
        self.contents.setWordWrap(True)
        self.contents.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.contents.itemClicked.connect(self._clicked)
        self.contents.itemActivated.connect(self._clicked)
        left_layout.addWidget(self.contents, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        self.text = QTextBrowser()
        self.text.setProperty("role", "manual")
        self.text.setOpenLinks(False)
        self.text.anchorClicked.connect(self._link)
        self.text.viewport().installEventFilter(self)
        right_layout.addWidget(self.text, 1)
        nav = QHBoxLayout()
        self.previous = QPushButton()
        self.previous.clicked.connect(lambda: self._step(-1))
        self.next = QPushButton()
        self.next.clicked.connect(lambda: self._step(1))
        nav.addWidget(self.previous)
        nav.addStretch(1)
        nav.addWidget(self.next)
        right_layout.addLayout(nav)

        self.split.addWidget(left)
        self.split.addWidget(right)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setChildrenCollapsible(False)
        sizes = self.state.get("splitter")
        self.split.setSizes(
            sizes if isinstance(sizes, list) and len(sizes) == 2 else [theme.px(300), 900]
        )
        self.body.addWidget(self.split, 1)
        self.lines = LineView(theme, max_lines=3)
        self.body.addWidget(self.lines)
        row = spacing.button_row(self.sizes)
        self.pdf = texts.text(QPushButton(), lambda: tr("OPEN PDF"))
        texts.tip(self.pdf, lambda: tr("The same manual as a PDF, to read or print."))
        self.pdf.clicked.connect(self.open_pdf)
        close = texts.text(QPushButton(), lambda: tr("CLOSE"))
        close.clicked.connect(self.accept)
        row.addWidget(self.pdf)
        row.addWidget(close)
        self.footer.addLayout(row)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._apply_search)
        self.search.textChanged.connect(lambda _: self._search_timer.start())
        self._fit = QTimer(self)
        self._fit.setSingleShot(True)
        self._fit.setInterval(120)
        self._fit.timeout.connect(lambda: self.render(keep_scroll=True))
        QShortcut(QKeySequence.StandardKey.Find, self, self._focus_search)
        # the text size of the app, also from here (D-115)
        for keys, step in (
            (QKeySequence.StandardKey.ZoomIn, 1),
            (QKeySequence("Ctrl+="), 1),
            (QKeySequence.StandardKey.ZoomOut, -1),
            (QKeySequence("Ctrl+0"), 0),
        ):
            QShortcut(keys, self, lambda s=step: self.step_scale(s))
        theme.changed.connect(self._restyle)
        theme.language_changed.connect(self.load)
        self._column = 0
        self._restore_place()
        self._restyle(render=False)
        self.load()

    # place and size

    def _restore_place(self) -> None:
        saved = self.state.get("geometry")
        if isinstance(saved, str):
            try:
                if self.restoreGeometry(QByteArray(base64.b64decode(saved))):
                    return
            except ValueError:
                pass
        parent = self.parentWidget()
        screen = (parent.screen() if parent else None) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        width, height = int(area.width() * SCREEN_SHARE), int(area.height() * SCREEN_SHARE)
        left = area.x() + (area.width() - width) // 2
        top = area.y() + (area.height() - height) // 2
        self.setGeometry(QRect(left, top, width, height))

    def save_state(self) -> None:
        state = config.load_ui_state(self.ctx.data_dir)
        state[STATE_KEY] = {
            "geometry": base64.b64encode(bytes(self.saveGeometry())).decode("ascii"),
            "splitter": self.split.sizes(),
            "chapter": self.chapter,
        }
        config.save_ui_state(state, self.ctx.data_dir)

    def done(self, result: int) -> None:
        self.save_state()
        super().done(result)

    # looks

    def _restyle(self, render: bool = True) -> None:
        """fonts and colors of the theme and the text size, without changing the chapter."""
        font, _ = manual_view.reading_fonts()
        for widget, size in ((self.contents, TOC_PX), (self.search, TOC_PX), (self.hits, TOC_PX)):
            qfont = QFont(font)
            qfont.setPixelSize(self.theme.px(size))
            widget.setFont(qfont)
        self.pdf.setIcon(self.theme.icon("document", "text.primary"))
        self.contents.setIndentation(self.theme.px(16))
        if render:
            self.render(keep_scroll=True)

    def html_style(self) -> manual_html.Style:
        font, mono = manual_view.reading_fonts()
        qfont = QFont(font)
        qfont.setPixelSize(self.theme.px(READING_PX))
        # lines 1.5 times the text size apart, not more (D-194)
        line = spacing.line_percent(qfont, READING_LINE)
        return manual_html.window_style(
            self.theme.theme.colors, font, mono, self.theme.px(READING_PX), line
        )

    def column(self) -> int:
        """the width of the text: about 80 characters, less on a narrow window."""
        font, _ = manual_view.reading_fonts()
        qfont = QFont(font)
        qfont.setPixelSize(self.theme.px(READING_PX))
        wanted = QFontMetricsF(qfont).averageCharWidth() * LINE_CHARS
        room = self.text.viewport().width() - 2 * self.theme.px(SIDE_PX)
        return int(max(self.theme.px(240), min(wanted, room)))

    def _center(self) -> None:
        """the text column in the middle of a wide window."""
        document = self.text.document()
        side = max(self.theme.px(SIDE_PX), (self.text.viewport().width() - self._column) // 2)
        frame = document.rootFrame()
        fmt = frame.frameFormat()
        fmt.setLeftMargin(side)
        fmt.setRightMargin(side)
        fmt.setTopMargin(self.theme.px(18))
        fmt.setBottomMargin(self.theme.px(28))
        frame.setFrameFormat(fmt)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - qt name
        if watched is self.text.viewport() and event.type() == QEvent.Type.Resize:
            if self.column() != self._column:
                self._fit.start()
            else:
                self._center()
        return super().eventFilter(watched, event)

    # content

    def load(self) -> None:
        """the manual in the language the window speaks now, in the same chapter."""
        self.source = manual.source_path(i18n.language())
        try:
            self.manual = manual.parse(self.source.read_text(encoding="utf-8"))
        except OSError as exc:
            self.manual = manual.Manual("", "", [])
            self.text.setPlainText(
                tr("The manual is missing ({error}). Install NeonSeek again.").format(error=exc)
            )
            self.contents.clear()
            return
        self._fill_contents()
        if self.manual.part_of(self.chapter) is None and self.manual.parts:
            self.chapter = self.manual.parts[0].id
        self.show_chapter(self.chapter)
        if self.search.text().strip():
            self._apply_search()

    def _fill_contents(self) -> None:
        self.contents.clear()
        for part in self.manual.parts:
            item = QTreeWidgetItem([part.title])
            item.setData(0, CHAPTER_ROLE, part.id)
            item.setToolTip(0, part.title)
            for section in part.sections:
                child = QTreeWidgetItem([section.title])
                child.setData(0, CHAPTER_ROLE, part.id)
                child.setData(0, ANCHOR_ROLE, section.id)
                child.setToolTip(0, section.title)
                item.addChild(child)
            self.contents.addTopLevelItem(item)

    def show_chapter(self, any_id: str | None, anchor: str | None = None) -> None:
        """a chapter from its top, or a section of it. the id may be a section's."""
        part = self.manual.part_of(any_id)
        if part is None:
            return
        if any_id != part.id and anchor is None:
            anchor = any_id
        changed = part.id != self.chapter
        self.chapter = part.id
        self.render()
        if anchor:
            self.text.scrollToAnchor(anchor)
        else:
            self.text.verticalScrollBar().setValue(0)
        self._mark_contents()
        self._update_nav()
        if changed:
            self.save_state()

    def render(self, keep_scroll: bool = False) -> None:
        part = self.manual.part(self.chapter)
        if part is None:
            return
        scroll = self.text.verticalScrollBar().value()
        style = self.html_style()
        self._column = self.column()
        document = self.text.document()
        font = QFont(style.font)
        font.setPixelSize(self.theme.px(READING_PX))
        document.setDefaultFont(font)
        sizes = manual_view.image_sizes(self.source.parent, self._column)
        manual_view.show_html(
            document, self.source.parent, style, manual_html.to_html(part.markdown, style, sizes)
        )
        # the cursor stays at the start, or qt scrolls to it when the layout changes
        self.text.moveCursor(QTextCursor.MoveOperation.Start)
        self._center()
        self._highlight()
        if keep_scroll:
            self.text.verticalScrollBar().setValue(scroll)

    def _mark_contents(self) -> None:
        """the open chapter stands out and shows its sections, the others are folded."""
        for index in range(self.contents.topLevelItemCount()):
            item = self.contents.topLevelItem(index)
            active = item.data(0, CHAPTER_ROLE) == self.chapter
            font = item.font(0)
            font.setBold(active)
            item.setFont(0, font)
            item.setExpanded(active)
            if active:
                self.contents.setCurrentItem(item)
                self.contents.scrollToItem(item)

    def _update_nav(self) -> None:
        index = self.manual.index(self.chapter)
        parts = self.manual.parts
        for button, other, text in (
            (self.previous, index - 1, tr("‹ PREVIOUS: {title}")),
            (self.next, index + 1, tr("NEXT: {title} ›")),
        ):
            visible = 0 <= other < len(parts)
            button.setVisible(visible)
            if visible:
                title = parts[other].title
                short = title if len(title) <= 42 else title[:40].rstrip() + "…"
                button.setText(text.format(title=short))
                button.setToolTip(title)

    def _step(self, step: int) -> None:
        index = self.manual.index(self.chapter) + step
        if 0 <= index < len(self.manual.parts):
            self.show_chapter(self.manual.parts[index].id)

    def _clicked(self, item: QTreeWidgetItem) -> None:
        chapter = item.data(0, CHAPTER_ROLE)
        anchor = item.data(0, ANCHOR_ROLE)
        if anchor:
            if chapter != self.chapter:
                self.show_chapter(chapter, anchor)
            else:
                self.text.scrollToAnchor(anchor)
            return
        self.show_chapter(chapter)

    def _link(self, url: QUrl) -> None:
        text = url.toString()
        if text.startswith(manual_html.IMAGE_SCHEME):
            path = self.source.parent / text[len(manual_html.IMAGE_SCHEME) :]
            viewer = ImageViewer(path, self.theme, self)
            viewer.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            viewer.show()
        elif url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)

    # search

    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def _apply_search(self) -> None:
        """chapters with hits in the contents, the hits marked in the text."""
        self.query = self.search.text().strip()
        hits = manual.search(self.manual, self.query) if self.query else {}
        for index in range(self.contents.topLevelItemCount()):
            item = self.contents.topLevelItem(index)
            part = self.manual.part(item.data(0, CHAPTER_ROLE))
            count = hits.get(part.id, 0)
            item.setHidden(bool(self.query) and not count)
            item.setText(0, f"{part.title}  ({count})" if count else part.title)
        if self.query:
            self.hits.setText(
                i18n.ntr(
                    len(hits), "{count} chapter with hits", "{count} chapters with hits"
                ).format(count=len(hits))
                if hits
                else tr("Nothing found. Try another word.")
            )
        self.hits.setVisible(bool(self.query))
        if self.query and hits and self.chapter not in hits:
            self.show_chapter(next(iter(hits)))
        else:
            self._highlight(scroll=True)

    def _highlight(self, scroll: bool = False) -> None:
        if not self.query:
            self.text.setExtraSelections([])
            return
        style = self.html_style()
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(style.highlight))
        fmt.setForeground(QColor(style.highlight_text))
        selections = []
        document = self.text.document()
        block = document.begin()
        while block.isValid():
            for start, end in find_all(block.text(), self.query):
                selection = QTextEdit.ExtraSelection()
                cursor = QTextCursor(document)
                cursor.setPosition(block.position() + start)
                cursor.setPosition(block.position() + end, QTextCursor.MoveMode.KeepAnchor)
                selection.cursor = cursor
                selection.format = fmt
                selections.append(selection)
            block = block.next()
        self.text.setExtraSelections(selections)
        if scroll and selections:
            cursor = QTextCursor(selections[0].cursor)
            cursor.clearSelection()
            self.text.setTextCursor(cursor)
            self.text.ensureCursorVisible()

    def hit_count(self) -> int:
        return len(self.text.extraSelections())

    # text size and the pdf

    def step_scale(self, step: int) -> None:
        current = self.ctx.settings().ui_scale
        wanted = config.DEFAULT_UI_SCALE if step == 0 else config.step_ui_scale(current, step)
        try:
            stored = actions.change_setting(self.ctx, "ui_scale", str(wanted))
        except NeonSeekError as exc:
            self.lines.add(exc.message, outcomes.ERROR)
            return
        if self.theme.percent() != stored:
            self.theme.apply(scale=stored)

    def open_pdf(self) -> None:
        """the pdf in the language of the window. built first when the package has none."""
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            path = manual_view.pdf_for(self.ctx.data_dir, i18n.language())
        except OSError as exc:
            self.lines.add(
                tr("Could not make the PDF of the manual ({error}).").format(error=exc),
                outcomes.ERROR,
            )
            return
        finally:
            QGuiApplication.restoreOverrideCursor()
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.lines.add(
                tr("No program for PDF files opened it. The PDF is here: {path}").format(path=path),
                outcomes.WARN,
            )

    # opening

    @classmethod
    def open_for(
        cls, ctx: actions.AppContext, theme: ThemeManager, owner: QWidget, chapter: str | None
    ) -> ManualWindow:
        """the help window of a window, at a chapter. a window that blocks the others (settings,
        the wizard) gets its own, so the help can be used next to it."""
        window = getattr(owner, "_manual_window", None)
        try:
            alive = window is not None and window.isVisible()
        except RuntimeError:
            alive = False
        if not alive:
            window = cls(ctx, theme, owner, chapter)
            window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            owner._manual_window = window
            window.show()
        elif chapter:
            window.show_chapter(chapter)
        window.raise_()
        window.activateWindow()
        return window
