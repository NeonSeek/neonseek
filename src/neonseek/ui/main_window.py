"""main window: collections on the left, the open collection on the right, the log below.

every action calls core (neonseek.actions, views, outcomes). long ones run in workers.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import neonseek
from neonseek import actions, applog, config, manual, outcomes, views
from neonseek.errors import NeonSeekError
from neonseek.i18n import ntr, tr
from neonseek.pipeline import runner
from neonseek.sources.base import KIND_CSV, row_name
from neonseek.sources.links import find_links
from neonseek.ui.covers import CoverLoader
from neonseek.ui.dialogs.about import AboutDialog
from neonseek.ui.dialogs.add_sources import AddSourcesDialog, csv_filter
from neonseek.ui.dialogs.csv_import import CsvImportDialog
from neonseek.ui.dialogs.download import ConfirmDownloadDialog
from neonseek.ui.dialogs.edit_track import EditTrackDialog
from neonseek.ui.dialogs.manual import ManualWindow
from neonseek.ui.dialogs.properties import PropertiesDialog
from neonseek.ui.dialogs.remove import RemoveCollectionDialog
from neonseek.ui.dialogs.report import ReportDialog
from neonseek.ui.dialogs.settings import SettingsDialog
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.track_menu import build_track_menu
from neonseek.ui.widgets.collection_header import CollectionHeader
from neonseek.ui.widgets.collections_panel import CollectionsPanel
from neonseek.ui.widgets.common import LiveTexts, SectionLabel
from neonseek.ui.widgets.log_panel import LogPanel
from neonseek.ui.widgets.track_table import TrackTable
from neonseek.ui.workers import DownloadWorker, Worker

# .txt is the playlist export of itunes and the music app (D-233)
CSV_SUFFIXES = (".csv", ".tsv", ".txt")
SCOPE_COLLECTION = "collection"
SCOPE_LIBRARY = "library"
# a low window keeps this many whole rows of tracks in view: the header goes to one line and
# the log gets smaller or folds (D-258)
MIN_TRACK_ROWS = 8
# height of the open log at 100 % text size
LOG_HEIGHT = 150
# how the window shows the log: at its height, lower, or only its bar
LOG_OPEN, LOG_LOWER, LOG_FOLDED = "open", "lower", "folded"


def reveal(path: Path, is_file: bool) -> None:
    """explorer with the file selected, or the folder opened."""
    if is_file and sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent if is_file else path)))


def run_dialog(dialog: QDialog) -> int:
    """a modal dialog that is gone afterwards, so closed dialogs do not pile up."""
    result = dialog.exec()
    dialog.deleteLater()
    return result


def with_live_label(row: views.TrackRow, percent: int | None) -> views.TrackRow:
    return replace(row, state_label=views.live_label(row, percent))


class MainWindow(QMainWindow):
    # true while a download runs, the report and dialogs follow it
    downloads_changed = Signal(bool)

    def __init__(
        self,
        ctx: actions.AppContext,
        theme: ThemeManager,
        app_log: applog.AppLog | None = None,
        list_at_start: bool = True,
    ) -> None:
        super().__init__()
        self.ctx = ctx
        self.theme = theme
        self.app_log = app_log or applog.AppLog(ctx.data_dir)
        self.setWindowTitle("NeonSeek")
        self.resize(1320, 840)
        # no fixed minimum: the one of the content grows with the text size, and at 100 % it fits
        # a 1366x768 screen at 125 % windows scale (D-252)
        self.setAcceptDrops(True)
        self.log = LogPanel(theme, sink=self.app_log.write)
        self.covers = CoverLoader(config.covers_dir(ctx.data_dir), ctx.http_factory)
        self.summaries: list = []
        self.current: int | None = None
        self.scope = SCOPE_COLLECTION
        self.task: Worker | None = None
        self.task_name = ""
        self.download: DownloadWorker | None = None
        self.monitor: outcomes.RunMonitor | None = None
        self.close_after_download = False
        # collections whose tags changed in neonseek but not yet in the files
        self.tags_pending: set[int] = set()
        # the spotify playlist list is read at start (D-130), the screenshot tool says no
        self.list_at_start = list_at_start
        self.report_dialog: ReportDialog | None = None
        self.manual_window: ManualWindow | None = None
        # fixed texts follow the language without restart (D-163)
        self.texts = LiveTexts(theme, self)
        # a library move runs as the background task: downloads wait for it (D-128)
        self.moving = False
        # the header and the log folded by the window, not by hand, open again when the
        # window grows. the height of the open log, once its edge was dragged (D-258)
        self.auto_compact = False
        self.auto_folded = False
        self.log_height: int | None = None
        self.fit_timer = QTimer(self)
        self.fit_timer.setSingleShot(True)
        self.fit_timer.timeout.connect(self.fit_parts)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._top_bar())

        self.panel = CollectionsPanel(theme, self.covers)
        self.panel.current_changed.connect(self.open_collection)
        self.panel.selection_toggled.connect(self._toggle_selected)
        self.panel.menu_requested.connect(self._collection_menu)
        self.panel.spotify_action.connect(self._spotify_action)
        self.panel.add_button.clicked.connect(lambda: self.open_add())

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        self.right_layout = right_layout
        self.header = CollectionHeader(theme, self.covers)
        self.header.sync.clicked.connect(lambda: self.sync(self.current))
        self.header.download.clicked.connect(self._download_current)
        self.header.open_folder.clicked.connect(lambda: self.open_folder(self.current))
        self.header.write_tags.clicked.connect(self._write_tags)
        self.header.tags_edited.connect(self._save_tags)
        self.header.compact_chosen.connect(self._header_chosen)
        right_layout.addWidget(self.header)
        self.filter_bar = self._filter_bar()
        right_layout.addWidget(self.filter_bar)
        self.table = TrackTable(theme, self.covers)
        self.table.menu_requested.connect(self._track_menu)
        self.empty = QLabel()
        self.empty.setProperty("role", "hint")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty)
        right_layout.addWidget(self.stack, 1)

        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(self.panel)
        top.addWidget(right)
        top.setStretchFactor(0, 0)
        top.setStretchFactor(1, 1)
        top.setSizes([theme.px(330), theme.px(990)])
        top.setHandleWidth(8)
        self.top_splitter = top
        top.setChildrenCollapsible(False)

        self.vertical = QSplitter(Qt.Orientation.Vertical)
        self.vertical.addWidget(top)
        self.vertical.addWidget(self.log)
        self.vertical.setStretchFactor(0, 1)
        self.vertical.setStretchFactor(1, 0)
        self.vertical.setSizes([theme.px(680), theme.px(150)])
        self.vertical.setHandleWidth(8)
        self.vertical.setChildrenCollapsible(False)
        self.log.collapsed_changed.connect(self._log_collapsed)
        self.log.toggle.clicked.connect(self._log_chosen)
        self.vertical.splitterMoved.connect(self._log_dragged)
        self.log.cancel_clicked.connect(self.cancel_running)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        self.body_layout = body_layout
        body_layout.addWidget(self.vertical)
        root_layout.addWidget(body, 1)
        self.setCentralWidget(root)

        QShortcut(QKeySequence.StandardKey.Find, self, self._focus_search)
        # a text field takes ctrl+v for itself, elsewhere it adds spotify links
        QShortcut(QKeySequence.StandardKey.Paste, self, self.paste)
        # text size: ctrl and plus, ctrl and minus, ctrl+0 (D-115)
        for keys, step in (
            (QKeySequence.StandardKey.ZoomIn, 1),
            (QKeySequence("Ctrl+="), 1),
            (QKeySequence.StandardKey.ZoomOut, -1),
            (QKeySequence("Ctrl+0"), 0),
        ):
            QShortcut(keys, self, lambda s=step: self.step_ui_scale(s))
        self._shown = False

        self.search.addAction(theme.icon("search", "text.muted"),
                              QLineEdit.ActionPosition.LeadingPosition)  # fmt: skip
        theme.glow(self.header.download)
        theme.changed.connect(self._paint_icons)
        theme.changed.connect(self._rescale)
        theme.language_changed.connect(self._language_changed)
        self._rescale()
        self._paint_icons()
        theme.track_window(self)
        self._start()

    # layout parts

    def _top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setProperty("role", "bar")
        layout = QHBoxLayout(bar)
        self.bar_layout = layout
        layout.addWidget(SectionLabel("NEONSEEK", role="logo"))
        version = QLabel(f"v{neonseek.__version__}")
        version.setProperty("role", "muted")
        layout.addWidget(version)
        layout.addStretch(1)
        texts = self.texts
        self.download_checked = texts.text(QPushButton(), lambda: tr("DOWNLOAD CHECKED"))
        self.download_checked.clicked.connect(self._download_checked)
        self.report_button = texts.text(QPushButton(), lambda: tr("REPORT"))
        self.report_button.clicked.connect(self.open_report)
        self.settings_button = texts.text(QPushButton(), lambda: tr("SETTINGS"))
        self.settings_button.clicked.connect(self.open_settings)
        self.help_button = texts.text(QPushButton(), lambda: tr("HELP"))
        texts.tip(self.help_button, lambda: tr("The user manual, also as a PDF."))
        self.help_button.clicked.connect(lambda: self.open_manual())
        self.about_button = texts.text(QPushButton(), lambda: tr("ABOUT"))
        self.about_button.clicked.connect(self.open_about)
        for button, icon in (
            (self.download_checked, "download"),
            (self.report_button, "report"),
            (self.settings_button, "settings"),
            (self.help_button, "manual"),
            (self.about_button, "info"),
        ):
            button.setProperty("icon_name", icon)
            layout.addWidget(button)
        return bar

    def _filter_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.texts.text(SectionLabel(""), lambda: tr("SHOW")))
        self.filter = QComboBox()
        for key in views.FILTERS:
            self.filter.addItem("", key)
        self.texts.call(self._filter_names)
        self.filter.currentIndexChanged.connect(self._filter_changed)
        layout.addWidget(self.filter)
        self.search = QLineEdit()
        self.texts.placeholder(self.search, lambda: tr("Search title, artist, album (Ctrl+F)"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._search_changed)
        self.search.setMinimumWidth(self.theme.px(120))
        layout.addWidget(self.search, 1)
        self.scope_box = QComboBox()
        self.scope_box.addItem("", SCOPE_COLLECTION)
        self.scope_box.addItem("", SCOPE_LIBRARY)
        self.texts.call(self._scope_names)
        self.scope_box.currentIndexChanged.connect(self._scope_changed)
        layout.addWidget(self.scope_box)
        self.count = QLabel()
        self.count.setProperty("role", "muted")
        layout.addWidget(self.count)
        return bar

    def _filter_names(self) -> None:
        for index in range(self.filter.count()):
            self.filter.setItemText(index, views.filter_label(self.filter.itemData(index)))

    def _scope_names(self) -> None:
        self.scope_box.setItemText(0, tr("This collection"))
        self.scope_box.setItemText(1, tr("Whole library"))

    def _language_changed(self) -> None:
        """texts that core makes from the data: lists, counters, states, buttons (D-163)."""
        self.refresh_collections()
        self.reload_tracks()
        self._set_state()

    def _rescale(self) -> None:
        px = self.theme.px
        # the same margin on every side, so the right edge never looks cut (D-119)
        self.body_layout.setContentsMargins(px(12), px(10), px(12), px(12))
        self.bar_layout.setContentsMargins(px(14), px(8), px(12), px(8))
        self.bar_layout.setSpacing(px(8))
        self.search.setMinimumWidth(px(120))
        # other text sizes, other heights: the rows in view are counted again
        self.fit_timer.start()

    def step_ui_scale(self, step: int) -> None:
        """one size up or down, 0 back to 100 %. saved at once, like the theme."""
        current = self.ctx.settings().ui_scale
        wanted = config.DEFAULT_UI_SCALE if step == 0 else config.step_ui_scale(current, step)
        self.set_ui_scale(wanted)

    def set_ui_scale(self, percent: int) -> None:
        try:
            stored = actions.change_setting(self.ctx, "ui_scale", str(percent))
        except NeonSeekError as exc:
            self.log.add(exc.message, outcomes.ERROR)
            return
        if self.theme.percent() != stored:
            self.theme.apply(scale=stored)
            self.log.add(tr("Text size: {percent}%").format(percent=stored))

    def showEvent(self, event) -> None:  # noqa: N802 - qt name
        super().showEvent(event)
        if not self._shown:
            self._shown = True
            # the list scrolls once the window has its final size, from the top (D-116)
            QTimer.singleShot(0, self.panel.reveal_current)
            self.fit_timer.start()

    def resizeEvent(self, event) -> None:  # noqa: N802 - qt name
        super().resizeEvent(event)
        # only a new height changes the room for the tracks, after the layout has it
        if event.size().height() != event.oldSize().height():
            self.fit_timer.start()

    def _paint_icons(self) -> None:
        for button in (self.download_checked, self.report_button, self.settings_button,
                       self.help_button, self.about_button):  # fmt: skip
            button.setIcon(self.theme.icon(button.property("icon_name"), "text.primary"))
        for action in self.search.actions():
            action.setIcon(self.theme.icon("search", "text.muted"))

    # start and refresh

    def _start(self) -> None:
        self.log.add(f"NeonSeek {neonseek.__version__}")
        self.log.add(tr("Data folder: {path}").format(path=self.ctx.data_dir))
        try:
            settings = self.ctx.settings()
            library = settings.library_dir or tr("not chosen yet")
            self.log.add(tr("Music library: {path}").format(path=library))
            recovered = actions.recover_interrupted(self.ctx)
            if recovered:
                self.log.add(
                    ntr(
                        recovered,
                        "{count} track of an interrupted download waits again.",
                        "{count} tracks of an interrupted download wait again.",
                    ).format(count=recovered)
                )
            moved = actions.recover_library_move(self.ctx)
            if moved:
                self.log.add(
                    ntr(
                        moved,
                        "Finished {count} file of an interrupted library move.",
                        "Finished {count} files of an interrupted library move.",
                    ).format(count=moved)
                )
            last = views.last_run_text(self.ctx)
            if last:
                self.log.add(last)
        except NeonSeekError as exc:
            self.log.add(exc.message, outcomes.ERROR)
        self.refresh_collections()
        first = views.first_collection(self.summaries)
        if first is not None:
            self.panel.open_collection(first)
        else:
            self.open_collection(None)
        self._set_state()
        if self.list_at_start:
            QTimer.singleShot(0, self.refresh_list_at_start)

    def refresh_list_at_start(self) -> None:
        """the list of spotify playlists, no content, in the background. no internet is no
        error, only a line in the log (D-130)."""
        try:
            wanted = actions.list_at_start(self.ctx)
        except NeonSeekError:
            return
        if not wanted or self.busy():
            return

        def done(report) -> None:
            self.show_lines(outcomes.sync_lines(report).lines)

        self.run_task(
            tr("Refreshing the list of your Spotify playlists..."),
            lambda ctx, worker: actions.sync_spotify(ctx, list_only=True),
            done,
            lambda message: self.show_lines([outcomes.list_refresh_failed_line(message)]),
        )

    def refresh_collections(self) -> None:
        try:
            self.summaries = actions.list_collections(self.ctx)
        except NeonSeekError as exc:
            self.log.add(exc.message, outcomes.ERROR)
            return
        self.panel.update_collections(self.summaries)
        self.refresh_spotify()
        self._update_buttons()

    def refresh_spotify(self) -> None:
        state = actions.spotify_state(self.ctx)
        if state.ready:
            self.panel.set_spotify_action(
                tr("SYNC"),
                tr(
                    "Read your Spotify playlists and the selected collections again, also those "
                    "from Apple Music, YouTube, SoundCloud and TIDAL."
                ),
            )
        else:
            self.panel.set_spotify_action(tr("LOG IN"), actions.spotify_needed_message(state))

    def _summary(self, collection_id: int | None):
        return next((s for s in self.summaries if s.id == collection_id), None)

    def open_collection(self, collection_id: int | None) -> None:
        self.current = collection_id
        if self.scope == SCOPE_LIBRARY:
            self.scope_box.setCurrentIndex(0)  # reloads through _scope_changed
            return
        self.reload_tracks()

    def reload_tracks(self) -> None:
        if self.scope == SCOPE_LIBRARY:
            rows = views.library_tracks(self.ctx)
            self.header.show_library(rows)
            self.table.set_rows(rows)
            hint = tr("No tracks yet. Add a CSV file or a Spotify link with + ADD.")
        else:
            summary = self._summary(self.current)
            rows = views.collection_tracks(self.ctx, self.current) if summary else []
            self.header.show_collection(summary, rows)
            self.header.set_tags_pending(self.current in self.tags_pending)
            self.table.set_rows(rows)
            if summary is not None:
                hint = views.empty_hint(summary)
            else:
                hint = tr(
                    "No collections yet. Add a CSV file or a Spotify link with + ADD, or log in "
                    "to Spotify."
                )
        if not rows:
            self.empty.setText(hint)
            self.stack.setCurrentWidget(self.empty)
        else:
            self.stack.setCurrentWidget(self.table)
        self.filter_bar.setVisible(bool(rows) or self.scope == SCOPE_LIBRARY)
        self._update_count()
        self._update_buttons()

    def _filter_changed(self) -> None:
        self.table.proxy.set_state_filter(self.filter.currentData())
        self._update_count()

    def _search_changed(self, text: str) -> None:
        self.table.proxy.set_query(text)
        self._update_count()

    def _scope_changed(self) -> None:
        self.scope = self.scope_box.currentData()
        self.reload_tracks()

    def _focus_search(self) -> None:
        self.filter_bar.setVisible(True)
        self.search.setFocus()
        self.search.selectAll()

    def _update_count(self) -> None:
        shown, total = self.table.visible_count(), len(self.table.model_.rows)
        text = ntr(total, "{shown} of {total} track", "{shown} of {total} tracks")
        self.count.setText(text.format(shown=shown, total=total) if total else "")

    # state of the buttons

    # a worker counts until its finished handler ran, results come before the idle state

    def downloading(self) -> bool:
        return self.download is not None

    def busy(self) -> bool:
        return self.task is not None

    def _update_buttons(self) -> None:
        # a library move counts as a download here: files must stay still
        downloading = self.downloading() or self.moving
        selected = [s for s in self.summaries if s.selected]
        # never active without a ticked collection (PLAN.md section 8). selected rows of the
        # table are not checked collections, so the button names the checkbox (D-158)
        self.download_checked.setEnabled(bool(selected) and not downloading)
        if not selected:
            tip = tr(
                "Tick the checkbox next to a playlist on the left first. To download single "
                "tracks, select them in the table, right-click and choose Download these tracks "
                "only."
            )
        elif downloading:
            tip = views.running_reason()
        else:
            tip = tr("Downloads the missing tracks of the ticked collections: {names}").format(
                names=", ".join(s.name for s in selected)
            )
        self.download_checked.setToolTip(tip)
        has = self._summary(self.current) is not None and self.scope == SCOPE_COLLECTION
        self.header.download.setEnabled(has and not downloading)
        self.header.sync.setEnabled(has and not self.busy())
        self.header.write_tags.setEnabled(has and not self.busy())
        self.panel.set_busy(self.busy())

    def _set_state(self) -> None:
        if self.downloading():
            text = self.monitor.progress_text() if self.monitor else tr("Downloading...")
        elif self.busy():
            text = self.task_name
        else:
            text = tr("Ready.")
        self.log.set_state(text)

    # background tasks: one at a time, next to a download

    def run_task(self, name: str, fn, on_done, on_error=None) -> Worker | None:
        if self.busy():
            QMessageBox.information(
                self, "NeonSeek", tr("Wait until this finishes: {task}").format(task=self.task_name)
            )
            return None
        self.task_name = name
        self.log.add(name)
        worker = Worker(self.ctx, fn, self)
        worker.progress.connect(self.log.add)
        worker.succeeded.connect(on_done)
        worker.failed.connect(on_error or self._task_failed)
        worker.finished.connect(self._task_finished)
        worker.finished.connect(worker.deleteLater)
        self.task = worker
        worker.start()
        self._update_buttons()
        self._set_state()
        return worker

    def _task_failed(self, message: str) -> None:
        self.log.add(message, outcomes.ERROR)
        if self.task is not None and self.task.trace:
            self.app_log.write(self.task.trace, outcomes.ERROR)
        QMessageBox.warning(self, "NeonSeek", message)

    def _task_finished(self) -> None:
        self.task = None
        self.refresh_collections()
        self.reload_tracks()
        self._set_state()

    def show_lines(self, lines: list[outcomes.Line]) -> None:
        for line in lines:
            self.log.add(line.text, line.kind)

    # selection

    def _toggle_selected(self, collection_id: int, selected: bool) -> None:
        try:
            actions.set_selected(self.ctx, [collection_id], selected)
        except NeonSeekError as exc:
            QMessageBox.warning(self, "NeonSeek", exc.message)
        self.refresh_collections()

    # downloads (PLAN.md section 8)

    def _download_current(self) -> None:
        if self.current is not None:
            self.start_download([self.current])

    def _download_checked(self) -> None:
        ids = actions.selected_collection_ids(self.ctx)
        if ids:
            self.start_download(ids)

    def download_tracks(self, track_ids: list[int]) -> None:
        """download this track only, and retry selected in the report (D-125, D-131)."""
        if track_ids:
            self.start_download([], track_ids)

    def start_download(self, collection_ids: list[int], track_ids: list[int] | None = None):
        if self.downloading():
            QMessageBox.information(self, "NeonSeek", tr("A download is already running."))
            return
        if self.moving:
            QMessageBox.information(
                self, "NeonSeek", tr("Wait until the library move ends, then download.")
            )
            return
        worker = DownloadWorker(self.ctx, collection_ids, self, track_ids)
        worker.planned.connect(self._confirm_download)
        worker.nothing.connect(self._nothing_to_download)
        worker.declined.connect(
            lambda: self.log.add(tr("Cancelled, nothing was downloaded."), outcomes.WARN)
        )
        worker.event.connect(self._download_event)
        worker.ran.connect(self._download_ended)
        worker.failed.connect(self._download_failed)
        worker.finished.connect(self._download_finished)
        worker.finished.connect(worker.deleteLater)
        self.download = worker
        self.monitor = outcomes.RunMonitor()
        worker.start()
        self._update_buttons()
        self.downloads_changed.emit(True)

    def _confirm_download(self, plan: actions.DownloadPlan) -> None:
        # every start shows collections, tracks and the search time first
        dialog = ConfirmDownloadDialog(self.theme, plan, self)
        go = run_dialog(dialog) == QDialog.DialogCode.Accepted
        if go:
            self.show_lines(outcomes.plan_lines(plan, gui=True))
            self.log.add(tr("Download started."))
            self.log.cancel.setVisible(True)
            self.log.set_progress(0, 0, tr("Starting..."))
        if self.download is not None:
            self.download.decide(go)
        self._set_state()

    def _nothing_to_download(self, plan: actions.DownloadPlan, paths: list) -> None:
        self.show_lines(outcomes.plan_lines(plan, gui=True))
        self.show_lines(outcomes.nothing_to_download_lines(paths))

    def _download_event(self, event) -> None:
        if self.monitor is None:
            return
        self.show_lines(self.monitor.feed(event))
        self.log.set_waiting(self.monitor.waiting)
        if self.monitor.pass_number:
            self.log.set_progress(self.monitor.pass_done, self.monitor.pass_total,
                                  self.monitor.progress_text())  # fmt: skip
        changed = self.monitor.changed
        if changed:
            numbers = self.table.model_.track_numbers()
            shown = [tid for tid in changed if tid in numbers]
            if shown:
                fresh = views.tracks_for(self.ctx, shown, numbers)
                self.table.model_.update_rows(
                    {
                        tid: with_live_label(row, self.monitor.percent.get(tid))
                        for tid, row in fresh.items()
                    }  # fmt: skip
                )
            if isinstance(event, runner.TrackFinished):
                self.refresh_collections()
        self._set_state()

    def _download_ended(self, outcome: runner.RunOutcome) -> None:
        self.show_lines(outcomes.run_end_lines(self.ctx, outcome))
        if outcome.ended == runner.ERROR and outcome.message:
            QMessageBox.warning(self, "NeonSeek", outcome.message)

    def _download_failed(self, message: str) -> None:
        self.log.add(message, outcomes.ERROR)
        if self.download is not None and self.download.trace:
            self.app_log.write(self.download.trace, outcomes.ERROR)
        QMessageBox.warning(self, "NeonSeek", message)

    def _download_finished(self) -> None:
        self.download = None
        self.monitor = None
        self.log.cancel.setVisible(False)
        self.log.set_waiting(False)
        self.log.set_progress(None)
        self.refresh_collections()
        self.reload_tracks()
        self._set_state()
        self.downloads_changed.emit(False)
        self.reload_report()
        if self.close_after_download:
            self.close()

    def cancel_download(self) -> None:
        if self.downloading():
            self.log.add(tr("Cancelling the download..."), outcomes.WARN)
            self.download.cancel()

    def cancel_running(self) -> None:
        """the cancel button of the log: the download, or a library move."""
        if self.downloading():
            self.cancel_download()
        elif self.moving and self.task is not None:
            self.log.add(tr("Stopping the library move after the current file..."), outcomes.WARN)
            self.task.cancel()

    # spotify

    def _spotify_action(self) -> None:
        state = actions.spotify_state(self.ctx)
        if state.ready:
            self.sync(None)
        elif not state.client_id:
            QMessageBox.information(self, "NeonSeek", actions.spotify_needed_message(state))
            self.open_settings()
        else:
            self.spotify_login()

    def spotify_login(self) -> None:
        def login(ctx, worker):
            flow = actions.start_spotify_login(ctx)
            worker.cancel_hook = flow.cancel
            worker.event.emit(flow.url)
            return actions.finish_spotify_login(ctx, flow)

        def open_browser(url: str) -> None:
            self.log.add(tr("Log in to Spotify in your browser. If it does not open, go to:"))
            self.log.add(url)
            QDesktopServices.openUrl(QUrl(url))

        def done(me: dict) -> None:
            self.log.add(
                tr("Logged in to Spotify as {name}.").format(
                    name=me.get("display_name") or me["id"]
                ),
                outcomes.DONE,
            )
            self.app_log.refresh_secrets()

        worker = self.run_task(tr("Waiting for the Spotify login in your browser..."), login, done)
        if worker is not None:
            worker.event.connect(open_browser)

    def sync(self, collection_id: int | None) -> None:
        """spotify needs a login, apple music, youtube, soundcloud and tidal do not (D-227)."""
        state = actions.spotify_state(self.ctx)
        summary = self._summary(collection_id) if collection_id is not None else None
        web_only = (summary is not None and views.is_web(summary.kind)) or (
            collection_id is None and actions.web_selected(self.ctx)
        )
        if not state.ready and not web_only:
            QMessageBox.information(self, "NeonSeek", actions.spotify_needed_message(state))
            if state.client_id:
                self.spotify_login()
            return

        def done(outcome) -> None:
            self.show_lines(outcomes.all_sync_lines(outcome).lines)
            if outcomes.nothing_synced(outcome) and collection_id is None:
                self.log.add(
                    tr("Tick the checkbox of the playlists you want, then press SYNC again.")
                )

        self.run_task(
            tr("Reading the collections again..."),
            lambda ctx, worker: actions.sync(ctx, collection_id, worker.progress.emit),
            done,
        )

    # adding

    def open_add(self, text: str = "") -> None:
        dialog = AddSourcesDialog(self.ctx, self.theme, text, self)
        dialog.added.connect(self._links_added)
        chosen: list[str] = []
        dialog.csv_chosen.connect(chosen.append)
        login: list[bool] = []
        dialog.login_wanted.connect(lambda: (login.append(True), dialog.reject()))
        run_dialog(dialog)
        if chosen:
            self.import_csv(Path(chosen[0]))
        elif login:
            self._spotify_action()

    def _links_added(self, results: list) -> None:
        self.show_lines(outcomes.add_lines(results))
        self.refresh_collections()
        ids = [r.collection_id for r in results if r.collection_id is not None]
        if ids:
            self.panel.open_collection(ids[-1])

    def import_csv(self, path: Path, collection_id: int | None = None) -> None:
        dialog = CsvImportDialog(self.ctx, self.theme, path, collection_id, self)
        dialog.imported.connect(self._imported)
        run_dialog(dialog)

    def _imported(self, outcome: actions.CsvImportOutcome) -> None:
        self.show_lines(outcomes.import_lines(outcome))
        self.refresh_collections()
        self.panel.open_collection(outcome.collection_id)
        self.reload_tracks()

    def paste(self) -> None:
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()
        files = [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()] if mime else []
        csv_files = [f for f in files if f.suffix.lower() in CSV_SUFFIXES]
        if csv_files:
            self.import_csv(csv_files[0])
            return
        links = find_links(clipboard.text())
        if links:
            self.open_add("\n".join(links))

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - qt name
        if self._dropped_csv(event) is not None:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - qt name
        path = self._dropped_csv(event)
        if path is not None:
            event.acceptProposedAction()
            self.import_csv(path)

    @staticmethod
    def _dropped_csv(event) -> Path | None:
        mime = event.mimeData()
        for url in mime.urls() if mime is not None else []:
            if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in CSV_SUFFIXES:
                return Path(url.toLocalFile())
        return None

    # collection menu

    def _collection_menu(self, collection_id: int, pos: QPoint) -> None:
        menu = self.collection_menu(collection_id)
        if menu is not None:
            menu.exec(pos)

    def collection_menu(self, collection_id: int) -> QMenu | None:
        summary = self._summary(collection_id)
        if summary is None:
            return None
        menu = QMenu(self)
        download = menu.addAction(tr("Download"), lambda: self.start_download([collection_id]))
        download.setEnabled(not self.downloading())
        sync_label = views.sync_label(summary.kind)
        if sync_label:
            menu.addAction(sync_label, lambda: self.sync(collection_id))
        if summary.kind == KIND_CSV:
            menu.addAction(tr("Import again"), lambda: self.reimport(collection_id))
            menu.addAction(tr("Import from another file..."),
                           lambda: self.choose_moved_file(collection_id))  # fmt: skip
        source = actions.collection_source(self.ctx, collection_id)
        if source.url or source.path:
            menu.addAction(tr("Open source"), lambda: self.open_source(collection_id))
        menu.addAction(tr("Open folder"), lambda: self.open_folder(collection_id))
        menu.addSeparator()
        menu.addAction(tr("Remove from NeonSeek..."), lambda: self.remove(collection_id))
        return menu

    def reimport(self, collection_id: int) -> None:
        missing: list[NeonSeekError] = []

        def failed(message: str) -> None:
            self.log.add(message, outcomes.ERROR)
            if not missing:
                QMessageBox.warning(self, "NeonSeek", message)
                return
            answer = QMessageBox.question(
                self, "NeonSeek", message + "\n\n" + tr("Choose the file now?")
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.choose_moved_file(collection_id)

        worker = self.run_task(
            tr("Importing the CSV file again..."),
            lambda ctx, _: actions.reimport_csv(ctx, collection_id),
            lambda outcome: self.show_lines(outcomes.import_lines(outcome)),
            failed,
        )
        if worker is not None:
            worker.error.connect(
                lambda exc: (
                    missing.append(exc) if isinstance(exc, actions.MissingSourceFileError) else None
                )
            )

    def choose_moved_file(self, collection_id: int) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Choose the CSV file"), "", csv_filter())
        if path:
            self.import_csv(Path(path), collection_id)

    def remove_dialog(self, plan: actions.RemovePlan) -> RemoveCollectionDialog:
        busy = self.downloading() or self.moving
        return RemoveCollectionDialog(self.theme, plan, busy, self)

    def remove(self, collection_id: int) -> None:
        """files stay unless the user ticks the recycle bin option, off by default (D-127)."""
        try:
            plan = actions.remove_plan(self.ctx, [collection_id])
        except NeonSeekError as exc:
            QMessageBox.warning(self, "NeonSeek", exc.message)
            return
        dialog = self.remove_dialog(plan)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        with_files = dialog.with_files()
        dialog.deleteLater()
        if not accepted:
            return
        try:
            if with_files:
                removed = actions.remove_collections_and_files(self.ctx, [collection_id])
                lines = outcomes.remove_lines(removed)
                self.show_lines(lines)
                if removed.failed or removed.unconfirmed:
                    QMessageBox.warning(self, "NeonSeek", outcomes.texts(lines))
            else:
                actions.remove_collections(self.ctx, [collection_id])
                self.log.add(
                    tr("Removed collections {ids}. Tracks, download state and files stay.").format(
                        ids=collection_id
                    )
                )
        except NeonSeekError as exc:
            QMessageBox.warning(self, "NeonSeek", exc.message)
            return
        self.refresh_collections()
        if self.current == collection_id:
            self.open_collection(None)
        self.reload_report()

    def open_source(self, collection_id: int) -> None:
        source = actions.collection_source(self.ctx, collection_id)
        if source.url:
            QDesktopServices.openUrl(QUrl(source.url))
        elif source.path:
            path = Path(source.path)
            if path.is_file():
                reveal(path, True)
            else:
                QMessageBox.information(
                    self,
                    "NeonSeek",
                    tr("The file is no longer at {path}. Use Import from another file.").format(
                        path=path
                    ),
                )

    def open_folder(self, collection_id: int | None) -> None:
        if collection_id is None:
            return
        try:
            place = actions.collection_folder(self.ctx, collection_id)
        except NeonSeekError as exc:
            QMessageBox.information(self, "NeonSeek", exc.message)
            return
        reveal(place.path, place.is_file)

    # tags (PLAN.md sections 8 and 9)

    def _save_tags(self, genre: str, comment: str) -> None:
        collection_id = self.current
        if collection_id is None:
            return
        try:
            row = actions.set_collection_tags(self.ctx, collection_id, genre, comment)
        except NeonSeekError as exc:
            QMessageBox.warning(self, "NeonSeek", exc.message)
            return
        self.show_lines([outcomes.tags_saved_line(row["genre"], row["comment"], row_name(row)),
                         outcomes.retag_hint(collection_id, gui=True)])  # fmt: skip
        self.tags_pending.add(collection_id)
        self.refresh_collections()
        self.header.show_collection(self._summary(collection_id), self.table.model_.rows)
        self.header.set_tags_pending(True)

    def _write_tags(self) -> None:
        collection_id = self.current
        if collection_id is None:
            return
        # a tag typed but not left yet is saved first
        self.header._edited()

        def done(outcome: actions.RetagOutcome) -> None:
            lines = outcomes.retag_lines(outcome)
            self.show_lines(lines)
            self.tags_pending.discard(collection_id)
            self.header.set_tags_pending(False)
            QMessageBox.information(self, "NeonSeek", outcomes.texts(lines))

        self.run_task(
            tr("Writing tags into the downloaded files..."),
            lambda ctx, _: actions.retag(ctx, [collection_id]),
            done,
        )

    # dialogs

    def open_report(self) -> None:
        """the report stays open next to the window, so a download from it can be followed
        in the log (D-131)."""
        if self.report_dialog is not None:
            self.report_dialog.raise_()
            self.report_dialog.activateWindow()
            return
        dialog = ReportDialog(self.ctx, self.theme, self)
        dialog.download_requested.connect(self.download_tracks)
        dialog.menu_requested.connect(self._track_menu)
        dialog.help_requested.connect(lambda: self.open_manual(manual.CHAPTER_REPORT))
        dialog.set_downloading(self.downloading() or self.moving)
        self.downloads_changed.connect(dialog.set_downloading)
        dialog.finished.connect(self._report_closed)
        self.report_dialog = dialog
        dialog.show()

    def _report_closed(self) -> None:
        dialog, self.report_dialog = self.report_dialog, None
        if dialog is not None:
            self.downloads_changed.disconnect(dialog.set_downloading)
            dialog.deleteLater()
        self.refresh_collections()
        self.reload_tracks()

    def reload_report(self) -> None:
        if self.report_dialog is not None:
            self.report_dialog.load()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.ctx, self.theme, self, self.downloading() or self.moving)
        run_dialog(dialog)
        self.app_log.refresh_secrets()
        self.refresh_collections()
        self.reload_tracks()
        if dialog.move_to:
            self.move_library(dialog.move_to)

    # the library folder (D-128)

    def move_library(self, folder: str) -> None:
        """moves the downloaded files in the background, stoppable with cancel."""
        stop = []

        def move(ctx, worker):
            worker.cancel_hook = lambda: stop.append(True)
            return actions.move_library(
                ctx,
                folder,
                lambda done, total, name: worker.event.emit((done, total, name)),
                lambda: bool(stop),
            )

        def step(event) -> None:
            done, total, name = event
            text = tr("Moving the library: {done} / {total}").format(done=done, total=total)
            self.log.set_progress(done, total, text)
            self.log.set_state(text)

        def done(outcome: actions.LibraryMoveOutcome) -> None:
            lines = outcomes.move_lines(outcome, gui=True)
            self.show_lines(lines)
            if not outcome.complete:
                QMessageBox.warning(self, "NeonSeek", outcomes.texts(lines))

        def failed(message: str) -> None:
            self._task_failed(message)

        worker = self.run_task(tr("Moving the library..."), move, done, failed)
        if worker is None:
            return
        self.moving = True
        worker.event.connect(step)
        worker.finished.connect(self._move_finished)
        self.log.cancel.setVisible(True)
        self.log.set_cancel_tip(
            lambda: tr(
                "Stops the move after the current file. Nothing is lost, move again to finish."
            )
        )
        self.log.set_progress(0, 0, tr("Moving the library..."))
        self._update_buttons()
        self.downloads_changed.emit(True)

    def _move_finished(self) -> None:
        self.moving = False
        self.log.cancel.setVisible(False)
        self.log.set_cancel_tip(None)
        self.log.set_progress(None)
        self._update_buttons()
        self.downloads_changed.emit(False)
        self.reload_report()

    # the track menu (D-125), in the table and the report

    def track_menu(self, track_ids: list[int]) -> QMenu | None:
        try:
            rows = list(views.tracks_for(self.ctx, track_ids, {}).values())
        except NeonSeekError as exc:
            self.log.add(exc.message, outcomes.ERROR)
            return None
        order = {tid: index for index, tid in enumerate(track_ids)}
        rows.sort(key=lambda row: order.get(row.track_id, 0))
        choice = views.track_choice(rows, self.downloading() or self.moving)
        handlers = {
            views.MENU_DOWNLOAD: self.download_tracks,
            views.MENU_PROPERTIES: self.show_properties,
            views.MENU_EDIT: self.edit_track,
            views.MENU_FOLDER: self.show_in_folder,
            views.MENU_EXCLUDE: self.exclude_tracks,
            views.MENU_INCLUDE: self.include_tracks,
            views.MENU_DELETE: self.delete_files,
        }
        return build_track_menu(self, self.theme, choice, handlers)

    def _track_menu(self, track_ids: list[int], pos: QPoint) -> None:
        menu = self.track_menu(track_ids)
        if menu is not None:
            menu.exec(pos)
            menu.deleteLater()

    def _tracks_changed(self) -> None:
        self.refresh_collections()
        self.reload_tracks()
        self.reload_report()

    def show_properties(self, track_ids: list[int]) -> None:
        parent = self.report_dialog or self
        run_dialog(PropertiesDialog(self.ctx, self.theme, track_ids, parent))

    def edit_track(self, track_ids: list[int]) -> None:
        """artist and title by hand, before the track is searched (D-229)."""
        parent = self.report_dialog or self
        dialog = EditTrackDialog(self.ctx, self.theme, track_ids[0], parent)
        dialog.saved.connect(lambda line: (self.show_lines([line]), self._tracks_changed()))
        run_dialog(dialog)

    def show_in_folder(self, track_ids: list[int]) -> None:
        """explorer with the file selected. all files share one folder, explorer selects one,
        the first of the chosen tracks."""
        for track_id in track_ids:
            info = actions.track_info(self.ctx, track_id)
            if info.file_exists:
                reveal(Path(info.row["file_path"]), True)
                return
        paths = [actions.track_info(self.ctx, t).row["file_path"] for t in track_ids]
        QMessageBox.information(
            self,
            "NeonSeek",
            tr("The file is no longer at {path}. Download the track again.").format(
                path=next((p for p in paths if p), "-")
            ),
        )

    def exclude_tracks(self, track_ids: list[int]) -> None:
        try:
            changed = actions.exclude_tracks(self.ctx, track_ids)
        except NeonSeekError as exc:
            QMessageBox.warning(self, "NeonSeek", exc.message)
            return
        self.show_lines(outcomes.exclude_lines(changed, track_ids, gui=True))
        self._tracks_changed()

    def include_tracks(self, track_ids: list[int]) -> None:
        try:
            changed = actions.include_tracks(self.ctx, track_ids)
        except NeonSeekError as exc:
            QMessageBox.warning(self, "NeonSeek", exc.message)
            return
        self.show_lines(outcomes.include_lines(changed, track_ids))
        self._tracks_changed()

    def delete_files(self, track_ids: list[int]) -> None:
        """files to the recycle bin after a question with their number (D-127)."""
        try:
            plan = actions.delete_plan(self.ctx, track_ids)
        except NeonSeekError as exc:
            QMessageBox.warning(self, "NeonSeek", exc.message)
            return
        self.show_lines(outcomes.delete_plan_lines(plan))
        if plan.files and plan.problem:
            # a drive without a recycle bin: refuse instead of deleting for good
            self.log.add(plan.problem, outcomes.ERROR)
            QMessageBox.warning(self, "NeonSeek", plan.problem)
            return
        if not plan.files and not plan.gone:
            return
        # a file already gone needs no question, nothing is deleted for it
        if plan.files:
            parent = self.report_dialog or self
            answer = QMessageBox.question(parent, "NeonSeek", outcomes.delete_question(plan))
            if answer != QMessageBox.StandardButton.Yes:
                self.log.add(tr("Cancelled. Nothing was deleted."))
                return
        try:
            outcome = actions.delete_track_files(self.ctx, track_ids)
        except NeonSeekError as exc:
            QMessageBox.warning(self, "NeonSeek", exc.message)
            return
        self.show_lines(outcomes.delete_lines(outcome))
        # a file not deleted, or not confirmed in the recycle bin: the user has to look
        if outcome.failed or outcome.unconfirmed:
            QMessageBox.warning(self, "NeonSeek", outcomes.texts(outcomes.delete_lines(outcome)))
        self._tracks_changed()

    def open_about(self) -> None:
        run_dialog(AboutDialog(self.ctx, self.theme, self))

    def open_manual(self, chapter: str | None = None) -> None:
        """the manual next to the window, in the language of the window, at a chapter or the
        last one read (D-164, D-182)."""
        if self.manual_window is not None:
            if chapter:
                self.manual_window.show_chapter(chapter)
            self.manual_window.raise_()
            self.manual_window.activateWindow()
            return
        window = ManualWindow(self.ctx, self.theme, self, chapter)
        window.finished.connect(self._manual_closed)
        self.manual_window = window
        window.show()

    def _manual_closed(self) -> None:
        window, self.manual_window = self.manual_window, None
        if window is not None:
            window.deleteLater()

    # window

    def _log_collapsed(self, collapsed: bool) -> None:
        self._set_log_height(self.log.folded_height() if collapsed else self._wanted_log())

    def _set_log_height(self, height: int) -> None:
        total = sum(self.vertical.sizes())
        self.vertical.setSizes([total - height, height])

    def _wanted_log(self) -> int:
        return self.log_height or self.theme.px(LOG_HEIGHT)

    def _header_chosen(self, _compact: bool) -> None:
        # by hand: a folded header stays folded, an open one may fold on the next resize
        self.auto_compact = False

    def _log_chosen(self) -> None:
        self.auto_folded = False

    def _log_dragged(self, _pos: int, _index: int) -> None:
        if not self.log.collapsed:
            self.log_height = self.log.height()

    def fit_parts(self) -> None:
        """the header and the log take what the tracks leave: at least MIN_TRACK_ROWS whole
        rows stay in view (D-258). in this order, the first that fits wins: the whole header
        and the log at its height, the one line header and the log at its height, the one
        line header and a lower log, the one line header and the folded log. what was folded
        by hand stays folded, what the window folded opens again when there is room."""
        total = self.vertical.height() - self.vertical.handleWidth()
        if total <= 0:
            return
        need = self.table.height_for_rows(MIN_TRACK_ROWS)
        around = self.filter_bar.sizeHint().height() + 2 * self.right_layout.spacing()
        headers = [True] if self.header.compact and not self.auto_compact else [False, True]
        options = []
        if not (self.log.collapsed and not self.auto_folded):
            options += [(compact, LOG_OPEN) for compact in headers]
            options += [(compact, LOG_LOWER) for compact in headers]
        options += [(compact, LOG_FOLDED) for compact in headers]
        wanted = self._wanted_log()
        smallest = {LOG_OPEN: wanted, LOG_LOWER: min(wanted, self.log.least_open_height()),
                    LOG_FOLDED: self.log.folded_height()}  # fmt: skip
        compact, log = options[-1]
        room = 0
        for header, height in options:
            left = total - around - self.header.height_in(header) - need
            if left >= smallest[height]:
                compact, log, room = header, height, left
                break
        if compact != self.header.compact:
            self.header.set_compact(compact)
            self.auto_compact = compact
        if log == LOG_FOLDED:
            if not self.log.collapsed:
                self.auto_folded = True
                self.log.set_collapsed(True)
            return
        if self.log.collapsed:
            self.auto_folded = False
            self.log.set_collapsed(False)
        # a lower log takes all the room the tracks leave
        self._set_log_height(wanted if log == LOG_OPEN else min(wanted, room))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - qt name
        if self.downloading():
            answer = QMessageBox.question(
                self,
                "NeonSeek",
                tr(
                    "A download is running. Cancel it and close NeonSeek? Finished tracks are kept."
                ),  # fmt: skip
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.close_after_download = True
                self.cancel_download()
            event.ignore()
            return
        if self.busy():
            self.task.cancel()
            QMessageBox.information(
                self, "NeonSeek", tr("Wait until this finishes: {task}").format(task=self.task_name)
            )
            event.ignore()
            return
        if self.report_dialog is not None:
            self.report_dialog.close()
        if self.manual_window is not None:
            self.manual_window.close()
        self.covers.shutdown()
        self.ctx.close()
        super().closeEvent(event)
