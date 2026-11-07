"""track table: cover, number, title, artist, album, length, state chip, file (PLAN.md 9)."""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPoint,
    QRect,
    QSize,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QWidget,
)

from neonseek import views
from neonseek.db import repo as db
from neonseek.i18n import tr
from neonseek.ui.covers import CoverLoader
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets.common import draw_thumb

# pixel sizes at 100 %, scaled with the text size setting
ROW_HEIGHT = 36
THUMB = 28
ROW_ROLE = Qt.ItemDataRole.UserRole

COVER, NUMBER, TITLE, ARTIST, ALBUM, LENGTH, STATE, FILE = range(8)


def column_titles() -> list[str]:
    return ["", "#", tr("TITLE"), tr("ARTIST"), tr("ALBUM"), tr("LENGTH"), tr("STATUS"),
            tr("FILE")]  # fmt: skip


# state -> icon and the theme token of its chip (never color alone, PLAN.md section 9)
STATE_LOOK = {
    db.PENDING: ("state-pending", "state.queued"),
    db.SEARCHING: ("state-searching", "accent.primary"),
    db.DOWNLOADING: ("state-downloading", "accent.primary"),
    db.DONE: ("state-done", "state.success"),
    db.NOT_FOUND: ("state-not-found", "state.warning"),
    db.FAILED: ("state-failed", "state.error"),
    views.WAITING_FALLBACK: ("state-waiting", "accent.secondary"),
    db.EXCLUDED: ("state-excluded", "text.muted"),
}


class TrackModel(QAbstractTableModel):
    def __init__(self, theme: ThemeManager, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.rows: list[views.TrackRow] = []
        self._by_cover: dict[str, list[int]] = {}

    def set_rows(self, rows: list[views.TrackRow]) -> None:
        self.beginResetModel()
        self.rows = rows
        self._by_cover = {}
        for index, row in enumerate(rows):
            if row.cover_url:
                self._by_cover.setdefault(row.cover_url, []).append(index)
        self.endResetModel()

    def update_rows(self, fresh: dict[int, views.TrackRow]) -> None:
        """new values for some tracks, during a download."""
        for index, row in enumerate(self.rows):
            if row.track_id in fresh:
                self.rows[index] = fresh[row.track_id]
                self.dataChanged.emit(self.index(index, 0), self.index(index, FILE))

    def track_numbers(self) -> dict[int, int]:
        return {row.track_id: row.number for row in self.rows}

    def cover_ready(self, url: str) -> None:
        for index in self._by_cover.get(url, []):
            cell = self.index(index, COVER)
            self.dataChanged.emit(cell, cell, [Qt.ItemDataRole.DecorationRole])

    # qt model

    def rowCount(self, parent=None) -> int:  # noqa: N802 - qt name
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None) -> int:  # noqa: N802 - qt name
        return 0 if parent is not None and parent.isValid() else len(column_titles())

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return column_titles()[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        column = index.column()
        if role == ROW_ROLE:
            return row
        if role == Qt.ItemDataRole.DisplayRole:
            return {
                NUMBER: str(row.number),
                TITLE: row.title,
                ARTIST: row.artists,
                ALBUM: row.album,
                LENGTH: row.length_label,
                STATE: row.state_label,
                FILE: row.file_label,
            }.get(column)
        if role == Qt.ItemDataRole.ToolTipRole:
            if column == STATE:
                # what the state means and what to do next (D-129)
                return "\n".join(part for part in (row.state_label, row.reason, row.hint) if part)
            if column == FILE and row.file_path:
                return row.file_path
            if column in (TITLE, ARTIST, ALBUM):
                return self.data(index)
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (NUMBER, LENGTH):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and column in (NUMBER, ALBUM, LENGTH, FILE):
            return self.theme.color("text.muted")
        return None


class TrackFilter(QSortFilterProxyModel):
    """state filter and search words, both decided by core (views)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.state_filter = views.FILTER_ALL
        self.query = ""

    def set_state_filter(self, key: str) -> None:
        self.beginFilterChange()
        self.state_filter = key
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def set_query(self, query: str) -> None:
        self.beginFilterChange()
        self.query = query
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        row = self.sourceModel().rows[source_row]
        return views.state_matches(row.state, self.state_filter) and views.track_matches(
            row, self.query
        )


class RowDelegate(QStyledItemDelegate):
    """paints the hovered row as a whole. the style sheet paints selection (D-117)."""

    def __init__(self, table: TrackTable) -> None:
        super().__init__(table)
        self.table = table
        self.theme = table.theme

    def _hover(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        selected = option.state & QStyle.StateFlag.State_Selected
        if index.row() == self.table.hover_row and not selected:
            painter.fillRect(option.rect, self.theme.color("bg.elevated"))

    def background(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        """hover and selection for cells this delegate paints itself."""
        self._hover(painter, option, index)
        opt = QStyleOptionViewItem(option)
        opt.state &= ~QStyle.StateFlag.State_MouseOver
        if opt.widget is not None:
            opt.widget.style().drawPrimitive(
                QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, opt.widget
            )

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        self._hover(painter, option, index)
        opt = QStyleOptionViewItem(option)
        # hover is the whole row, never one cell
        opt.state &= ~QStyle.StateFlag.State_MouseOver
        super().paint(painter, opt, index)


class CoverDelegate(RowDelegate):
    def __init__(self, table: TrackTable, covers: CoverLoader) -> None:
        super().__init__(table)
        self.covers = covers

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        self.background(painter, option, index)
        row: views.TrackRow = index.data(ROW_ROLE)
        rect = option.rect
        side = min(self.theme.px(THUMB), rect.height() - 6)
        target = QRect(rect.x() + (rect.width() - side) // 2,
                       rect.y() + (rect.height() - side) // 2, side, side)  # fmt: skip
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        draw_thumb(painter, target, self.theme, self.covers, row.cover_url)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - qt name
        return QSize(self.theme.px(THUMB + 12), self.theme.px(ROW_HEIGHT))


class StateChipDelegate(RowDelegate):
    """icon and text in a chip: border and icon in the state color, text in text.primary,
    background bg.surface (PLAN.md section 9)."""

    def _parts(self) -> tuple[int, int, int, int, int]:
        """icon, padding, gap, left offset and right margin of a chip at this text size."""
        px = self.theme.px
        return px(12), px(6), px(5), px(4), px(4)

    def chip_width(self, metrics, text: str, cell_width: int) -> int:
        icon_size, pad, gap, left, right = self._parts()
        full = pad + icon_size + gap + metrics.horizontalAdvance(text) + pad
        return min(cell_width - left - right, full)

    def text_room(self, metrics, text: str, cell_width: int) -> int:
        """the width the text gets inside its chip in a cell this wide."""
        icon_size, pad, gap, _, _ = self._parts()
        return self.chip_width(metrics, text, cell_width) - pad - icon_size - gap - pad + 2

    def cell_width(self, metrics, text: str) -> int:
        """the narrowest cell that shows the whole text in its chip."""
        icon_size, pad, gap, left, right = self._parts()
        return left + pad + icon_size + gap + metrics.horizontalAdvance(text) + pad + right

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        self.background(painter, option, index)
        row: views.TrackRow = index.data(ROW_ROLE)
        icon_name, token = STATE_LOOK.get(row.state, ("state-pending", "state.queued"))
        metrics = option.fontMetrics
        text = row.state_label
        px = self.theme.px
        icon_size, pad, gap = px(12), px(6), px(5)
        width = self.chip_width(metrics, text, option.rect.width())
        height = min(metrics.height() + px(6), option.rect.height() - 2)
        chip = QRect(
            option.rect.x() + px(4),
            option.rect.y() + (option.rect.height() - height) // 2,
            width,
            height,
        )
        painter.save()
        painter.fillRect(chip, self.theme.color("bg.surface"))
        pen = QPen(self.theme.color(token))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawRect(chip.adjusted(0, 0, -1, -1))
        dpr = painter.device().devicePixelRatioF()
        icon = self.theme.pixmap(icon_name, token, icon_size, dpr)
        icon_rect = QRect(chip.x() + pad, chip.y() + (height - icon_size) // 2, icon_size,
                          icon_size)  # fmt: skip
        painter.drawPixmap(icon_rect, icon)
        painter.setPen(self.theme.color("text.primary"))
        text_rect = chip.adjusted(pad + icon_size + gap, 0, -pad + 2, 0)
        align = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(text_rect, int(align), elided)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - qt name
        row: views.TrackRow = index.data(ROW_ROLE)
        width = self.cell_width(option.fontMetrics, row.state_label)
        return QSize(width, self.theme.px(ROW_HEIGHT))


# column widths at 100 %, title, artist and album share the rest. the status column is
# measured from the longest status label, so a longer translation still fits
FIXED_WIDTHS = {COVER: THUMB + 12, NUMBER: 40, LENGTH: 66, FILE: 112}
# title, artist and album share what is left in these parts, never below the minimum. a
# window too narrow for the minimums scrolls sideways instead of cutting every text (D-121)
SHARED = {TITLE: (4, 150), ARTIST: (3, 110), ALBUM: (3, 110)}


class TrackTable(QTableView):
    menu_requested = Signal(list, QPoint)  # track ids, global position

    def __init__(self, theme: ThemeManager, covers: CoverLoader, parent: QWidget | None = None):
        super().__init__(parent)
        self.theme = theme
        self.hover_row = -1
        self.model_ = TrackModel(theme, self)
        self.proxy = TrackFilter(self)
        self.proxy.setSourceModel(self.model_)
        self.setModel(self.proxy)
        self.setItemDelegate(RowDelegate(self))
        self.setItemDelegateForColumn(COVER, CoverDelegate(self, covers))
        self.state_delegate = StateChipDelegate(self)
        self.setItemDelegateForColumn(STATE, self.state_delegate)
        covers.ready.connect(self.model_.cover_ready)

        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setMouseTracking(True)
        # a click selects the whole row (D-117)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStretchLastSection(False)
        for column, mode in (
            (COVER, QHeaderView.ResizeMode.Fixed),
            (NUMBER, QHeaderView.ResizeMode.Fixed),
            (TITLE, QHeaderView.ResizeMode.Interactive),
            (ARTIST, QHeaderView.ResizeMode.Interactive),
            (ALBUM, QHeaderView.ResizeMode.Interactive),
            (LENGTH, QHeaderView.ResizeMode.Fixed),
            (STATE, QHeaderView.ResizeMode.Interactive),
            (FILE, QHeaderView.ResizeMode.Interactive),
        ):
            header.setSectionResizeMode(column, mode)
        # the track menu works on the selected rows (D-125)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        theme.changed.connect(self._rescale)
        theme.language_changed.connect(self._retranslate)
        self._rescale()

    def _retranslate(self) -> None:
        # column titles in the new language, the status column fits its new labels (D-157)
        self.model_.headerDataChanged.emit(
            Qt.Orientation.Horizontal, 0, self.model_.columnCount() - 1
        )
        self._rescale()

    def _menu(self, pos: QPoint) -> None:
        index = self.indexAt(pos)
        if not index.isValid():
            return
        # a right click outside the selection selects that row, like explorer does
        if not self.selectionModel().isRowSelected(index.row(), QModelIndex()):
            self.selectRow(index.row())
        self.menu_requested.emit(self.selected_track_ids(), self.viewport().mapToGlobal(pos))

    def selected_track_ids(self) -> list[int]:
        rows = sorted({index.row() for index in self.selectionModel().selectedRows()})
        return [self.proxy.index(row, 0).data(ROW_ROLE).track_id for row in rows]

    def _rescale(self) -> None:
        px = self.theme.px
        self.verticalHeader().setDefaultSectionSize(px(ROW_HEIGHT))
        self.horizontalHeader().setMinimumSectionSize(px(28))
        for column, width in FIXED_WIDTHS.items():
            self.setColumnWidth(column, px(width))
        self.setColumnWidth(STATE, self.state_width())
        self._share_width()
        self.viewport().update()

    def state_width(self) -> int:
        """the status column fits the longest status label at this text size. measured with
        the table font and the theme font, the style sheet sets both, the wider one counts."""
        fonts = (QFontMetrics(self.font()), QFontMetrics(self.theme.font()))
        return max(
            self.state_delegate.cell_width(metrics, label)
            for metrics in fonts
            for label in views.all_state_labels()
        )

    def _share_width(self) -> None:
        px = self.theme.px
        fixed = sum(self.columnWidth(column) for column in (*FIXED_WIDTHS, STATE))
        free = self.viewport().width() - fixed
        parts = sum(part for part, _ in SHARED.values())
        for column, (part, minimum) in SHARED.items():
            self.setColumnWidth(column, max(px(minimum), free * part // parts))

    def resizeEvent(self, event) -> None:  # noqa: N802 - qt name
        super().resizeEvent(event)
        self._share_width()

    def set_rows(self, rows: list[views.TrackRow]) -> None:
        self.hover_row = -1
        self.model_.set_rows(rows)

    def visible_count(self) -> int:
        return self.proxy.rowCount()

    def height_for_rows(self, rows: int) -> int:
        """the height that shows this many whole rows, with the column titles and room for the
        sideways scroll bar of a narrow window."""
        titles = self.horizontalHeader().sizeHint().height()
        sideways = self.horizontalScrollBar().sizeHint().height()
        body = rows * self.verticalHeader().defaultSectionSize()
        return titles + body + sideways + 2 * self.frameWidth()

    def whole_rows_shown(self) -> int:
        """rows of the open list that are fully in view."""
        bottom = self.viewport().rect().bottom()
        shown = 0
        for row in range(self.proxy.rowCount()):
            rect = self.visualRect(self.proxy.index(row, 0))
            if rect.top() >= 0 and rect.bottom() <= bottom:
                shown += 1
        return shown

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - qt name
        row = self.rowAt(event.position().toPoint().y())
        if row != self.hover_row:
            self.hover_row = row
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - qt name
        self.hover_row = -1
        self.viewport().update()
        super().leaveEvent(event)
