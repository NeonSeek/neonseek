"""the window part of phase 3b, offscreen: the track menu in the table and the report,
properties, the recycle bin, the remove window, the library move from settings, the playlist
list at start, the progress bar text and the noir theme. soulseek only on a mock library,
spotify only from fixtures, the recycle bin is the fake one."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from download_env import needs_sockseek, track
from neonseek import actions, config, themes, views
from neonseek.db import repo as db
from neonseek.pipeline import playlists
from neonseek.sources import ingest
from neonseek.sources.base import CollectionRecord, TrackRecord
from neonseek.ui import main_window
from neonseek.ui.dialogs.download import ConfirmDownloadDialog
from neonseek.ui.dialogs.properties import PropertiesDialog
from neonseek.ui.dialogs.remove import RemoveCollectionDialog
from neonseek.ui.dialogs.settings import MOVE_FILES, SettingsDialog
from neonseek.ui.main_window import MainWindow
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets import track_table

WAIT = 30_000


@pytest.fixture
def boxes(monkeypatch):
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


@pytest.fixture
def library(ctx, tmp_path) -> Path:
    folder = tmp_path / "Music"
    actions.change_setting(ctx, "library_dir", str(folder))
    return folder


def _window(qtbot, ctx, theme) -> MainWindow:
    win = MainWindow(ctx, theme)
    qtbot.addWidget(win)
    return win


def _idle(qtbot, win) -> None:
    qtbot.waitUntil(lambda: not win.busy() and not win.downloading(), timeout=WAIT)
    QApplication.processEvents()


def _csv(ctx, name, titles) -> int:
    tracks = [TrackRecord(title=t, artists=("Artist",), duration_ms=60_000) for t in titles]
    record = CollectionRecord(kind="csv", origin="file", name=name, source_ref=f"C:/{name}.csv",
                              snapshot_id=name, tracks=tracks)  # fmt: skip
    return ingest.ingest_collection(ctx.repo, record).collection_id


def _downloaded(ctx, library, track_id, name) -> Path:
    path = library / "tracks" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    ctx.repo.update_download(track_id, {"status": db.DONE, "file_path": str(path),
                                        "format": "flac"})  # fmt: skip
    return path


def _actions(menu) -> dict[str, object]:
    return {a.data(): a for a in menu.actions() if a.data()}


# the track menu (D-125)


def test_right_click_selects_the_row_and_offers_the_menu(qtbot, ctx, theme, library, boxes):
    cid = _csv(ctx, "mix", ["One", "Two"])
    one, two = ctx.repo.collection_track_ids(cid)
    _downloaded(ctx, library, one, "Artist - One.flac")
    win = _window(qtbot, ctx, theme)
    win.panel.open_collection(cid)
    asked = []
    win.table.menu_requested.disconnect()
    win.table.menu_requested.connect(lambda ids, pos: asked.append(ids))
    row_y = win.table.rowViewportPosition(1) + 5
    win.table._menu(QPoint(40, row_y))
    assert asked == [[two]] and win.table.selected_track_ids() == [two]

    items = _actions(win.track_menu([one]))
    assert set(items) == set(views.MENU_ITEMS)
    assert items["download"].text() == "Download this track only"
    assert not items["download"].isEnabled() and items["download"].toolTip()
    assert items["folder"].isEnabled() and items["delete"].isEnabled()
    assert not items["exclude"].isEnabled() and "Delete file" in items["exclude"].toolTip()
    items = _actions(win.track_menu([one, two]))
    assert items["download"].text() == "Download this track only"  # only one is not done
    assert items["exclude"].isEnabled() and items["include"].toolTip()


def test_exclude_and_include_from_the_menu(qtbot, ctx, theme, boxes):
    cid = _csv(ctx, "mix", ["One", "Two"])
    one, _ = ctx.repo.collection_track_ids(cid)
    win = _window(qtbot, ctx, theme)
    win.panel.open_collection(cid)
    _actions(win.track_menu([one]))["exclude"].trigger()
    assert ctx.repo.get_download(one)["status"] == "excluded"
    row = win.table.model_.rows[0]
    assert row.state_label == "excluded"
    tip = win.table.model_.data(win.table.model_.index(0, track_table.STATE),
                                track_table.Qt.ItemDataRole.ToolTipRole)  # fmt: skip
    assert "Include again" in tip
    assert win.panel._rows[cid][1].counter.text() == "0/1"  # excluded tracks do not count
    win.filter.setCurrentIndex(views.FILTERS.index(views.FILTER_EXCLUDED))
    assert win.table.visible_count() == 1
    _actions(win.track_menu([one]))["include"].trigger()
    assert ctx.repo.get_download(one)["status"] == "pending"
    assert "Included 1 track again" in win.log.text()


def test_download_checked_names_the_checkbox(qtbot, ctx, theme, boxes):
    cid = _csv(ctx, "mix", ["One", "Two"])
    win = _window(qtbot, ctx, theme)
    win.panel.open_collection(cid)
    button = win.download_checked
    assert button.text() == "DOWNLOAD CHECKED" and not button.isEnabled()
    assert "checkbox" in button.toolTip() and "Download these tracks only" in button.toolTip()
    win.table.selectAll()  # rows picked in the table are not ticked collections
    assert not button.isEnabled()
    win.panel._rows[cid][1].check.setChecked(True)
    assert button.isEnabled() and "mix" in button.toolTip()


@pytest.mark.parametrize("percent", config.UI_SCALES)
def test_write_tags_sits_in_the_comment_row(qtbot, ctx, theme, percent):
    theme.apply("noir", percent)
    cid = _csv(ctx, "mix", ["One"])
    win = _window(qtbot, ctx, theme)
    win.panel.open_collection(cid)
    win.show()
    qtbot.waitExposed(win)
    qtbot.wait(50)  # the layout settles after the first show
    header = win.header
    button = header.write_tags.geometry()
    comment = header.comment.geometry()
    assert button.top() == comment.top() and button.height() == comment.height()
    assert button.left() > comment.right()  # next to the field, not under the genre


def test_status_chip_and_count_words(qtbot, ctx, theme, boxes):
    cid = _csv(ctx, "mix", ["One"])
    win = _window(qtbot, ctx, theme)
    win.panel.open_collection(cid)
    model = win.table.model_
    assert model.data(model.index(0, track_table.STATE)) == "not downloaded"
    tip = model.data(model.index(0, track_table.STATE), track_table.Qt.ItemDataRole.ToolTipRole)
    assert "Not downloaded yet" in tip and "Press DOWNLOAD" in tip
    assert win.count.text() == "1 of 1 track"


def _status_fits(table, labels) -> list[str]:
    """labels a chip in the status column would cut with an ellipsis."""
    width = table.columnWidth(track_table.STATE)
    metrics = table.fontMetrics()  # what the chip paints with
    delegate = table.state_delegate
    elide = track_table.Qt.TextElideMode.ElideRight
    return [
        label
        for label in labels
        if metrics.elidedText(label, elide, delegate.text_room(metrics, label, width)) != label
    ]


@pytest.mark.parametrize("percent", config.UI_SCALES)
def test_status_column_fits_every_label(qtbot, ctx, theme, percent):
    theme.apply(scale=percent)
    win = _window(qtbot, ctx, theme)
    labels = views.all_state_labels()
    assert "waiting for WAV/AIFF" in labels and "failed 100/100" in labels
    assert _status_fits(win.table, labels) == []


def test_status_column_is_measured_from_the_labels(qtbot, ctx, theme, monkeypatch):
    win = _window(qtbot, ctx, theme)
    before = win.table.columnWidth(track_table.STATE)
    real = views.state_label

    def longer(state, attempts=0, max_attempts=0):
        # a translation can be longer than english, the column follows it
        return real(state, attempts, max_attempts) + " (dłuższa etykieta)"

    monkeypatch.setattr(views, "state_label", longer)
    win.table._rescale()
    assert win.table.columnWidth(track_table.STATE) > before
    assert _status_fits(win.table, views.all_state_labels()) == []


def test_properties_and_show_in_folder(qtbot, ctx, theme, library, boxes, monkeypatch):
    cid = _csv(ctx, "mix", ["One", "Two"])
    one, two = ctx.repo.collection_track_ids(cid)
    path = _downloaded(ctx, library, one, "Artist - One.flac")
    dialog = PropertiesDialog(ctx, theme, [one, two])
    qtbot.addWidget(dialog)
    assert dialog.list.isVisibleTo(dialog) and dialog.list.count() == 2
    assert dialog.values["Path"] == str(path) and dialog.values["Status"] == "done"
    dialog.list.setCurrentRow(1)
    assert dialog.values["Status"] == "not downloaded" and dialog.values["Path"] == "-"
    single = PropertiesDialog(ctx, theme, [two])
    qtbot.addWidget(single)
    assert not single.list.isVisibleTo(single)

    shown = []
    monkeypatch.setattr(main_window, "reveal", lambda p, is_file: shown.append((p, is_file)))
    win = _window(qtbot, ctx, theme)
    items = _actions(win.track_menu([two]))
    assert not items["folder"].isEnabled()
    _actions(win.track_menu([two, one]))["folder"].trigger()
    assert shown == [(path, True)]


def test_delete_file_from_the_menu(qtbot, ctx, theme, library, boxes, recycle_bin):
    keep = _csv(ctx, "keep", ["One"])
    cid = _csv(ctx, "mix", ["One", "Two"])
    one, _ = ctx.repo.collection_track_ids(cid)
    path = _downloaded(ctx, library, one, "Artist - One.flac")
    actions.write_playlists(ctx, [keep, cid])
    win = _window(qtbot, ctx, theme)
    win.panel.open_collection(cid)
    boxes.answers.append(QMessageBox.StandardButton.No)
    _actions(win.track_menu([one]))["delete"].trigger()
    assert path.exists() and boxes[-1][0] == "question"
    assert "Move 1 file to the Windows Recycle Bin?" in boxes[-1][1]
    _actions(win.track_menu([one]))["delete"].trigger()
    assert not path.exists() and recycle_bin.names() == ["Artist - One.flac"]
    assert ctx.repo.get_download(one)["status"] == "excluded"
    for collection in (keep, cid):
        text = playlists.playlist_path(ctx.repo, collection, library).read_text(encoding="utf-8")
        assert "Artist - One.flac" not in text


def test_delete_file_without_a_bin_says_no(qtbot, ctx, theme, library, boxes, recycle_bin):
    cid = _csv(ctx, "mix", ["One"])
    (one,) = ctx.repo.collection_track_ids(cid)
    path = _downloaded(ctx, library, one, "Artist - One.flac")
    recycle_bin.without_bin(library)
    win = _window(qtbot, ctx, theme)
    _actions(win.track_menu([one]))["delete"].trigger()
    assert path.exists() and boxes[-1][0] == "warning" and "USB stick" in boxes[-1][1]
    assert not [b for b in boxes if b[0] == "question"]


# the remove window (D-127)


def test_remove_window_has_the_option_off_by_default(qtbot, ctx, theme, library, boxes,
                                                      recycle_bin, monkeypatch):  # fmt: skip
    cid = _csv(ctx, "mix", ["One"])
    (one,) = ctx.repo.collection_track_ids(cid)
    path = _downloaded(ctx, library, one, "Artist - One.flac")
    actions.write_playlists(ctx, [cid])
    win = _window(qtbot, ctx, theme)
    dialog = win.remove_dialog(actions.remove_plan(ctx, [cid]))
    qtbot.addWidget(dialog)
    assert not dialog.delete_files.isChecked() and dialog.delete_files.isEnabled()
    assert "(2 files)" in dialog.delete_files.text()
    win.download = object()  # a download runs: the option is off and says why
    busy = win.remove_dialog(actions.remove_plan(ctx, [cid]))
    qtbot.addWidget(busy)
    assert not busy.delete_files.isEnabled()
    assert "library move is running" in busy.delete_files.toolTip()
    win.download = None

    def tick_and_accept(self):
        self.delete_files.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(RemoveCollectionDialog, "exec", tick_and_accept)
    win.remove(cid)
    assert ctx.repo.get_collection(cid) is None and not path.exists()
    assert sorted(recycle_bin.names()) == ["Artist - One.flac", "mix.m3u8"]


# the library folder in settings (D-128)


def test_settings_move_the_library(qtbot, ctx, theme, library, boxes, monkeypatch, tmp_path):
    cid = _csv(ctx, "mix", ["One"])
    (one,) = ctx.repo.collection_track_ids(cid)
    _downloaded(ctx, library, one, "Artist - One.flac")
    new = tmp_path / "New Music"
    win = _window(qtbot, ctx, theme)
    asked = []

    def answer(self, plan):
        asked.append(len(plan.files))
        return MOVE_FILES

    def save(self):
        self.library.setText(str(new))
        self._save()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SettingsDialog, "ask_move", answer)
    monkeypatch.setattr(SettingsDialog, "exec", save)
    win.open_settings()
    _idle(qtbot, win)
    assert asked == [1] and ctx.settings().library_dir == str(new)
    assert Path(ctx.repo.get_download(one)["file_path"]).parent == new / "tracks"
    assert "The library folder is now" in win.log.text() and not win.moving

    win.download = object()
    locked = SettingsDialog(ctx, theme, win, downloading=True)
    qtbot.addWidget(locked)
    assert not locked.library.isEnabled()
    win.download = None


# the playlist list at start (D-130)


def test_spotify_list_is_read_at_start(qtbot, spotify_ctx, fake_spotify, theme, boxes):
    win = _window(qtbot, spotify_ctx, theme)
    qtbot.waitUntil(lambda: "Spotify playlists on your account" in win.log.text(), timeout=WAIT)
    _idle(qtbot, win)
    summaries = spotify_ctx.repo.list_collections()
    assert summaries and all(not s.selected for s in summaries)
    assert not [r for r in fake_spotify.requests if "/items" in r and not r.endswith("limit=1")]
    assert not boxes


def test_no_internet_at_start_is_no_error(qtbot, spotify_ctx, theme, boxes):
    def offline(request):
        raise httpx.ConnectError("no internet", request=request)

    spotify_ctx.http_factory = lambda: httpx.Client(transport=httpx.MockTransport(offline))
    spotify_ctx._http = None
    win = _window(qtbot, spotify_ctx, theme)
    qtbot.waitUntil(lambda: "Could not refresh your Spotify playlists" in win.log.text(),
                    timeout=WAIT)  # fmt: skip
    _idle(qtbot, win)
    assert not boxes


def test_no_list_at_start_when_turned_off(qtbot, spotify_ctx, fake_spotify, theme, boxes):
    actions.change_setting(spotify_ctx, "sync_on_start", "false")
    win = _window(qtbot, spotify_ctx, theme)
    qtbot.wait(200)
    assert not win.busy() and not fake_spotify.requests


# the progress bar (D-133)


def _luminance(color: QColor) -> float:
    return themes.luminance(color.name().upper())


@pytest.mark.parametrize("key", themes.BUILTIN)
def test_progress_text_reads_on_the_filled_part(qtbot, ctx, theme, key):
    theme.apply(key)
    win = _window(qtbot, ctx, theme)
    bar = win.log.progress
    win.log.set_progress(5, 5, "Pass 1 (FLAC): 5 / 5")
    bar.resize(theme.px(260), theme.px(20))
    fill, empty = bar.text_colors()
    image = bar.grab().toImage()
    # the text color of the filled part is really painted in the middle of the bar
    found = {
        image.pixelColor(x, y).name().upper()
        for x in range(image.width() // 4, image.width() * 3 // 4)
        for y in range(image.height())
    }
    assert fill.name().upper() in found
    gradient = themes.progress_gradient(theme.theme)
    assert min(themes.contrast(fill.name().upper(), c) for c in gradient) >= 3.0
    assert empty == theme.color("text.primary")


def test_mono_bar_is_not_white_on_white(qtbot, ctx, theme):
    theme.apply("mono")
    win = _window(qtbot, ctx, theme)
    fill, _ = win.log.progress.text_colors()
    assert _luminance(fill) < 0.2  # dark text on the white fill


def test_noir_switches_without_restart(qtbot, ctx, theme, boxes):
    win = _window(qtbot, ctx, theme)
    theme.apply("noir")
    assert theme.theme.key == "noir"
    assert theme.theme.colors["accent.primary"] in QApplication.instance().styleSheet()
    win.grab()


# the report (D-131)


def test_report_menu_comes_from_the_window(qtbot, ctx, theme, boxes):
    cid = _csv(ctx, "mix", ["One"])
    (one,) = ctx.repo.collection_track_ids(cid)
    ctx.repo.update_download(one, {"status": db.NOT_FOUND, "last_error": "NoSearchResults"})
    win = _window(qtbot, ctx, theme)
    win.open_report()
    report = win.report_dialog
    assert report is not None and not report.isModal() and report.table.rowCount() == 1
    win.open_report()
    assert win.report_dialog is report  # one report at a time
    asked = []
    # the window would open the real menu here, which waits for a click
    report.menu_requested.disconnect()
    report.menu_requested.connect(lambda ids, pos: asked.append(ids))
    report._menu(QPoint(10, report.table.rowViewportPosition(0) + 5))
    assert asked == [[one]]
    _actions(win.track_menu([one]))["exclude"].trigger()
    assert report.table.rowCount() == 0  # the report follows the change
    report.close()
    assert win.report_dialog is None


def _inside(widget, dialog) -> bool:
    """the whole widget lies inside the dialog."""
    top_left = widget.mapTo(dialog, QPoint(0, 0))
    return dialog.rect().contains(QRect(top_left, widget.size()))


@pytest.mark.parametrize(
    ("percent", "area"),
    [
        (125, QRect(0, 0, 2560, 1392)),  # the owner: 2560 px monitors, 125 %
        (150, QRect(0, 0, 1366, 728)),  # a small laptop screen with the biggest text
    ],
)
def test_report_buttons_are_always_on_screen(qtbot, ctx, theme, monkeypatch, percent, area):
    from neonseek.ui.dialogs.base import Dialog

    theme.apply("noir", percent)
    monkeypatch.setattr(Dialog, "available_area", lambda self: area)
    cid = _csv(ctx, "mix", [f"Song {n}" for n in range(40)])
    for tid in ctx.repo.collection_track_ids(cid):
        ctx.repo.update_download(tid, {"status": db.NOT_FOUND})
    win = _window(qtbot, ctx, theme)
    win.open_report()
    report = win.report_dialog
    qtbot.waitExposed(report)
    qtbot.wait(50)
    frame = report.frameGeometry()
    assert area.contains(frame), (frame, area)
    for button in (report.export, report.retry):
        assert button.isVisible() and _inside(button, report)
    report.close()


def test_a_disabled_button_keeps_its_frame(qtbot, ctx, theme):
    from neonseek.ui.dialogs.report import ReportDialog

    theme.apply("noir")
    dialog = ReportDialog(ctx, theme)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert not dialog.retry.isEnabled()
    image = dialog.retry.grab().toImage()
    # the top edge of the button is drawn in the border color, so it reads as a button
    edge = image.pixelColor(image.width() // 2, 0).name().upper()
    assert edge == theme.theme.colors["border"]


@needs_sockseek
def test_retry_selected_downloads_at_once(qtbot, env, theme, boxes, monkeypatch):
    env.file("Alpha Artist/First Album/01 - First Song.flac", 60)
    cid = env.collection("One", [track("Alpha Artist", "First Song", "First Album", 60),
                                 track("Beta", "Other", "Album", 50)])  # fmt: skip
    first, other = env.ids(cid)
    env.ctx.repo.update_download(first, {"status": db.NOT_FOUND, "stage": db.STAGE_FALLBACK})
    win = _window(qtbot, env.ctx, theme)
    plans = []
    monkeypatch.setattr(ConfirmDownloadDialog, "exec",
                        lambda self: plans.append(self) or QDialog.DialogCode.Accepted)  # fmt: skip
    win.open_report()
    report = win.report_dialog
    report.table.selectRow(0)
    report.retry.click()
    assert win.downloading() and not report.retry.isEnabled()
    assert "library move is running" in report.retry.toolTip()
    _idle(qtbot, win)
    assert len(plans) == 1  # the same summary first
    assert env.state(first)["status"] == "done" and env.state(other)["status"] == "pending"
    assert report.table.rowCount() == 0 and "Track in this download" in win.log.text()
    report.close()
