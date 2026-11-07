"""csv import: the first 20 rows after mapping, columns to change, length unit, name (PLAN 9)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from neonseek import actions, outcomes, views
from neonseek.i18n import tr
from neonseek.sources import csv_file
from neonseek.ui.dialogs.base import Dialog, LineView, plain_table
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets import spacing
from neonseek.ui.widgets.common import SectionLabel
from neonseek.ui.workers import Worker


def field_label(name: str) -> str:
    return {
        "title": tr("TITLE"),
        "artist": tr("ARTIST"),
        "album": tr("ALBUM"),
        "length": tr("LENGTH"),
        "isrc": tr("ISRC"),
        "spotify_id": tr("SPOTIFY ID"),
    }[name]


def unit_label(unit: str) -> str:
    return {
        actions.AUTO_UNIT: tr("detect"),
        "s": tr("seconds"),
        "ms": tr("milliseconds"),
        "clock": tr("m:ss or h:mm:ss"),
    }[unit]


class CsvImportDialog(Dialog):
    imported = Signal(object)  # CsvImportOutcome

    def __init__(
        self,
        ctx: actions.AppContext,
        theme: ThemeManager,
        path: Path,
        collection_id: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(theme, lambda: tr("Import CSV"), parent)
        self.ctx = ctx
        self.path = Path(path)
        self.worker: Worker | None = None
        # the preview runs in a worker, a change during it asks for one more (D-111)
        self.preview_worker: Worker | None = None
        self.preview_again = False
        self.resize_scaled(980, 640)
        defaults = actions.csv_import_defaults(ctx, self.path, collection_id)
        self.collection_id = defaults.collection_id
        # only fields the user picked by hand, the rest is detected (D-011)
        self.overrides: dict[str, str | None] = dict(defaults.overrides)
        self.stored = dict(defaults.overrides)
        self.auto: dict[str, str] = {}
        self.header: list[str] = []

        self.file = QLabel(str(self.path))
        self.file.setProperty("role", "muted")
        self.file.setWordWrap(True)
        self.body.addWidget(self.file)
        if self.collection_id is not None:
            note = QLabel(
                tr("This updates collection {id}. Its downloaded tracks stay.").format(
                    id=self.collection_id
                )
            )
            note.setProperty("role", "muted")
            self.body.addWidget(note)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.combos: dict[str, QComboBox] = {}
        for index, name in enumerate(csv_file.FIELDS):
            combo = QComboBox()
            combo.currentIndexChanged.connect(lambda _, n=name: self._field_changed(n))
            self.combos[name] = combo
            grid.addWidget(SectionLabel(field_label(name)), index // 3 * 2, index % 3)
            grid.addWidget(combo, index // 3 * 2 + 1, index % 3)
        self.unit = QComboBox()
        for unit in (actions.AUTO_UNIT, *csv_file.LENGTH_UNITS):
            self.unit.addItem(unit_label(unit), unit)
        if defaults.length_unit:
            self.unit.setCurrentIndex(self.unit.findData(defaults.length_unit))
        self.unit.currentIndexChanged.connect(self._refresh)
        grid.addWidget(SectionLabel(tr("LENGTH UNIT")), 4, 0)
        grid.addWidget(self.unit, 5, 0)
        self.name = QLineEdit(defaults.name)
        grid.addWidget(SectionLabel(tr("COLLECTION NAME")), 4, 1)
        grid.addWidget(self.name, 5, 1, 1, 2)
        self.body.addLayout(grid)

        self.table = plain_table(QTableWidget(0, 6))
        self.table.setHorizontalHeaderLabels(
            ["#", tr("TITLE"), tr("ARTISTS"), tr("ALBUM"), tr("LENGTH"), tr("ISRC / SPOTIFY")]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 40)
        self.body.addWidget(self.table, 1)
        self.lines = LineView(theme, max_lines=5)
        self.body.addWidget(self.lines)

        buttons = spacing.button_row(self.sizes)
        self.cancel = QPushButton(tr("CANCEL"))
        self.cancel.clicked.connect(self.reject)
        self.start = QPushButton(tr("IMPORT"))
        self.start.setProperty("primary", True)
        self.start.clicked.connect(self._import)
        buttons.addWidget(self.cancel)
        buttons.addWidget(self.start)
        self.footer.addLayout(buttons)
        self._refresh()

    # mapping

    def _fill_combos(self, header: list[str], columns: dict[str, str]) -> None:
        for name, combo in self.combos.items():
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(tr("(not used)"), None)
            for column in header:
                combo.addItem(column, column)
            chosen = columns.get(name)
            combo.setCurrentIndex(combo.findData(chosen) if chosen else 0)
            combo.blockSignals(False)

    def _field_changed(self, name: str) -> None:
        chosen = self.combos[name].currentData()
        if chosen == self.auto.get(name) and name not in self.stored:
            self.overrides.pop(name, None)
        else:
            self.overrides[name] = chosen
        self._refresh()

    def _unit(self) -> str:
        return self.unit.currentData()

    def _refresh(self) -> None:
        self.start.setEnabled(False)
        if self.preview_worker is not None:
            self.preview_again = True
            return
        self.lines.clear_lines()
        self.lines.add(tr("Reading the file..."))
        path, overrides, unit = self.path, dict(self.overrides) or None, self._unit()
        worker = Worker(self.ctx, lambda _ctx, _: actions.csv_preview(path, overrides, unit), self)
        worker.succeeded.connect(self._show_preview)
        worker.failed.connect(self._preview_failed)
        worker.finished.connect(self._preview_finished)
        self.preview_worker = worker
        worker.start()

    def _preview_finished(self) -> None:
        self.preview_worker = None
        if self.preview_again:
            self.preview_again = False
            self._refresh()

    def _preview_failed(self, message: str) -> None:
        self.lines.clear_lines()
        self.lines.add(message, outcomes.ERROR)

    def preview_busy(self) -> bool:
        return self.preview_worker is not None

    def _show_preview(self, preview: actions.CsvPreview) -> None:
        if self.preview_again:
            return  # a newer mapping is on its way
        self.lines.clear_lines()
        self.header = preview.header
        self.auto = preview.auto
        self._fill_combos(preview.header, preview.columns)
        parsed = preview.parse
        if parsed is None:
            # no title column: the user picks it, the rest of the mapping stays
            self.table.setRowCount(0)
            self.lines.add(preview.error or "", outcomes.ERROR)
            return
        self._show_rows(parsed)
        self.lines.add(
            tr("File: {encoding}, delimiter {delimiter!r}. Tracks in file: {count}.").format(
                encoding=parsed.table.encoding,
                delimiter=parsed.table.delimiter,
                count=len(parsed.tracks),
            )
        )
        self.lines.add_lines(outcomes.mapping_lines(parsed) + outcomes.count_lines(parsed))
        self.start.setEnabled(bool(parsed.tracks) and not self._busy())

    def _show_rows(self, parsed: csv_file.CsvParse) -> None:
        rows = parsed.tracks[: csv_file.PREVIEW_ROWS]
        self.table.setRowCount(len(rows))
        for index, track in enumerate(rows):
            ids = " / ".join(part for part in (track.isrc, track.spotify_id) if part)
            values = [str(index + 1), track.title, "; ".join(track.artists), track.album or "",
                      views.length_label(track.duration_ms), ids]  # fmt: skip
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))

    # import

    def _busy(self) -> bool:
        return self.worker is not None

    def _import(self) -> None:
        name = self.name.text()
        overrides = dict(self.overrides) or None
        unit = self._unit()
        path, cid = self.path, self.collection_id
        self.start.setEnabled(False)
        self.cancel.setEnabled(False)
        self.lines.add(tr("Importing..."))
        self.worker = Worker(
            self.ctx,
            lambda ctx, _: actions.import_csv(
                ctx, path, name=name, overrides=overrides, length_unit=unit, collection_id=cid
            ),
            self,
        )
        self.outcome = None
        self.worker.succeeded.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _done(self, outcome: actions.CsvImportOutcome) -> None:
        self.outcome = outcome

    def _finished(self) -> None:
        # closes only after the thread ended
        self.worker = None
        if self.outcome is not None:
            self.imported.emit(self.outcome)
            self.accept()
        else:
            self.start.setEnabled(True)

    def _failed(self, message: str) -> None:
        self.lines.add(message, outcomes.ERROR)
        self.cancel.setEnabled(True)
