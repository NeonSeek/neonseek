"""properties of tracks, read only: everything neonseek knows (D-125). several tracks get a
list on the left. the cli shows the same with neonseek track info."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from neonseek import actions, views
from neonseek.i18n import tr
from neonseek.pipeline import runner
from neonseek.ui.dialogs.base import Dialog
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets import spacing
from neonseek.ui.widgets.common import SectionLabel

ID_ROLE = Qt.ItemDataRole.UserRole


def _value_label(value: str, link_color: str) -> QLabel:
    if value.startswith("https://"):
        url = html.escape(value)
        label = QLabel(f'<a href="{url}" style="color: {link_color}">{url}</a>')
        label.setOpenExternalLinks(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    else:
        label = QLabel(value)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setWordWrap(True)
    return label


class PropertiesDialog(Dialog):
    def __init__(
        self, ctx: actions.AppContext, theme: ThemeManager, track_ids: list[int], parent=None
    ) -> None:
        super().__init__(theme, lambda: tr("Properties"), parent)
        self.ctx = ctx
        self.track_ids = list(dict.fromkeys(track_ids))
        self.resize_scaled(900 if len(self.track_ids) > 1 else 680, 640)
        row = QHBoxLayout()
        self.sizes.layout(row, margins=0, spacing=12)
        self.list = QListWidget()
        self.list.setVisible(len(self.track_ids) > 1)
        self.list.setProperty("role", "roomy")
        self.list.setMaximumWidth(theme.px(260))
        states = ctx.repo.track_states(self.track_ids)
        for tid in self.track_ids:
            if tid in states:
                item = QListWidgetItem(runner.track_label(states[tid]))
                item.setData(ID_ROLE, tid)
                self.list.addItem(item)
        self.list.currentItemChanged.connect(self._chosen)
        row.addWidget(self.list)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        row.addWidget(self.scroll, 1)
        self.body.addLayout(row, 1)
        buttons = spacing.button_row(self.sizes)
        close = QPushButton(tr("CLOSE"))
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        self.footer.addLayout(buttons)
        self.values: dict[str, str] = {}
        if self.list.count():
            self.list.setCurrentRow(0)
        # a new text size lays the page out again
        theme.changed.connect(self._again)

    def _again(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.show_track(item.data(ID_ROLE))

    def _chosen(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is not None:
            self.show_track(current.data(ID_ROLE))

    def show_track(self, track_id: int) -> None:
        info = actions.track_info(self.ctx, track_id)
        sections = views.track_info_sections(
            info.row, info.collections, info.file_exists, self.ctx.settings()
        )
        page = QWidget()
        page.setProperty("role", "surface")
        layout = QVBoxLayout(page)
        px = self.theme.px
        layout.setContentsMargins(px(spacing.WINDOW), px(16), px(spacing.WINDOW), px(16))
        # one grid for every section, so all values start at the same place
        grid = QGridLayout()
        grid.setHorizontalSpacing(px(spacing.LABEL))
        grid.setVerticalSpacing(px(8))
        grid.setColumnStretch(1, 1)
        link = self.theme.theme.colors["accent.secondary"]
        self.values = {}
        row = 0
        for title, rows in sections:
            header = SectionLabel(title)
            # a clear gap before a section, a smaller one to its first row (D-194)
            header.setContentsMargins(0, px(spacing.SECTION - 8) if row else 0, 0, px(2))
            grid.addWidget(header, row, 0, 1, 2)
            row += 1
            for label, value in rows:
                name = QLabel(label)
                name.setProperty("role", "muted")
                name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                grid.addWidget(name, row, 0)
                grid.addWidget(_value_label(value, link), row, 1)
                self.values.setdefault(label, value)
                row += 1
        layout.addLayout(grid)
        layout.addStretch(1)
        self.scroll.setWidget(page)
