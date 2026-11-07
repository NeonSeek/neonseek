"""left panel: collections in the groups spotify, links and csv (PLAN.md section 9)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from neonseek import views
from neonseek.db.repo import CollectionSummary
from neonseek.i18n import tr
from neonseek.sources.base import (
    KIND_CSV,
    KIND_SINGLES,
    KIND_SPOTIFY_ALBUM,
    KIND_SPOTIFY_LIKED,
)
from neonseek.ui.covers import CoverLoader
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets.common import ElidedLabel, LiveTexts, SectionLabel, Thumb, set_role
from neonseek.ui.widgets.spacing import Hint

# pixel sizes at 100 %, scaled with the text size setting
ROW_HEIGHT = 40
# a group title with room above it, so groups stand apart (D-194)
HEADER_HEIGHT = 40
HINT_HEIGHT = 52
THUMB = 28
COUNTER_HEIGHT = 20
LOCK = 14
ID_ROLE = Qt.ItemDataRole.UserRole
GROUP_ROLE = Qt.ItemDataRole.UserRole + 1


def kind_icon(kind: str) -> str:
    return {
        KIND_SPOTIFY_LIKED: "heart",
        KIND_SPOTIFY_ALBUM: "album",
        KIND_SINGLES: "single",
        KIND_CSV: "csv",
    }.get(kind, "playlist")


class CollectionRow(QWidget):
    toggled = Signal(int, bool)

    def __init__(self, summary: CollectionSummary, theme: ThemeManager, covers: CoverLoader):
        super().__init__()
        self.theme = theme
        self.collection_id = summary.id
        layout = QHBoxLayout(self)
        px = theme.px
        layout.setContentsMargins(px(12), 0, px(8), 0)
        layout.setSpacing(px(8))
        self.check = QCheckBox()
        LiveTexts(theme, self).tip(
            self.check, lambda: tr("Tick it for DOWNLOAD CHECKED and for SYNC.")
        )
        self.check.toggled.connect(lambda on: self.toggled.emit(self.collection_id, on))
        self.thumb = Thumb(THUMB, theme, covers)
        self.name = ElidedLabel()
        self.lock = QLabel()
        self.lock.setFixedSize(px(LOCK), px(LOCK))
        self.counter = QLabel()
        self.counter.setProperty("role", "counter")
        self.counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.counter.setFixedHeight(px(COUNTER_HEIGHT))
        layout.addWidget(self.check)
        layout.addWidget(self.thumb)
        layout.addWidget(self.name, 1)
        layout.addWidget(self.lock)
        layout.addWidget(self.counter, 0, Qt.AlignmentFlag.AlignVCenter)
        self.update_summary(summary)

    def update_summary(self, summary: CollectionSummary) -> None:
        self.summary = summary
        self.check.blockSignals(True)
        self.check.setChecked(summary.selected)
        self.check.blockSignals(False)
        self.thumb.set_cover(summary.image_url, kind_icon(summary.kind))
        self.name.setText(summary.name)
        self.counter.setText(views.collection_counter(summary))
        self.counter.setToolTip(views.counter_hint(summary))
        hint = views.locked_hint(summary)
        set_role(self.name, "locked" if hint else "")
        self.lock.setVisible(bool(hint))
        self.setToolTip(hint or "")
        self.name.setToolTip(hint or "")
        self.lock.setPixmap(
            self.theme.pixmap("lock", "text.muted", self.theme.px(LOCK), self.devicePixelRatioF())
        )


class GroupHeader(QWidget):
    """title of a group. the same widget stays on top of the list while it scrolls."""

    def __init__(self, theme: ThemeManager, group: views.CollectionGroup | None = None) -> None:
        super().__init__()
        self.key = ""
        layout = QHBoxLayout(self)
        px = theme.px
        layout.setContentsMargins(px(12), px(12), px(8), px(2))
        layout.setSpacing(px(6))
        self.label = SectionLabel("")
        self.count = QLabel()
        self.count.setProperty("role", "muted")
        layout.addWidget(self.label)
        layout.addWidget(self.count)
        layout.addStretch(1)
        # only the spotify group: log in, or sync the account
        self.action = QPushButton()
        self.action.setProperty("flat", True)
        self.action.setVisible(False)
        layout.addWidget(self.action)
        if group is not None:
            self.show_group(group.key, group.title, len(group.collections))

    def show_group(self, key: str, title: str, count: int) -> None:
        self.key = key
        self.label.setText(title)
        self.count.setText(str(count))


class GroupHint(QWidget):
    def __init__(self, theme: ThemeManager, text: str) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(theme.px(12), theme.px(2), theme.px(10), theme.px(12))
        layout.addWidget(Hint(text))

    def height_for(self, width: int) -> int:
        """as tall as its wrapped text at this width: a longer translation takes more lines."""
        return self.layout().totalHeightForWidth(width)


class CollectionsPanel(QFrame):
    """emits the open collection and checkbox changes. the window talks to core."""

    current_changed = Signal(object)  # collection id or none
    selection_toggled = Signal(int, bool)
    menu_requested = Signal(int, QPoint)  # collection id, global position
    spotify_action = Signal()

    def __init__(self, theme: ThemeManager, covers: CoverLoader, parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("role", "panel")
        self.theme = theme
        self.covers = covers
        self.summaries: list[CollectionSummary] = []
        self._rows: dict[int, tuple[QListWidgetItem, CollectionRow]] = {}
        self._headers: dict[str, tuple[QListWidgetItem, GroupHeader]] = {}
        # hints of empty groups, their height follows the panel width
        self._hints: list[tuple[QListWidgetItem, GroupHint]] = []
        self._groups: list[views.CollectionGroup] = []
        self._spotify_text = ("", "")
        self._busy = False
        self._built = False
        self._scale = theme.scale
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.top = QWidget()
        self.top.setProperty("role", "surface")
        self.top_layout = QHBoxLayout(self.top)
        texts = LiveTexts(theme, self)
        self.top_layout.addWidget(texts.text(SectionLabel(""), lambda: tr("COLLECTIONS")))
        self.top_layout.addStretch(1)
        self.add_button = texts.text(QPushButton(), lambda: tr("+ ADD"))
        self.top_layout.addWidget(self.add_button)
        layout.addWidget(self.top)

        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # rows follow the panel width when the splitter moves
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.currentItemChanged.connect(self._current_changed)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.verticalScrollBar().valueChanged.connect(self._update_sticky)
        # the list gets narrower when its scroll bar shows, the hints follow its width
        self.list.viewport().installEventFilter(self)
        layout.addWidget(self.list, 1)

        # the title of the group at the top stays visible while the list scrolls (D-116)
        self.sticky = GroupHeader(theme)
        self.sticky.setParent(self.list)
        self.sticky.setProperty("role", "surface")
        self.sticky.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.sticky.action.clicked.connect(self.spotify_action)
        self.sticky.hide()

        theme.changed.connect(self._rescale)
        theme.language_changed.connect(self._retranslate)
        self._rescale()

    def _retranslate(self) -> None:
        # group titles, hints and counters come from core in the new language (D-163)
        if self._built:
            self.set_collections(self.summaries)

    # sizes

    def _rescale(self) -> None:
        px = self.theme.px
        self.top_layout.setContentsMargins(px(12), px(12), px(8), px(8))
        self.top_layout.setSpacing(px(6))
        # a new theme only repaints, a new text size needs new row heights
        if self._built and self.theme.scale != self._scale:
            self.set_collections(self.summaries)
        self._scale = self.theme.scale

    def resizeEvent(self, event) -> None:  # noqa: N802 - qt name
        super().resizeEvent(event)
        self._fit_hints()
        self._update_sticky()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - qt name
        if watched is self.list.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_hints()
        return super().eventFilter(watched, event)

    def _fit_hints(self) -> None:
        width = self.list.viewport().width()
        for item, hint in self._hints:
            height = max(self.theme.px(HINT_HEIGHT), hint.height_for(width))
            if item.sizeHint().height() != height:
                item.setSizeHint(QSize(0, height))

    # content

    def current_id(self) -> int | None:
        item = self.list.currentItem()
        return item.data(ID_ROLE) if item is not None else None

    def set_collections(self, summaries: list[CollectionSummary]) -> None:
        """rebuilds the list and keeps the open collection open."""
        keep = self.current_id()
        scroll = self.list.verticalScrollBar().value()
        self.summaries = list(summaries)
        self._built = True
        px = self.theme.px
        self.list.blockSignals(True)
        self.list.clear()
        self._hints.clear()
        self._rows.clear()
        self._headers.clear()
        self._groups = views.collection_groups(summaries)
        for group in self._groups:
            header_item = QListWidgetItem()
            header_item.setFlags(Qt.ItemFlag.NoItemFlags)
            header_item.setSizeHint(QSize(0, px(HEADER_HEIGHT)))
            header_item.setData(GROUP_ROLE, group.key)
            self.list.addItem(header_item)
            header = GroupHeader(self.theme, group)
            if group.key == views.GROUP_SPOTIFY:
                header.action.clicked.connect(self.spotify_action)
            self.list.setItemWidget(header_item, header)
            self._headers[group.key] = (header_item, header)
            if not group.collections:
                hint_item = QListWidgetItem()
                hint_item.setFlags(Qt.ItemFlag.NoItemFlags)
                hint_item.setSizeHint(QSize(0, px(HINT_HEIGHT)))
                hint_item.setData(GROUP_ROLE, group.key)
                self.list.addItem(hint_item)
                hint = GroupHint(self.theme, views.empty_group_hint(group.key))
                self.list.setItemWidget(hint_item, hint)
                self._hints.append((hint_item, hint))
            for summary in group.collections:
                item = QListWidgetItem()
                item.setData(ID_ROLE, summary.id)
                item.setData(GROUP_ROLE, group.key)
                item.setSizeHint(QSize(0, px(ROW_HEIGHT)))
                row = CollectionRow(summary, self.theme, self.covers)
                row.toggled.connect(self.selection_toggled)
                self.list.addItem(item)
                self.list.setItemWidget(item, row)
                self._rows[summary.id] = (item, row)
        if keep in self._rows:
            self.list.setCurrentItem(self._rows[keep][0])
        self.list.verticalScrollBar().setValue(scroll)
        self.list.blockSignals(False)
        self._paint_spotify()
        self._fit_hints()
        if keep not in self._rows and keep is not None:
            self.current_changed.emit(None)
        QTimer.singleShot(0, self._update_sticky)

    def update_collections(self, summaries: list[CollectionSummary]) -> None:
        """new counters and checkboxes without rebuilding, when the set is the same."""
        if not self._built or {s.id for s in summaries} != set(self._rows):
            self.set_collections(summaries)
            return
        self.summaries = list(summaries)
        for summary in summaries:
            self._rows[summary.id][1].update_summary(summary)

    def open_collection(self, collection_id: int) -> None:
        if collection_id in self._rows:
            self.list.setCurrentItem(self._rows[collection_id][0])
            self.reveal_current()

    def reveal_current(self) -> None:
        """shows the open collection, from the top of the list when it fits there."""
        self.list.scrollToTop()
        item = self.list.currentItem()
        if item is not None:
            self.list.scrollToItem(item)
        self._update_sticky()

    # the spotify button and the sticky title

    def set_spotify_action(self, text: str, tooltip: str) -> None:
        """the button on the spotify group: log in without spotify, else sync the account."""
        self._spotify_text = (text, tooltip)
        self._paint_spotify()

    def _paint_spotify(self) -> None:
        text, tooltip = self._spotify_text
        headers = [h for _, h in self._headers.values() if h.key == views.GROUP_SPOTIFY]
        if self.sticky.key == views.GROUP_SPOTIFY:
            headers.append(self.sticky)
        for header in headers:
            header.action.setText(text)
            header.action.setToolTip(tooltip)
            header.action.setVisible(bool(text))
            header.action.setEnabled(not self._busy)
        if self.sticky.key != views.GROUP_SPOTIFY:
            self.sticky.action.setVisible(False)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._paint_spotify()

    def spotify_header(self) -> GroupHeader | None:
        found = self._headers.get(views.GROUP_SPOTIFY)
        return found[1] if found else None

    def _update_sticky(self) -> None:
        viewport = self.list.viewport()
        top = self.list.itemAt(QPoint(2, 1))
        key = top.data(GROUP_ROLE) if top is not None else None
        header = self._headers.get(key) if key else None
        # the real title is fully in view: no copy needed
        if header is None or self.list.visualItemRect(header[0]).top() >= 0:
            self.sticky.hide()
            return
        group = next(g for g in self._groups if g.key == key)
        self.sticky.show_group(group.key, group.title, len(group.collections))
        self._paint_spotify()
        self.sticky.setGeometry(
            viewport.x(), viewport.y(), viewport.width(), self.theme.px(HEADER_HEIGHT)
        )
        self.sticky.show()
        self.sticky.raise_()

    # signals

    def _menu(self, pos: QPoint) -> None:
        item = self.list.itemAt(pos)
        if item is None or item.data(ID_ROLE) is None:
            return
        self.list.setCurrentItem(item)
        self.menu_requested.emit(item.data(ID_ROLE), self.list.viewport().mapToGlobal(pos))

    def _current_changed(self, current: QListWidgetItem | None, _previous) -> None:
        self.current_changed.emit(current.data(ID_ROLE) if current is not None else None)
