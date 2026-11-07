"""apple music, youtube and soundcloud in the window, offscreen, on the made up answers of
fixtures/web (D-227, D-229)."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from neonseek import actions, views
from neonseek.ui.dialogs.add_sources import AddSourcesDialog
from neonseek.ui.dialogs.edit_track import EditTrackDialog
from neonseek.ui.main_window import MainWindow
from neonseek.ui.theme.loader import ThemeManager

ALBUM_URL = "https://music.apple.com/pl/album/fixture-tunes-ep/1000000001"
YOUTUBE_URL = "https://www.youtube.com/playlist?list=PLfixture00000000000000000000000001"
WAIT = 30_000


@pytest.fixture
def theme(web):
    manager = ThemeManager(web.data_dir)
    manager.apply()
    return manager


@pytest.fixture
def boxes(monkeypatch):
    seen: list[tuple[str, str]] = []
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(
            QMessageBox, kind,
            lambda parent, title, text, *a, k=kind, **kw: seen.append((k, text))
            or QMessageBox.StandardButton.Ok,
        )  # fmt: skip
    return seen


def _window(qtbot, ctx, theme) -> MainWindow:
    win = MainWindow(ctx, theme)
    qtbot.addWidget(win)
    return win


def _idle(qtbot, win: MainWindow) -> None:
    qtbot.waitUntil(lambda: not win.busy(), timeout=WAIT)
    QApplication.processEvents()


def test_links_tab_adds_apple_and_youtube_without_spotify(qtbot, web, theme):
    dialog = AddSourcesDialog(web, theme, f"{ALBUM_URL}\n{YOUTUBE_URL}\n")
    qtbot.addWidget(dialog)
    assert dialog.tabs.tabText(0) == "LINKS"
    assert dialog.add_button.isEnabled()  # no spotify needed for these
    added = []
    dialog.added.connect(added.append)
    dialog._add()
    qtbot.waitUntil(lambda: bool(added), timeout=WAIT)
    results = added[0]
    assert [r.outcome for r in results] == ["added", "added"]
    shown = dialog.lines.toPlainText()
    assert "Added: Neon Fixture Artist - Fixture Tunes - EP" in shown
    assert "unavailable" in shown and "Edit artist and title" in shown
    assert dialog.urls.toPlainText() == ""  # nothing failed, nothing stays in the field


def test_track_menu_edit_corrects_the_names(qtbot, web, theme, boxes, monkeypatch):
    (result,) = actions.add_links(web, [YOUTUBE_URL])
    row = web.repo.find_track_by_source_id("youtube:fixtureVid3")
    win = _window(qtbot, web, theme)
    win.panel.open_collection(result.collection_id)
    menu = win.track_menu([row["id"]])
    items = {a.data(): a for a in menu.actions() if a.data()}
    assert set(items) == set(views.MENU_ITEMS)
    edit = items[views.MENU_EDIT]
    assert edit.isEnabled() and edit.text() == "Edit artist and title..."

    shown = []
    monkeypatch.setattr(EditTrackDialog, "exec", lambda self: shown.append(self) or 0)
    edit.trigger()
    (dialog,) = shown
    assert dialog.artist.text() == "Fixture Singer" and dialog.song.text() == "Third Song"
    dialog.song.setText("   ")
    dialog._save()
    assert "cannot be empty" in dialog.lines.toPlainText()
    dialog.artist.setText("Fixture Singer & Band")
    dialog.song.setText("Third Song (Live)")
    dialog._save()
    track = web.repo.get_track(row["id"])
    assert track["title"] == "Third Song (Live)"
    assert track["artists_json"] == '["Fixture Singer & Band"]'
    assert "is now: Fixture Singer & Band - Third Song (Live)" in win.log.text()

    two = win.track_menu([row["id"], web.repo.find_track_by_source_id("youtube:fixtureVid1")["id"]])
    edit = {a.data(): a for a in two.actions() if a.data()}[views.MENU_EDIT]
    assert not edit.isEnabled() and edit.toolTip() == "Choose one track to correct."


def test_web_collection_syncs_without_spotify(qtbot, web, theme, boxes):
    (result,) = actions.add_links(web, [ALBUM_URL])
    win = _window(qtbot, web, theme)
    win.panel.open_collection(result.collection_id)
    texts = [a.text() for a in win.collection_menu(result.collection_id).actions()]
    assert "Read again from Apple Music" in texts and "Sync with Spotify" not in texts
    assert win.header.sync.isVisibleTo(win)
    win.sync(result.collection_id)
    _idle(qtbot, win)
    assert not boxes  # no question about a spotify login
    assert "no changes on Apple Music" in win.log.text()
