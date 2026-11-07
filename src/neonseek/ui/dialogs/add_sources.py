"""+ ADD: links of spotify, apple music, youtube, soundcloud and tidal, one per line, or a csv file
(PLAN.md section 9, D-227)."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from neonseek import actions, outcomes
from neonseek.i18n import tr
from neonseek.sources import spotify_url
from neonseek.ui.dialogs.base import Dialog, LineView
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets import spacing
from neonseek.ui.workers import Worker


def csv_filter() -> str:
    return tr("CSV and text files (*.csv *.tsv *.txt);;All files (*)")


class Tabs(QTabWidget):
    """tabs whose height does not follow the width of the window. a page lays its wrapped
    texts out in the room it gets, the dialog needs only the smallest size of the tabs:
    otherwise it would scroll while everything still fits (D-252)."""

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - qt name
        return False


class AddSourcesDialog(Dialog):
    added = Signal(list)  # AddResult list
    csv_chosen = Signal(str)  # path, the window opens the import dialog
    login_wanted = Signal()

    def __init__(self, ctx: actions.AppContext, theme: ThemeManager, text: str = "", parent=None):
        super().__init__(theme, lambda: tr("Add"), parent)
        self.ctx = ctx
        self.worker: Worker | None = None
        self.resize_scaled(720, 540)
        self.tabs = Tabs()
        self.body.addWidget(self.tabs, 1)

        links = QWidget()
        links_layout = QVBoxLayout(links)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setProperty("role", "waiting")
        self.login = QPushButton(tr("LOG IN TO SPOTIFY"))
        self.login.clicked.connect(self._login)
        notice_row = QHBoxLayout()
        notice_row.addWidget(self.notice, 1)
        notice_row.addWidget(self.login)
        links_layout.addLayout(notice_row)
        hint = QLabel(
            tr(
                "Paste links to playlists, albums or tracks of Spotify, Apple Music, YouTube, "
                "SoundCloud or TIDAL, one per line. A track goes to Single tracks. Only the track "
                "list is "
                "read there, the audio comes from Soulseek. From YouTube and SoundCloud the "
                "artist and title are guessed from the upload titles, so fewer tracks are found."
            )
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        links_layout.addWidget(hint)
        self.urls = QPlainTextEdit(text)
        self.urls.setProperty("role", "input")
        self.urls.setPlaceholderText(
            "https://open.spotify.com/playlist/...\n"
            "https://music.apple.com/.../album/...\n"
            "https://www.youtube.com/playlist?list=...\n"
            "https://soundcloud.com/.../sets/...\n"
            "https://tidal.com/playlist/..."
        )
        links_layout.addWidget(self.urls, 1)
        self.add_button = QPushButton(tr("ADD LINKS"))
        self.add_button.setProperty("primary", True)
        self.add_button.clicked.connect(self._add)
        row = spacing.button_row(self.sizes)
        row.addWidget(self.add_button)
        links_layout.addLayout(row)
        self.tabs.addTab(links, tr("LINKS"))

        csv_tab = QWidget()
        csv_layout = QVBoxLayout(csv_tab)
        csv_hint = QLabel(
            tr(
                "A CSV file with a header row, for example exported from Exportify or Excel, "
                "or a playlist exported from iTunes or the Music app as a .txt file. Spotify is "
                "not needed. You can also drop the file on the window."
            )
        )
        csv_hint.setWordWrap(True)
        csv_hint.setProperty("role", "muted")
        csv_layout.addWidget(csv_hint)
        choose = QPushButton(tr("CHOOSE CSV FILE"))
        choose.setProperty("primary", True)
        choose.clicked.connect(self._choose_csv)
        csv_layout.addWidget(choose)
        csv_layout.addStretch(1)
        self.tabs.addTab(csv_tab, tr("CSV FILE"))

        self.lines = LineView(theme, max_lines=7)
        self.body.addWidget(self.lines)
        close = QPushButton(tr("CLOSE"))
        close.clicked.connect(self.reject)
        bottom = spacing.button_row(self.sizes)
        bottom.addWidget(close)
        self.footer.addLayout(bottom)
        self.refresh()

    def refresh(self) -> None:
        state = actions.spotify_state(self.ctx)
        # without spotify a login button for spotify links, the other links work anyway
        self.notice.setText(
            ""
            if state.ready
            else actions.spotify_needed_message(state)
            + " "
            + tr("Apple Music, YouTube, SoundCloud and TIDAL links work without Spotify.")
        )
        self.notice.setVisible(not state.ready)
        self.login.setVisible(not state.ready)
        self.add_button.setEnabled(not self._busy())

    def _busy(self) -> bool:
        return self.worker is not None

    def _login(self) -> None:
        self.login_wanted.emit()

    def _add(self) -> None:
        texts = [line for line in self.urls.toPlainText().splitlines() if line.strip()]
        if not texts:
            self.lines.add(tr("Paste at least one link."), outcomes.WARN)
            return
        self.add_button.setEnabled(False)
        self.lines.add(tr("Reading {count} links...").format(count=len(texts)))
        self.worker = Worker(
            self.ctx,
            lambda ctx, worker: actions.add_links(ctx, texts, worker.progress.emit),
            self,
        )
        self.worker.progress.connect(self.lines.add)
        self.worker.succeeded.connect(self._added)
        self.worker.failed.connect(lambda message: self.lines.add(message, outcomes.ERROR))
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _finished(self) -> None:
        self.worker = None
        self.refresh()

    def _added(self, results: list) -> None:
        self.lines.add_lines(outcomes.add_lines(results))
        # links that failed stay in the field, so they can be fixed and added again
        kept = (spotify_url.ADDED, spotify_url.EXISTS)
        failed = [r.text for r in results if r.outcome not in kept]
        self.urls.setPlainText("\n".join(failed))
        self.added.emit(results)

    def _choose_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Choose a CSV or .txt file"), "", csv_filter()
        )
        if path:
            self.csv_chosen.emit(path)
            self.accept()
