"""report: not found and failed tracks of every collection, retry and csv export (PLAN 9).

retry selected downloads the tracks at once (D-131) and the rows have the track menu (D-125).
the window does both, the report only asks for them. it does not block the main window, so
the download can be followed in the log.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from neonseek import actions, outcomes, views
from neonseek.errors import NeonSeekError
from neonseek.i18n import tr
from neonseek.ui.dialogs.base import Dialog, LineView, plain_table
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets import spacing
from neonseek.ui.widgets.track_table import STATE_LOOK

ID_ROLE = Qt.ItemDataRole.UserRole


class ReportDialog(Dialog):
    download_requested = Signal(list)  # track ids, the window downloads them
    help_requested = Signal()  # the manual at the chapter about the report (D-182)
    menu_requested = Signal(list, QPoint)  # track ids, global position

    def __init__(self, ctx: actions.AppContext, theme: ThemeManager, parent=None) -> None:
        super().__init__(theme, lambda: tr("Report"), parent)
        self.ctx = ctx
        self.downloading = False
        self.resize_scaled(1040, 620)
        texts = self.texts
        hint = texts.text(
            spacing.Hint(),
            lambda: tr(
                "Tracks not found and failed, from every collection. Tracks waiting for WAV/AIFF "
                "are not lost: the next download looks for them in those formats. RETRY SELECTED "
                "downloads the selected tracks now, starting again from FLAC. Right-click a track "
                "for more."
            ),
        )
        self.body.addWidget(hint)
        self.table = plain_table(QTableWidget(0, 5))
        texts.call(
            lambda: self.table.setHorizontalHeaderLabels(
                [tr("TRACK"), tr("ALBUM"), tr("STATUS"), tr("REASON"), tr("COLLECTIONS")]
            )
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._selection)
        self.body.addWidget(self.table, 1)
        self.lines = LineView(theme, max_lines=4)
        self.body.addWidget(self.lines)

        row = spacing.button_row(self.sizes, stretch_first=False)
        self.count = QLabel()
        self.count.setProperty("role", "muted")
        row.addWidget(self.count, 1)
        self.export = texts.text(QPushButton(), lambda: tr("EXPORT CSV"))
        self.export.clicked.connect(self._export)
        self.retry = texts.text(QPushButton(), lambda: tr("RETRY SELECTED"))
        self.retry.setProperty("primary", True)
        self.retry.clicked.connect(self._retry)
        help_button = spacing.HelpButton(
            theme, lambda: tr("Opens the chapter of the manual about the report"), small=False
        )
        help_button.clicked.connect(self.help_requested)
        close = texts.text(QPushButton(), lambda: tr("CLOSE"))
        close.clicked.connect(self.accept)
        row.addWidget(help_button)
        row.addWidget(self.export)
        row.addWidget(self.retry)
        row.addWidget(close)
        self.footer.addLayout(row)
        self.rows: list[actions.ReportRow] = []
        self.load()
        # states, reasons and counts come from core in the new language (D-163)
        theme.language_changed.connect(self.load)

    def load(self) -> None:
        keep = set(self.selected_ids())
        self.rows = actions.report(self.ctx)
        settings = self.ctx.settings()
        self.table.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            state, label = views.report_state(row, settings)
            icon_name, token = STATE_LOOK.get(state, ("state-failed", "state.error"))
            cells = [row.label, row.album or "", label, row.reason,
                     ", ".join(name for _, name in row.collections)]  # fmt: skip
            for column, value in enumerate(cells):
                item = QTableWidgetItem(value)
                item.setData(ID_ROLE, row.track_id)
                item.setToolTip(value)
                if column == 2:
                    item.setIcon(self.theme.icon(icon_name, token))
                    # what the state means and what to do next (D-129)
                    item.setToolTip(
                        views.state_hint(
                            state,
                            row.stage,
                            attempts=row.attempts,
                            max_attempts=settings.max_attempts,
                        )  # fmt: skip
                    )
                self.table.setItem(index, column, item)
        self.table.clearSelection()
        for index, row in enumerate(self.rows):
            if row.track_id in keep:
                self.table.selectRow(index)
        self.count.setText(outcomes.report_count_text(len(self.rows)))
        self.export.setEnabled(bool(self.rows))
        self._selection()

    def selected_ids(self) -> list[int]:
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        return [self.rows[r].track_id for r in sorted(rows) if r < len(self.rows)]

    def set_downloading(self, downloading: bool) -> None:
        """another download runs: retry selected waits for it, with a word why."""
        self.downloading = downloading
        self._selection()

    def _selection(self) -> None:
        chosen = bool(self.selected_ids())
        self.retry.setEnabled(chosen and not self.downloading)
        if self.downloading:
            self.retry.setToolTip(views.running_reason())
        elif not chosen:
            self.retry.setToolTip(tr("Select the tracks to download again first."))
        else:
            self.retry.setToolTip(
                tr(
                    "Downloads the selected tracks now, starting again from FLAC. A summary "
                    "comes first."
                )  # fmt: skip
            )

    def _retry(self) -> None:
        ids = self.selected_ids()
        if ids and not self.downloading:
            self.download_requested.emit(ids)

    def _menu(self, pos: QPoint) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        if not self.table.selectionModel().isRowSelected(index.row(), QModelIndex()):
            self.table.selectRow(index.row())
        self.menu_requested.emit(self.selected_ids(), self.table.viewport().mapToGlobal(pos))

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Save the report"), "neonseek-report.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            actions.export_report(self.rows, path)
        except (OSError, NeonSeekError) as exc:
            self.lines.add(
                tr("Could not save {path} ({error}). Choose another place.").format(
                    path=path, error=getattr(exc, "strerror", None) or exc
                ),
                outcomes.ERROR,
            )
            return
        self.lines.add_lines([outcomes.saved_report_line(len(self.rows), path)])
