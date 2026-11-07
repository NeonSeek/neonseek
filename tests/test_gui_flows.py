"""the whole flow of phase 3 through the window, offscreen.

soulseek only through the fake process or sockseek on a mock library, spotify only through the
fixtures. message boxes and modal dialogs are answered by the tests.
"""

from __future__ import annotations

import httpx
import pytest
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox

from conftest import LOGIN_OK, LOGIN_REJECTED
from download_env import needs_sockseek, track
from neonseek import actions, themes, views
from neonseek import secrets as secret_store
from neonseek.db import repo as db
from neonseek.engine import base as engine
from neonseek.sources import ingest
from neonseek.sources.base import CollectionRecord, TrackRecord
from neonseek.ui.dialogs.about import AboutDialog
from neonseek.ui.dialogs.add_sources import AddSourcesDialog
from neonseek.ui.dialogs.csv_import import CsvImportDialog
from neonseek.ui.dialogs.download import ConfirmDownloadDialog
from neonseek.ui.dialogs.report import ReportDialog
from neonseek.ui.dialogs.settings import SettingsDialog
from neonseek.ui.dialogs.wizard import SetupWizard
from neonseek.ui.main_window import MainWindow
from neonseek.ui.theme.loader import ThemeManager

WAIT = 30_000


@pytest.fixture
def boxes(monkeypatch):
    """records message boxes. questions get the answers queued in boxes.answers (yes by
    default)."""

    class Boxes(list):
        answers: list = []

    seen = Boxes()

    def record(kind):
        def box(parent, title, text, *args, **kwargs):
            seen.append((kind, text))
            if kind == "question":
                return seen.answers.pop(0) if seen.answers else QMessageBox.StandardButton.Yes
            return QMessageBox.StandardButton.Ok

        return box

    for kind in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, kind, record(kind))
    return seen


@pytest.fixture
def theme(ctx):
    manager = ThemeManager(ctx.data_dir)
    manager.apply()
    return manager


def _window(qtbot, ctx, theme) -> MainWindow:
    win = MainWindow(ctx, theme)
    qtbot.addWidget(win)
    return win


def _idle(qtbot, win: MainWindow) -> None:
    qtbot.waitUntil(lambda: not win.busy() and not win.downloading(), timeout=WAIT)
    QApplication.processEvents()


def _csv(ctx, name, titles, **values) -> int:
    tracks = [TrackRecord(title=t, artists=("Artist",), duration_ms=60_000) for t in titles]
    record = CollectionRecord(kind="csv", origin="file", name=name, source_ref=f"C:/{name}.csv",
                              snapshot_id=name, tracks=tracks)  # fmt: skip
    cid = ingest.ingest_collection(ctx.repo, record).collection_id
    if values:
        with ctx.repo.transaction():
            ctx.repo.update_collection(cid, values)
    return cid


# first run wizard


def test_wizard_new_account_library_and_theme(qtbot, ctx, theme, fake_sockseek, boxes, tmp_path):
    fake_sockseek(LOGIN_OK)
    wizard = SetupWizard(ctx, theme)
    qtbot.addWidget(wizard)
    page = wizard.soulseek
    assert not page.isComplete()
    page.form.username.setText("  my new name ")
    page.form.connect_button.click()
    qtbot.waitUntil(lambda: not page.form.busy(), timeout=WAIT)
    login = secret_store.get_soulseek_login()
    # the user typed only the name, the app made a long password and showed it nowhere
    assert login.username == "my new name" and len(login.password) == 32
    assert login.password not in page.form.result.toPlainText()
    assert "Created the Soulseek account" in page.form.result.toPlainText()
    assert page.isComplete()

    wizard.library.path.setText("relative")
    assert not wizard.library.validatePage()
    wizard.library.path.setText(str(tmp_path / "Music"))
    assert wizard.library.validatePage()
    assert ctx.settings().library_dir == str(tmp_path / "Music")

    assert wizard.spotify.validatePage()  # skipping spotify is allowed
    items = [wizard.colors.list.item(i) for i in range(wizard.colors.list.count())]
    assert [i.data(256) for i in items] == list(themes.BUILTIN)
    wizard.colors.list.setCurrentItem(items[themes.BUILTIN.index("ice")])
    assert theme.theme.key == "ice"  # previewed without restart
    assert wizard.colors.validatePage()
    assert ctx.settings().theme == "ice"
    assert not actions.needs_setup(ctx)


def test_wizard_taken_name_asks_for_another(qtbot, ctx, theme, fake_sockseek, boxes):
    fake_sockseek(LOGIN_REJECTED)
    wizard = SetupWizard(ctx, theme)
    qtbot.addWidget(wizard)
    form = wizard.soulseek.form
    form.username.setText("taken")
    form.connect_button.click()
    qtbot.waitUntil(lambda: not form.busy(), timeout=WAIT)
    assert 'The name "taken" is already taken' in form.result.toPlainText()
    assert "Nothing was saved" in form.result.toPlainText()
    assert secret_store.get_soulseek_login() is None
    assert not wizard.soulseek.isComplete()


def test_bad_name_is_refused_before_connecting(qtbot, ctx, theme, fake_sockseek, boxes):
    script = fake_sockseek(LOGIN_OK)
    wizard = SetupWizard(ctx, theme)
    qtbot.addWidget(wizard)
    form = wizard.soulseek.form
    form.username.setText("zażółć")
    assert form.problem.isVisibleTo(form)
    form.connect_button.click()
    assert not form.busy() and not script.with_suffix(".args.json").exists()
    assert "plain English letters" in form.result.toPlainText()


def test_own_account_checks_passwords_and_warns(qtbot, ctx, theme, fake_sockseek, boxes):
    fake_sockseek(LOGIN_OK)
    form = SetupWizard(ctx, theme).soulseek.form
    form.own.setChecked(True)
    form.username.setText("samename")
    form.password.setText("samename")
    form.again.setText("other")
    form.connect_button.click()
    assert "different" in form.result.toPlainText() and not form.busy()
    form.password.setText("samename")
    form.again.setText("samename")
    form.connect_button.click()
    qtbot.waitUntil(lambda: not form.busy(), timeout=WAIT)
    text = form.result.toPlainText()
    assert 'Logged in to Soulseek as "samename"' in text and "same as your username" in text
    assert form.password.text() == "" and secret_store.get_soulseek_login().password == "samename"
    # a second login asks before it replaces the saved one
    boxes.answers.append(QMessageBox.StandardButton.No)
    form.password.setText("x")
    form.again.setText("x")
    form.connect_button.click()
    assert boxes[-1][0] == "question" and not form.busy()
    assert secret_store.get_soulseek_login().password == "samename"


# adding sources


def _previewed(qtbot, dialog) -> None:
    qtbot.waitUntil(lambda: not dialog.preview_busy(), timeout=WAIT)


def test_csv_dialog_maps_columns_and_imports_unselected(qtbot, ctx, theme, csv_dir, boxes):
    dialog = CsvImportDialog(ctx, theme, csv_dir / "custom_headers.csv")
    qtbot.addWidget(dialog)
    # the file is read in a worker, the dialog shows that it is reading
    assert dialog.preview_busy() and "Reading the file" in dialog.lines.toPlainText()
    assert not dialog.start.isEnabled()
    _previewed(qtbot, dialog)
    assert not dialog.start.isEnabled()
    assert "Could not find the column with track titles" in dialog.lines.toPlainText()
    header = dialog.header
    title_box = dialog.combos["title"]
    title_box.setCurrentIndex(title_box.findData(header[0]))
    assert not dialog.start.isEnabled()  # import waits for the new preview
    _previewed(qtbot, dialog)
    assert dialog.start.isEnabled() and dialog.table.rowCount() > 0
    dialog.name.setText("My list")
    imported = []
    dialog.imported.connect(imported.append)
    dialog.start.click()
    qtbot.waitUntil(lambda: bool(imported), timeout=WAIT)
    outcome = imported[0]
    row = ctx.repo.get_collection(outcome.collection_id)
    assert row["name"] == "My list" and row["selected"] == 0


def test_csv_dialog_preview_and_unit(qtbot, ctx, theme, csv_dir, boxes):
    dialog = CsvImportDialog(ctx, theme, csv_dir / "exportify_comma_ms.csv")
    qtbot.addWidget(dialog)
    _previewed(qtbot, dialog)
    assert dialog.table.rowCount() == 4 and dialog.start.isEnabled()
    assert dialog.table.item(0, 4).text() == "4:05"
    # two changes while one preview runs: the last one wins
    dialog.unit.setCurrentIndex(dialog.unit.findData("clock"))
    dialog.unit.setCurrentIndex(dialog.unit.findData("s"))
    _previewed(qtbot, dialog)
    assert dialog.table.item(0, 4).text() != "4:05"  # milliseconds read as seconds
    assert "Length unit: s" in dialog.lines.toPlainText()


def test_csv_dialog_with_a_missing_file(qtbot, ctx, theme, tmp_path, boxes):
    dialog = CsvImportDialog(ctx, theme, tmp_path / "gone.csv")
    qtbot.addWidget(dialog)
    _previewed(qtbot, dialog)
    assert "does not exist" in dialog.lines.toPlainText() and not dialog.start.isEnabled()


def test_add_links_with_spotify(qtbot, spotify_ctx, fake_spotify, theme, boxes):
    dialog = AddSourcesDialog(spotify_ctx, theme)
    qtbot.addWidget(dialog)
    assert dialog.add_button.isEnabled() and not dialog.notice.isVisibleTo(dialog)
    album = fake_spotify.ids["single_album"]
    dialog.urls.setPlainText(f"https://open.spotify.com/album/{album}\nhttps://example.com/x")
    added = []
    dialog.added.connect(added.append)
    dialog.add_button.click()
    qtbot.waitUntil(lambda: bool(added), timeout=WAIT)
    text = dialog.lines.toPlainText()
    assert "Added:" in text and "Not added: https://example.com/x" in text
    assert dialog.urls.toPlainText() == "https://example.com/x"  # only the failed one stays
    cid = next(r.collection_id for r in added[0] if r.collection_id)
    assert spotify_ctx.repo.get_collection(cid)["selected"] == 0


def test_add_links_without_spotify_offers_login(qtbot, ctx, theme, boxes):
    dialog = AddSourcesDialog(ctx, theme, "https://open.spotify.com/track/x")
    qtbot.addWidget(dialog)
    assert dialog.notice.isVisibleTo(dialog) and "Client ID" in dialog.notice.text()
    # apple music, youtube and soundcloud links need no spotify, so adding stays possible
    assert dialog.login.isVisibleTo(dialog) and dialog.add_button.isEnabled()
    assert "YouTube" in dialog.notice.text()


def test_paste_and_drop(qtbot, ctx, theme, boxes, monkeypatch, csv_dir):
    win = _window(qtbot, ctx, theme)
    opened = []
    monkeypatch.setattr(AddSourcesDialog, "exec",
                        lambda self: opened.append(self.urls.toPlainText()) or 0)  # fmt: skip
    QGuiApplication.clipboard().setText(
        "see https://open.spotify.com/playlist/0NeonFixturePlaylist01 now"
    )
    win.paste()
    assert opened == ["https://open.spotify.com/playlist/0NeonFixturePlaylist01"]

    paths = []
    monkeypatch.setattr(CsvImportDialog, "exec", lambda self: paths.append(self.path) or 0)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(csv_dir / "comma_seconds.csv"))])

    class Drop:
        def mimeData(self):
            return mime

        def acceptProposedAction(self):
            pass

    win.dropEvent(Drop())
    assert paths == [csv_dir / "comma_seconds.csv"]


# spotify from the window


def test_spotify_group_button_and_sync(qtbot, spotify_ctx, theme, boxes):
    win = _window(qtbot, spotify_ctx, theme)
    assert win.panel.spotify_header().action.text() == "SYNC"
    win.sync(None)
    _idle(qtbot, win)
    groups = views.collection_groups(win.summaries)
    assert groups[0].collections  # playlists of the account in the spotify group
    assert all(not c.selected for c in win.summaries)
    assert "Spotify playlists on your account" in win.log.text()


def test_without_spotify_the_group_offers_login(qtbot, ctx, theme, boxes):
    win = _window(qtbot, ctx, theme)
    assert win.panel.spotify_header().action.text() == "LOG IN"
    win.sync(None)
    assert boxes and "Client ID" in boxes[-1][1] and not win.busy()


# downloads


def _confirm(monkeypatch, answer=QDialog.DialogCode.Accepted):
    plans = []

    def exec_(self):
        plans.append(self)
        return answer

    monkeypatch.setattr(ConfirmDownloadDialog, "exec", exec_)
    return plans


@needs_sockseek
def test_download_from_the_header_only_the_open_collection(qtbot, env, theme, boxes,
                                                          monkeypatch):  # fmt: skip
    env.file("Alpha Artist/First Album/01 - First Song.flac", 60)
    env.file("Beta Artist/Second Album/02 - Second Song.flac", 50)
    one = env.collection("One", [track("Alpha Artist", "First Song", "First Album", 60)])
    two = env.collection("Two", [track("Beta Artist", "Second Song", "Second Album", 50)],
                         selected=1)  # fmt: skip
    win = _window(qtbot, env.ctx, theme)
    win.panel.open_collection(one)
    dialogs = _confirm(monkeypatch)
    win.header.download.click()
    _idle(qtbot, win)
    assert len(dialogs) == 1  # every start asks first
    assert env.state(env.ids(one)[0])["status"] == "done"
    assert env.state(env.ids(two)[0])["status"] == "pending"  # selected, but not open
    assert win.table.model_.rows[0].state == "done"
    assert win.panel._rows[one][1].counter.text() == "1/1"
    log = win.log.text()
    assert "FLAC pass: 1 track to search" in log and "done       Alpha Artist - First Song" in log
    assert not win.log.progress.isVisibleTo(win) and not win.log.cancel.isVisibleTo(win)


@needs_sockseek
def test_download_checked_and_decline(qtbot, env, theme, boxes, monkeypatch):
    env.file("Alpha Artist/First Album/01 - First Song.flac", 60)
    env.file("Beta Artist/Second Album/02 - Second Song.flac", 50)
    one = env.collection("One", [track("Alpha Artist", "First Song", "First Album", 60)])
    two = env.collection("Two", [track("Beta Artist", "Second Song", "Second Album", 50)])
    win = _window(qtbot, env.ctx, theme)
    assert not win.download_checked.isEnabled()
    win.panel._rows[two][1].check.setChecked(True)
    assert win.download_checked.isEnabled()

    _confirm(monkeypatch, QDialog.DialogCode.Rejected)
    win.download_checked.click()
    _idle(qtbot, win)
    assert "Cancelled, nothing was downloaded." in win.log.text()
    assert env.state(env.ids(two)[0])["status"] == "pending"

    _confirm(monkeypatch)
    win.download_checked.click()
    _idle(qtbot, win)
    assert env.state(env.ids(two)[0])["status"] == "done"
    assert env.state(env.ids(one)[0])["status"] == "pending"


@needs_sockseek
def test_cancel_a_running_download(qtbot, env, theme, boxes, monkeypatch):
    env.file("Alpha Artist/First Album/01 - First Song.flac", 60)
    env.flags["flac"] = ["--mock-files-slow"]
    one = env.collection("One", [track("Alpha Artist", "First Song", "First Album", 60)])
    win = _window(qtbot, env.ctx, theme)
    win.panel.open_collection(one)
    _confirm(monkeypatch)
    win.header.download.click()
    qtbot.waitUntil(
        lambda: bool(win.downloading() and win.monitor is not None and win.monitor.changed),
        timeout=WAIT,
    )
    assert win.log.cancel.isVisibleTo(win)
    # closing asks, a no keeps the download running
    boxes.answers.append(QMessageBox.StandardButton.No)
    win.close()
    assert win.downloading()
    win.log.cancel.click()
    _idle(qtbot, win)
    assert "Cancelled. Finished tracks are kept" in win.log.text()
    assert env.state(env.ids(one)[0])["status"] == "pending"


def test_download_without_soulseek_login_says_what_to_do(qtbot, ctx, theme, boxes, monkeypatch):
    cid = _csv(ctx, "mix", ["One"])
    actions.change_setting(ctx, "library_dir", str(ctx.data_dir / "lib"))
    win = _window(qtbot, ctx, theme)
    win.panel.open_collection(cid)
    _confirm(monkeypatch)
    win.header.download.click()
    _idle(qtbot, win)
    assert boxes[-1][0] == "warning" and "No Soulseek login" in boxes[-1][1]


# report, tags, settings, search, themes, about


def test_report_retry(qtbot, ctx, theme, boxes):
    cid = _csv(ctx, "mix", ["One", "Two"])
    one, two = ctx.repo.collection_track_ids(cid)
    ctx.repo.update_download(one, {"status": db.NOT_FOUND, "stage": db.STAGE_FALLBACK,
                                   "last_error": "NoSearchResults"})  # fmt: skip
    ctx.repo.add_failure(two, "AllDownloadsFailed")
    ctx.repo.add_failure(two, "AllDownloadsFailed")
    ctx.repo.add_failure(two, "AllDownloadsFailed")
    dialog = ReportDialog(ctx, theme)
    qtbot.addWidget(dialog)
    assert dialog.table.rowCount() == 2
    assert dialog.table.item(0, 2).text() == "not found"
    assert dialog.table.item(1, 2).text() == "waiting for WAV/AIFF"  # failed 3/3 in flac
    assert not dialog.retry.isEnabled()
    dialog.table.selectRow(0)
    # retry selected asks the window to download the tracks at once (D-131)
    asked = []
    dialog.download_requested.connect(asked.append)
    dialog.retry.click()
    assert asked == [[one]]
    # nothing changes before the user confirms the download
    assert ctx.repo.get_download(one)["status"] == db.NOT_FOUND
    dialog.set_downloading(True)
    assert not dialog.retry.isEnabled() and "library move is running" in dialog.retry.toolTip()
    dialog.set_downloading(False)
    assert dialog.retry.isEnabled()


def test_genre_comment_and_write_tags(qtbot, ctx, theme, boxes):
    cid = _csv(ctx, "mix", ["One"])
    win = _window(qtbot, ctx, theme)
    win.panel.open_collection(cid)
    win.header.genre.setText("  Hard   Techno ")
    win.header.genre.editingFinished.emit()
    row = ctx.repo.get_collection(cid)
    assert row["genre"] == "Hard Techno" and row["comment"] is None
    assert cid in win.tags_pending and win.header.write_tags.property("primary")
    assert "Press WRITE TAGS" in win.log.text()
    win.header.write_tags.click()
    _idle(qtbot, win)
    assert cid not in win.tags_pending
    assert boxes[-1][0] == "information" and "Reload Tag" in boxes[-1][1]


def test_settings_save_and_cancel(qtbot, ctx, theme, boxes):
    dialog = SettingsDialog(ctx, theme)
    qtbot.addWidget(dialog)
    dialog.tags.setCurrentIndex(dialog.tags.findData("fill"))
    dialog.on_missing.setCurrentIndex(dialog.on_missing.findData("not_found"))
    dialog.attempts.setValue(5)
    dialog.theme_box.setCurrentIndex(dialog.theme_box.findData("mono"))
    # the theme applies and saves at once
    assert theme.theme.key == "mono" and ctx.settings().theme == "mono"
    dialog.library.setText(str(ctx.data_dir / "Music"))
    dialog._save()
    settings = ctx.settings()
    assert (settings.tags_from_source, settings.on_missing_flac, settings.max_attempts,
            settings.theme) == ("fill", "not_found", 5, "mono")  # fmt: skip
    again = SettingsDialog(ctx, theme)
    qtbot.addWidget(again)
    again.tags.setCurrentIndex(again.tags.findData("off"))
    again.scale_box.setCurrentIndex(again.scale_box.findData(125))
    assert theme.percent() == 125 and ctx.settings().ui_scale == 125
    again.reject()
    # cancel drops the other changes, theme and text size stay
    assert ctx.settings().tags_from_source == "fill" and ctx.settings().ui_scale == 125


def test_search_in_the_collection_and_the_whole_library(qtbot, ctx, theme, boxes):
    one = _csv(ctx, "one", ["Midnight Drive", "Glass Hearts"])
    _csv(ctx, "two", ["Midnight Club"])
    win = _window(qtbot, ctx, theme)
    win.panel.open_collection(one)
    win.search.setText("midnight")
    assert win.table.visible_count() == 1 and win.count.text() == "1 of 2 tracks"
    win.scope_box.setCurrentIndex(1)
    assert win.header.name.full_text() == "Whole library"
    assert win.table.visible_count() == 2
    win.search.clear()
    assert win.table.visible_count() == 3
    three = _csv(ctx, "three", ["x"])
    win.refresh_collections()
    win.panel.open_collection(three)  # opening a collection leaves the library search
    assert win.scope == "collection" and win.current == three


def test_every_theme_switches_without_restart(qtbot, ctx, theme, boxes):
    win = _window(qtbot, ctx, theme)
    for key in themes.BUILTIN:
        theme.apply(key)
        sheet = QApplication.instance().styleSheet()
        assert theme.theme.colors["bg.base"] in sheet
        win.grab()  # paints with the new theme


def test_about_copies_diagnostics_without_secrets(
    qtbot, ctx, theme, boxes, fake_sockseek, monkeypatch
):
    fake_sockseek([{"out": "3.0.5"}, {"exit": 0}])
    secret_store.set_soulseek_login(secret_store.SoulseekLogin("djname", "Pass-123-word"))
    dialog = AboutDialog(ctx, theme)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: "Sockseek 3.0.5" in dialog.lines.toPlainText(), timeout=WAIT)
    # the logo from the full svg next to the name (D-247)
    assert dialog.logo.width() == 56 and not dialog.logo.pixmap().isNull()
    labels = [label.text() for label in dialog.findChildren(QLabel)]
    assert any("GPL-3.0-or-later" in text for text in labels)
    assert any("Source code of this version" in text for text in labels)
    assert any("LGPL-3.0" in text for text in labels)
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url) or True)
    dialog.licenses.click()
    assert opened and opened[0].toLocalFile().endswith("THIRD_PARTY_LICENSES")
    fake_sockseek([{"out": "3.0.5"}, {"exit": 0}])
    dialog.copy.click()
    qtbot.waitUntil(lambda: "copied" in dialog.lines.toPlainText(), timeout=WAIT)
    text = QGuiApplication.clipboard().text()
    assert "NeonSeek" in text and "Qt" in text
    assert "Pass-123-word" not in text and "djname" not in text


@pytest.mark.parametrize("language", ["en", "pl"])
def test_about_shows_and_opens_the_project_and_donation_pages(
    qtbot, ctx, theme, fake_sockseek, monkeypatch, language
):
    # D-265: the exact addresses, opened by a click, a fake instead of the browser
    theme.set_language(language)
    fake_sockseek([{"out": "3.0.5"}, {"exit": 0}])
    opened: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toString()) or True)  # fmt: skip
    dialog = AboutDialog(ctx, theme)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: "Sockseek 3.0.5" in dialog.lines.toPlainText(), timeout=WAIT)
    expected = ["https://github.com/NeonSeek/neonseek", "https://buymeacoffee.com/neonseek"]
    assert list(dialog.links) == expected
    for (caption, url), label in zip(actions.project_links(), dialog.links.values(), strict=True):
        assert f'href="{url}"' in label.text() and f">{url}</a>" in label.text()
        assert label.isVisibleTo(dialog)
        # a click on the address, past the caption in front of it
        metrics = label.fontMetrics()
        x = label.contentsRect().left() + metrics.horizontalAdvance(f"{caption}: https")
        y = label.contentsRect().top() + metrics.height() // 2
        qtbot.mouseClick(label, Qt.MouseButton.LeftButton, pos=QPoint(x, y))
    assert opened == expected


def test_collection_menu_and_remove(qtbot, ctx, theme, boxes, monkeypatch):
    from neonseek.ui.dialogs.remove import RemoveCollectionDialog

    cid = _csv(ctx, "mix", ["One"])
    win = _window(qtbot, ctx, theme)
    texts = [action.text() for action in win.collection_menu(cid).actions()]
    assert "Import again" in texts and "Remove from NeonSeek..." in texts
    assert "Sync with Spotify" not in texts
    monkeypatch.setattr(RemoveCollectionDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    win.remove(cid)
    assert ctx.repo.get_collection(cid) is None
    assert ctx.repo.count("tracks") == 1  # tracks and download state stay


def test_reimport_of_a_moved_file_offers_to_choose_it(qtbot, ctx, theme, boxes, monkeypatch):
    cid = _csv(ctx, "gone", ["One"])
    win = _window(qtbot, ctx, theme)
    chosen = []
    monkeypatch.setattr(MainWindow, "choose_moved_file", lambda self, c: chosen.append(c))
    boxes.answers.append(QMessageBox.StandardButton.Yes)
    win.reimport(cid)
    _idle(qtbot, win)
    assert chosen == [cid]
    assert "is no longer at" in win.log.text()


def test_live_progress_updates_the_row(qtbot, ctx, theme, boxes):
    from neonseek import outcomes
    from neonseek.pipeline import runner

    cid = _csv(ctx, "mix", ["One"])
    (tid,) = ctx.repo.collection_track_ids(cid)
    win = _window(qtbot, ctx, theme)
    win.panel.open_collection(cid)
    win.monitor = outcomes.RunMonitor()
    win._download_event(runner.PassStarted(1, ("flac",), (tid,)))
    ctx.repo.update_download(tid, {"status": db.DOWNLOADING})
    win._download_event(engine.ItemDownloading(tid, 55))
    assert win.table.model_.rows[0].state_label == "downloading 55%"
    assert win.log.progress.isVisibleTo(win) and win.log.progress.format() == (
        "Pass 1 (FLAC): 0 / 1"
    )


def test_cover_http_is_never_the_network(ctx):
    # the guard from conftest also covers the window: real cover urls cannot be fetched
    with pytest.raises(AssertionError):
        ctx.http_factory().get("https://i.scdn.co/image/x")
    assert isinstance(ctx.http_factory(), httpx.Client)


_QUIT_WITH_THE_WIZARD_OPEN = """
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from neonseek.ui import app
from neonseek.ui.dialogs.wizard import SetupWizard

qapp = app.create_app(["neonseek-gui"])

def quit_now():
    shown = [w for w in QApplication.topLevelWidgets() if isinstance(w, SetupWizard)]
    print("wizard shown:", any(w.isVisible() for w in shown), flush=True)
    # the logo is the icon of every window, the wizard too (D-247)
    sizes = sorted(s.width() for s in shown[0].windowIcon().availableSizes())
    print("window icon:", sizes, flush=True)
    QApplication.quit()

QTimer.singleShot(1500, quit_now)
sys.exit(app.main(["neonseek-gui", "--data-dir", sys.argv[1]]))
"""


def test_quit_while_the_wizard_is_open_ends_the_app(tmp_path):
    # windows asks the app to close for an installer or a log off, qt answers with quit(). a
    # wizard in its own event loop before the main one kept the app alive and the installer
    # waited forever on "closing applications" (D-225). its own process: a real app.exec()
    # and quit() would change qt state for the other tests
    import os
    import subprocess
    import sys

    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen",
           "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring"}  # fmt: skip
    proc = subprocess.run(
        [sys.executable, "-c", _QUIT_WITH_THE_WIZARD_OPEN, str(tmp_path / "data")],
        capture_output=True, text=True, timeout=60, env=env,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "wizard shown: True" in proc.stdout
    assert "window icon: [16, 20, 24, 32, 40, 48, 64, 256]" in proc.stdout
