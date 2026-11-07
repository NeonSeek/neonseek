"""soulseek and spotify account forms, shared by the first run wizard and the settings."""

from __future__ import annotations

import time

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from neonseek import actions, outcomes
from neonseek import secrets as secret_store
from neonseek.engine import sockseek
from neonseek.i18n import tr
from neonseek.spotify import auth as spotify_auth
from neonseek.ui.dialogs.base import LineView
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets import spacing
from neonseek.ui.widgets.common import LiveTexts, SectionLabel
from neonseek.ui.workers import Worker

SPOTIFY_DASHBOARD = "https://developer.spotify.com/dashboard"


def _wrap(text: str = "", role: str = "") -> QLabel:
    """a wrapped text with room between its lines (D-194)."""
    return spacing.Hint(text, role=role)


class SoulseekAccountForm(QWidget):
    """new account (name only, the app makes the password) or the user's own account.
    both test the connection and save the login only after it worked (D-053, D-057)."""

    saved = Signal(str)  # username
    # a check started or ended: the wizard asks isComplete again only after the form is idle,
    # never while the worker that saved the login is still finishing (D-250)
    state_changed = Signal()

    # the check ends itself after sockseek's limit. this is the last line of defence, should
    # anything below the window ever hang: the form reports it and is usable again
    limit_s = sockseek.LOGIN_CHECK_TIMEOUT_S
    grace_s = 15.0

    def __init__(self, ctx: actions.AppContext, theme: ThemeManager, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.worker: Worker | None = None
        # workers given up after the limit: kept until they end, a thread is never destroyed
        # while it runs, and what they report is no longer shown
        self._late: list[Worker] = []
        self._started = 0.0
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._show_waiting)
        texts = LiveTexts(theme, self)
        sizes = spacing.LiveSizes(theme, self)
        layout = QVBoxLayout(self)
        sizes.layout(layout, margins=0, spacing=spacing.ROW)

        self.current = QLabel()
        self.current.setProperty("role", "muted")
        layout.addWidget(self.current)

        self.new = texts.text(QRadioButton(), lambda: tr("Create a new account (recommended)"))
        self.own = texts.text(QRadioButton(), lambda: tr("Use my own account"))
        self.new.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.new)
        group.addButton(self.own)
        # the description under the choice, in line with its text
        new_hint = texts.text(
            _wrap(role="muted"),
            lambda: tr(
                "You choose the name. NeonSeek makes a long password and keeps it in Windows "
                "Credential Manager, you never need to type it. The first login creates the "
                "account. A separate account only for NeonSeek is best."
            ),
        )
        choice = QVBoxLayout()
        sizes.layout(choice, margins=0, spacing=spacing.HINT)
        choice.addWidget(self.new)
        choice.addWidget(new_hint)
        layout.addLayout(choice)
        sizes.call(lambda: new_hint.setContentsMargins(self._indent(), 0, 0, 0))
        layout.addWidget(self.own)

        # labels in one column with room to their fields (D-194)
        grid = QGridLayout()
        sizes.layout(grid, margins=(0, 4, 0, 0), horizontal=spacing.LABEL, vertical=spacing.ROW)
        self.username = texts.placeholder(QLineEdit(), lambda: tr("your Soulseek username"))
        self.username.setMaxLength(60)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.again = QLineEdit()
        self.again.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_label = texts.text(SectionLabel(""), lambda: tr("PASSWORD"))
        self.again_label = texts.text(SectionLabel(""), lambda: tr("REPEAT"))
        grid.addWidget(texts.text(SectionLabel(""), lambda: tr("USERNAME")), 0, 0)
        grid.addWidget(self.username, 0, 1)
        grid.addWidget(self.password_label, 1, 0)
        grid.addWidget(self.password, 1, 1)
        grid.addWidget(self.again_label, 2, 0)
        grid.addWidget(self.again, 2, 1)
        layout.addLayout(grid)
        self.problem = _wrap("")
        self.problem.setProperty("role", "waiting")
        self.problem.setVisible(False)
        layout.addWidget(self.problem)

        buttons = spacing.button_row(sizes, stretch_first=False)
        self.connect_button = texts.text(QPushButton(), lambda: tr("CONNECT AND SAVE"))
        self.connect_button.setProperty("primary", True)
        self.connect_button.clicked.connect(self._connect)
        self.test_button = texts.text(QPushButton(), lambda: tr("TEST SAVED LOGIN"))
        self.test_button.clicked.connect(self._test)
        self.waiting = QLabel()
        self.waiting.setProperty("role", "muted")
        self.waiting.setVisible(False)
        buttons.addWidget(self.connect_button)
        buttons.addWidget(self.test_button)
        buttons.addWidget(self.waiting)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        # the log takes the free room of the window, the spacer only while it is empty
        self.result = LineView(theme)
        layout.addWidget(self.result, 1)
        layout.addStretch(0)

        self.new.toggled.connect(self._mode)
        self.username.textChanged.connect(self._check_name)
        self._mode()
        self.refresh()
        theme.language_changed.connect(self.refresh)
        theme.language_changed.connect(self._check_name)

    def _indent(self) -> int:
        """where the text of a radio button starts."""
        style = self.new.style()
        indicator = style.pixelMetric(style.PixelMetric.PM_ExclusiveIndicatorWidth, None, self.new)
        gap = style.pixelMetric(style.PixelMetric.PM_RadioButtonLabelSpacing, None, self.new)
        return max(0, indicator + gap)

    def refresh(self) -> None:
        login = actions.soulseek_login(self.ctx)
        self.current.setText(
            tr('Saved account: "{user}"').format(user=login.username)
            if login
            else tr("No Soulseek account saved yet.")
        )
        self.test_button.setEnabled(login is not None and not self.busy())

    def busy(self) -> bool:
        # until the finished handler ran, so results are shown before the form is idle
        return self.worker is not None

    def _mode(self) -> None:
        own = self.own.isChecked()
        for widget in (self.password, self.again, self.password_label, self.again_label):
            widget.setVisible(own)

    def _check_name(self) -> None:
        text = self.username.text()
        problem = secret_store.username_problem(text.strip()) if text else None
        self.problem.setText(problem or "")
        self.problem.setVisible(bool(problem))

    def _run(self, fn, on_done) -> None:
        self.connect_button.setEnabled(False)
        self.test_button.setEnabled(False)
        self.result.add(actions.soulseek_check_started_message())
        worker = Worker(self.ctx, fn, self)
        self.worker = worker
        # a worker given up after the limit reports nothing any more
        worker.succeeded.connect(lambda result: worker is self.worker and on_done(result))
        worker.failed.connect(
            lambda message: worker is self.worker and self.result.add(message, outcomes.ERROR)
        )
        worker.finished.connect(lambda: self._finished(worker))
        self._started = time.monotonic()
        self._show_waiting()
        self.waiting.setVisible(True)
        self._tick.start()
        worker.start()
        self.state_changed.emit()

    def _show_waiting(self) -> None:
        elapsed = int(time.monotonic() - self._started)
        self.waiting.setText(tr("Waiting for Soulseek: {seconds} s").format(seconds=elapsed))
        if self.worker is not None and elapsed >= self.limit_s + self.grace_s:
            self._give_up()

    def _give_up(self) -> None:
        self._late.append(self.worker)
        self.result.add(
            tr("Error: {message}").format(message=sockseek.start_timeout_message(self.limit_s)),
            outcomes.ERROR,
        )
        self._idle()

    def _finished(self, worker: Worker) -> None:
        if worker in self._late:
            self._late.remove(worker)
            worker.deleteLater()
            return
        if worker is self.worker:
            self._idle()

    def _idle(self) -> None:
        self.worker = None
        self._tick.stop()
        self.waiting.setVisible(False)
        self.connect_button.setEnabled(True)
        self.refresh()
        self.state_changed.emit()

    def _connect(self) -> None:
        username = self.username.text().strip()
        problem = secret_store.username_problem(username)
        if problem:
            self.result.add(problem, outcomes.ERROR)
            return
        new = self.new.isChecked()
        password = self.password.text()
        if not new:
            if not password:
                self.result.add(tr("Enter a Soulseek password."), outcomes.ERROR)
                return
            if password != self.again.text():
                self.result.add(tr("The two passwords are different. Type them again."),
                                outcomes.ERROR)  # fmt: skip
                return
        saved = actions.soulseek_login(self.ctx)
        if saved is not None:
            answer = QMessageBox.question(
                self,
                "NeonSeek",
                tr("This replaces the saved Soulseek login for {user}. Continue?").format(
                    user=saved.username
                ),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if new:
            self._run(lambda ctx, _: actions.create_soulseek_account(ctx, username),
                      lambda outcome: self._saved(outcome, True))  # fmt: skip
        else:
            self._run(
                lambda ctx, _: actions.use_own_soulseek_account(ctx, username, password),
                lambda outcome: self._saved(outcome, False),
            )
        self.password.clear()
        self.again.clear()

    def _saved(self, outcome: actions.SoulseekLoginOutcome, new: bool) -> None:
        self.result.add_lines(outcomes.soulseek_saved_lines(outcome, new))
        self.saved.emit(outcome.login.username)

    def _test(self) -> None:
        login = actions.soulseek_login(self.ctx)
        if login is None:
            return
        self._run(
            lambda ctx, _: actions.verify_soulseek_login(ctx),
            lambda check: self.result.add_lines(
                [outcomes.soulseek_check_line(check, login.username)]
            ),
        )


class SpotifyForm(QWidget):
    """the optional spotify step: own developer app, client id, login (PLAN.md 6.1)."""

    changed = Signal()

    def __init__(self, ctx: actions.AppContext, theme: ThemeManager, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.worker: Worker | None = None
        texts = LiveTexts(theme, self)
        sizes = spacing.LiveSizes(theme, self)
        layout = QVBoxLayout(self)
        sizes.layout(layout, margins=0, spacing=spacing.ROW)
        layout.addWidget(
            texts.text(
                _wrap(role="muted"),
                lambda: tr(
                    "Optional. Without Spotify you import track lists from CSV files. Spotify "
                    "needs Premium on the account that creates the developer app."
                ),
            )
        )
        steps = QGridLayout()
        sizes.layout(steps, margins=0, horizontal=spacing.LABEL, vertical=spacing.ROW)
        steps.addWidget(
            texts.text(
                _wrap(),
                lambda: tr("1. Create an app in the Spotify developer dashboard (Web API)."),
            ),
            0,
            0,
        )
        dashboard = texts.text(QPushButton(), lambda: tr("OPEN DASHBOARD"))
        dashboard.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(SPOTIFY_DASHBOARD)))
        steps.addWidget(dashboard, 0, 1)
        steps.addWidget(
            texts.text(_wrap(), lambda: tr("2. Add this Redirect URI to the app, exactly:")), 1, 0
        )
        copy = texts.text(QPushButton(), lambda: tr("COPY"))
        copy.clicked.connect(self._copy_uri)
        steps.addWidget(copy, 1, 1)
        uri = QLineEdit(spotify_auth.REDIRECT_URI)
        uri.setReadOnly(True)
        steps.addWidget(uri, 2, 0, 1, 2)
        steps.addWidget(
            texts.text(_wrap(), lambda: tr("3. Copy the Client ID of the app here and save it.")),
            3,
            0,
        )
        self.client_id = QLineEdit(actions.spotify_client_id(ctx))
        texts.placeholder(self.client_id, lambda: tr("Client ID"))
        save = texts.text(QPushButton(), lambda: tr("SAVE"))
        save.clicked.connect(self._save_id)
        steps.addWidget(self.client_id, 4, 0)
        steps.addWidget(save, 4, 1)
        steps.setColumnStretch(0, 1)
        layout.addLayout(steps)

        row = spacing.button_row(sizes, stretch_first=False)
        self.state = QLabel()
        self.login = texts.text(QPushButton(), lambda: tr("LOG IN TO SPOTIFY"))
        self.login.setProperty("primary", True)
        self.login.clicked.connect(self.start_login)
        self.logout = texts.text(QPushButton(), lambda: tr("LOG OUT"))
        self.logout.clicked.connect(self._logout)
        self.cancel = texts.text(QPushButton(), lambda: tr("CANCEL LOGIN"))
        self.cancel.clicked.connect(self._cancel)
        row.addWidget(self.state, 1)
        row.addWidget(self.login)
        row.addWidget(self.logout)
        row.addWidget(self.cancel)
        layout.addLayout(row)
        self.result = LineView(theme)
        layout.addWidget(self.result, 1)
        layout.addStretch(0)
        self.refresh()
        theme.language_changed.connect(self.refresh)

    def busy(self) -> bool:
        return self.worker is not None

    def refresh(self) -> None:
        state = actions.spotify_state(self.ctx)
        if state.ready:
            self.state.setText(tr("Logged in to Spotify."))
        elif state.client_id:
            self.state.setText(tr("Client ID saved, not logged in yet."))
        else:
            self.state.setText(tr("Spotify is not set up."))
        busy = self.busy()
        self.login.setVisible(not state.logged_in and not busy)
        self.login.setEnabled(state.client_id)
        self.logout.setVisible(state.logged_in and not busy)
        self.cancel.setVisible(busy)

    def _copy_uri(self) -> None:
        QGuiApplication.clipboard().setText(spotify_auth.REDIRECT_URI)
        self.result.add(tr("Redirect URI copied."))

    def _save_id(self) -> None:
        actions.set_spotify_client_id(self.ctx, self.client_id.text())
        self.result.add(tr("Spotify Client ID saved.") if self.client_id.text().strip()
                        else tr("Spotify Client ID removed."))  # fmt: skip
        self.refresh()
        self.changed.emit()

    def save_if_changed(self) -> None:
        if self.client_id.text().strip() != actions.spotify_client_id(self.ctx):
            self._save_id()

    def start_login(self) -> None:
        self.save_if_changed()
        if not actions.spotify_state(self.ctx).client_id:
            self.result.add(actions.spotify_needed_message(actions.spotify_state(self.ctx)),
                            outcomes.ERROR)  # fmt: skip
            return

        def login(ctx, worker):
            flow = actions.start_spotify_login(ctx)
            worker.cancel_hook = flow.cancel
            worker.progress.emit(flow.url)
            return actions.finish_spotify_login(ctx, flow)

        self.worker = Worker(self.ctx, login, self)
        self.worker.progress.connect(self._open_browser)
        self.worker.succeeded.connect(self._logged_in)
        self.worker.failed.connect(lambda message: self.result.add(message, outcomes.ERROR))
        self.worker.finished.connect(self._login_finished)
        self.worker.start()
        self.refresh()

    def _open_browser(self, url: str) -> None:
        self.result.add(tr("Log in to Spotify in your browser. If it does not open, go to:"))
        self.result.add(url)
        QDesktopServices.openUrl(QUrl(url))

    def _logged_in(self, me: dict) -> None:
        self.result.add(
            tr("Logged in to Spotify as {name}.").format(name=me.get("display_name") or me["id"]),
            outcomes.DONE,
        )

    def _login_finished(self) -> None:
        self.worker = None
        self.refresh()
        self.changed.emit()

    def _cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()

    def _logout(self) -> None:
        actions.spotify_logout(self.ctx)
        self.result.add(
            tr("Logged out of Spotify. The saved login was removed from this computer.")
        )
        self.refresh()
        self.changed.emit()
