"""the soulseek step of the first run wizard after the first test on another computer (D-250).

the log said "Created the Soulseek account ... and saved it" and NEXT stayed grey: the login
was saved while its worker still ran, and nothing asked the page again once the form was idle.
soulseek only through the fake process, nothing here reaches the network.
"""

from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QWizard

from conftest import LOGIN_OK
from neonseek import actions
from neonseek import secrets as secret_store
from neonseek.engine import sockseek
from neonseek.ui.dialogs.accounts import SoulseekAccountForm
from neonseek.ui.dialogs.wizard import SetupWizard
from neonseek.ui.theme.loader import ThemeManager

WAIT = 30_000


@pytest.fixture
def theme(ctx):
    manager = ThemeManager(ctx.data_dir)
    manager.apply()
    return manager


@pytest.fixture(autouse=True)
def _yes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)


def _wizard(qtbot, ctx, theme) -> SetupWizard:
    wizard = SetupWizard(ctx, theme)
    qtbot.addWidget(wizard)
    wizard.show()
    QApplication.processEvents()
    return wizard


def _next(wizard: SetupWizard):
    return wizard.button(QWizard.WizardButton.NextButton)


def test_next_turns_on_once_the_new_account_is_saved(qtbot, ctx, theme, fake_sockseek):
    fake_sockseek(LOGIN_OK)
    wizard = _wizard(qtbot, ctx, theme)
    form = wizard.soulseek.form
    assert not _next(wizard).isEnabled()
    form.username.setText("dj_tester")
    form.connect_button.click()
    assert form.busy() and not _next(wizard).isEnabled()
    assert "at most 30 seconds" in form.result.toPlainText()
    qtbot.waitUntil(lambda: not form.busy(), timeout=WAIT)
    assert 'Created the Soulseek account "dj_tester"' in form.result.toPlainText()
    assert 'Saved account: "dj_tester"' in form.current.text()
    # the bug: this stayed grey
    assert _next(wizard).isEnabled()

    # checking the saved login turns NEXT off while it runs and on again afterwards
    form.test_button.click()
    assert form.busy() and not _next(wizard).isEnabled()
    qtbot.waitUntil(lambda: not form.busy(), timeout=WAIT)
    assert 'Soulseek login works for "dj_tester"' in form.result.toPlainText()
    assert _next(wizard).isEnabled()
    wizard.next()
    assert wizard.currentPage() is wizard.library


def test_second_start_with_a_saved_account_goes_on(qtbot, ctx, theme):
    # the wizard shows again until a library folder is saved too, the account is kept
    secret_store.set_soulseek_login(secret_store.SoulseekLogin("dj_tester", "x" * 32))
    assert actions.needs_setup(ctx)
    wizard = _wizard(qtbot, ctx, theme)
    assert 'Saved account: "dj_tester"' in wizard.soulseek.form.current.text()
    assert wizard.soulseek.form.test_button.isEnabled()
    assert _next(wizard).isEnabled()
    wizard.next()
    assert wizard.currentPage() is wizard.library


def test_a_check_without_answer_ends_with_what_to_do(qtbot, ctx, theme, fake_sockseek,
                                                       monkeypatch):  # fmt: skip
    # sockseek waits for the server forever (a ban looks like that): the check ends at its limit
    monkeypatch.setattr(sockseek, "LOGIN_CHECK_TIMEOUT_S", 1.0)
    fake_sockseek([{"sleep": 60}])
    secret_store.set_soulseek_login(secret_store.SoulseekLogin("dj_tester", "x" * 32))
    wizard = _wizard(qtbot, ctx, theme)
    form = wizard.soulseek.form
    began = time.monotonic()
    form.test_button.click()
    assert form.waiting.isVisibleTo(form)
    qtbot.waitUntil(lambda: not form.busy(), timeout=WAIT)
    assert time.monotonic() - began < 15
    text = form.result.toPlainText()
    assert "Could not connect to Soulseek within 1 seconds" in text
    assert "Check your internet connection" in text
    assert not form.waiting.isVisibleTo(form)
    assert form.test_button.isEnabled() and form.connect_button.isEnabled()
    # the saved login stays, a check that got no answer does not block the wizard
    assert _next(wizard).isEnabled()


def test_the_form_never_waits_past_its_own_limit(qtbot, ctx, theme, monkeypatch):
    # should anything below the window ever hang, the form gives up, says what to do and is
    # usable again. the late answer is not shown
    monkeypatch.setattr(SoulseekAccountForm, "limit_s", 0.5)
    monkeypatch.setattr(SoulseekAccountForm, "grace_s", 0.5)

    def stuck(ctx):
        time.sleep(3)
        return sockseek.LoginCheck(sockseek.LOGIN_OK)

    monkeypatch.setattr(actions, "verify_soulseek_login", stuck)
    secret_store.set_soulseek_login(secret_store.SoulseekLogin("dj_tester", "x" * 32))
    wizard = _wizard(qtbot, ctx, theme)
    form = wizard.soulseek.form
    form.test_button.click()
    assert not _next(wizard).isEnabled()
    qtbot.waitUntil(lambda: not form.busy(), timeout=10_000)
    assert "Could not connect to Soulseek" in form.result.toPlainText()
    assert form.test_button.isEnabled() and _next(wizard).isEnabled()
    qtbot.waitUntil(lambda: not form._late, timeout=10_000)
    QApplication.processEvents()
    assert "login works" not in form.result.toPlainText()


def test_the_log_takes_the_free_room_of_the_step(qtbot, ctx, theme, fake_sockseek):
    fake_sockseek(LOGIN_OK)
    wizard = _wizard(qtbot, ctx, theme)
    wizard.resize(theme.px(720), theme.px(900))
    form = wizard.soulseek.form
    form.username.setText("dj_tester")
    form.connect_button.click()
    qtbot.waitUntil(lambda: not form.busy(), timeout=WAIT)
    QApplication.processEvents()
    log = form.result.geometry()
    room = form.rect()
    # down to the bottom of the form, not a few lines with empty room below
    assert room.bottom() - log.bottom() < theme.px(20)
    assert log.height() > form.result.sizeHint().height()
