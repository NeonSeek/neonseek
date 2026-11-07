"""windows on small screens and corner resizing (D-252).

a laptop with 1366x768 at 125 % windows scale gives a window 1092x576 logical pixels
without the taskbar, 1920x1080 at 150 % gives 1280x688. qt lays windows out in logical
pixels, so these areas stand for the real screens. offscreen windows have no frame, the
frame of windows 11 is added by hand.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QWidget

from neonseek import actions
from neonseek import secrets as secret_store
from neonseek.sources import ingest
from neonseek.sources.base import CollectionRecord, TrackRecord
from neonseek.ui.dialogs import base
from neonseek.ui.dialogs.about import AboutDialog
from neonseek.ui.dialogs.add_sources import AddSourcesDialog
from neonseek.ui.dialogs.csv_import import CsvImportDialog
from neonseek.ui.dialogs.download import ConfirmDownloadDialog
from neonseek.ui.dialogs.edit_track import EditTrackDialog
from neonseek.ui.dialogs.properties import PropertiesDialog
from neonseek.ui.dialogs.remove import RemoveCollectionDialog
from neonseek.ui.dialogs.report import ReportDialog
from neonseek.ui.dialogs.settings import SettingsDialog
from neonseek.ui.dialogs.wizard import SetupWizard
from neonseek.ui.main_window import LOG_HEIGHT, MIN_TRACK_ROWS, MainWindow
from neonseek.ui.theme.loader import ThemeManager

SCREENS = {
    "1366x768 at 125 %": QRect(0, 0, 1092, 576),
    "1920x1080 at 150 %": QRect(0, 0, 1280, 688),
}
# title bar and borders of a windows 11 window, in logical pixels
FRAME_W, FRAME_H = 2, 32


@pytest.fixture
def theme(ctx):
    manager = ThemeManager(ctx.data_dir)
    manager.apply()
    return manager


def _collection(ctx) -> tuple[int, list[int]]:
    tracks = [TrackRecord(title=f"Song {n}", artists=("Artist",), duration_ms=60_000)
              for n in range(30)]  # fmt: skip
    record = CollectionRecord(kind="csv", origin="file", name="Mix", source_ref="C:/mix.csv",
                              snapshot_id="mix", tracks=tracks)  # fmt: skip
    cid = ingest.ingest_collection(ctx.repo, record).collection_id
    ids = [row[0] for row in ctx.repo.conn.execute(
        "SELECT track_id FROM collection_tracks WHERE collection_id = ?", (cid,))]  # fmt: skip
    return cid, ids


def _windows(ctx, theme, csv_dir, window):
    cid, ids = _collection(ctx)
    plan = actions.DownloadPlan([(cid, "Mix")], ids[:2], 2, 0, 15, 21, [], 220)
    yield "settings", SettingsDialog(ctx, theme, window)
    yield "about", AboutDialog(ctx, theme, window)
    yield "add", AddSourcesDialog(ctx, theme, "", window)
    yield "csv", CsvImportDialog(ctx, theme, csv_dir / "custom_headers.csv", None, window)
    yield "report", ReportDialog(ctx, theme, window)
    yield "confirm", ConfirmDownloadDialog(theme, plan, window)
    yield "edit", EditTrackDialog(ctx, theme, ids[0], window)
    yield "remove", RemoveCollectionDialog(theme, actions.remove_plan(ctx, [cid]), False, window)
    yield "properties", PropertiesDialog(ctx, theme, ids[:3], window)


def _footer_widgets(layout) -> list[QWidget]:
    found = []
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item.widget() is not None:
            found.append(item.widget())
        elif item.layout() is not None:
            found += _footer_widgets(item.layout())
    return found


def _fits(widget: QWidget, area: QRect) -> bool:
    return (widget.minimumWidth() + FRAME_W <= area.width()
            and widget.minimumHeight() + FRAME_H <= area.height())  # fmt: skip


def _check(qtbot, widget, name, monkeypatch) -> None:
    qtbot.addWidget(widget)
    widget.show()
    QApplication.processEvents()
    QApplication.processEvents()
    # a corner drag is free: no height tied to the width of the window
    assert not widget.hasHeightForWidth(), name
    for screen, area in SCREENS.items():
        assert _fits(widget, area), (name, screen, widget.minimumSize().toTuple())
        monkeypatch.setattr(base, "available_area", lambda _w, a=area: a)
        base.fit_to_screen(widget)
        QApplication.processEvents()
        assert widget.width() <= area.width() and widget.height() <= area.height(), (name, screen)
        assert area.contains(widget.frameGeometry()), (name, screen, widget.frameGeometry())


def test_wizard_fits_small_screens_and_resizes_freely(qtbot, ctx, theme, monkeypatch):
    wizard = SetupWizard(ctx, theme)
    for index in range(len(wizard.pageIds())):
        wizard.show()
        wizard.setCurrentId(wizard.pageIds()[index])
        wizard.resize(theme.px(720), theme.px(620))
        _check(qtbot, wizard, f"wizard page {index + 1}", monkeypatch)
    # smaller than it opens with, down to a sensible size
    assert wizard.minimumWidth() <= theme.px(720 * base.MIN_SHARE)
    assert wizard.minimumHeight() <= theme.px(620 * base.MIN_SHARE)
    assert wizard.minimumWidth() >= theme.px(400)


def test_main_window_and_dialogs_fit_small_screens(qtbot, ctx, theme, csv_dir, monkeypatch):
    secret_store.set_soulseek_login(secret_store.SoulseekLogin("demo", "not-a-real-password"))
    window = MainWindow(ctx, theme, list_at_start=False)
    window.resize(1320, 840)
    _check(qtbot, window, "main window", monkeypatch)
    for name, dialog in _windows(ctx, theme, csv_dir, window):
        _check(qtbot, dialog, name, monkeypatch)
        # the buttons stay below the part that scrolls
        buttons = _footer_widgets(dialog.footer)
        assert buttons, name
        assert not any(dialog.room.isAncestorOf(button) for button in buttons), name
        dialog.done(0)
        QApplication.processEvents()


def test_a_minimum_bigger_than_the_screen_gives_way(qtbot, ctx, theme, monkeypatch):
    # a big text size on a small screen: the window still fits, its content scrolls
    dialog = SettingsDialog(ctx, theme)
    qtbot.addWidget(dialog)
    dialog.room.setMinimumSize(900, 700)
    area = QRect(0, 0, 800, 500)
    monkeypatch.setattr(base, "available_area", lambda _w: area)
    dialog.show()
    QApplication.processEvents()
    base.fit_to_screen(dialog)
    QApplication.processEvents()
    assert area.contains(dialog.frameGeometry()), dialog.frameGeometry()


@pytest.mark.parametrize("language", ["en", "pl"])
def test_nothing_scrolls_at_the_size_a_window_opens_with(qtbot, ctx, theme, csv_dir, language):
    # the scroll area is only for small screens: at its own size every window fits
    theme.set_language(language)
    secret_store.set_soulseek_login(secret_store.SoulseekLogin("demo", "not-a-real-password"))
    window = MainWindow(ctx, theme, list_at_start=False)
    qtbot.addWidget(window)
    for name, dialog in _windows(ctx, theme, csv_dir, window):
        qtbot.addWidget(dialog)
        dialog.show()
        QApplication.processEvents()
        QApplication.processEvents()
        assert dialog.room.verticalScrollBar().maximum() == 0, (language, name)
        dialog.done(0)
    wizard = SetupWizard(ctx, theme)
    qtbot.addWidget(wizard)
    wizard.show()
    for index, page in enumerate(wizard.pageIds(), start=1):
        wizard.setCurrentId(page)
        QApplication.processEvents()
        scroll = wizard.currentPage().findChild(base.FreeScroll)
        assert scroll.verticalScrollBar().maximum() == 0, (language, f"wizard {index}")


# rows of tracks on a low window (D-258)

# the whole screen in logical pixels, and what windows leaves to a window on it: without the
# taskbar and the frame
LOW_WINDOWS = {
    "1366x768 at 125 %, whole screen": (1093, 614),
    "1366x768 at 125 %, fitted": (1092 - FRAME_W, 576 - FRAME_H),
    "1920x1080 at 150 %, whole screen": (1280, 720),
    "1920x1080 at 150 %, fitted": (1280 - FRAME_W, 688 - FRAME_H),
}


def _main_window(qtbot, ctx, theme, size=(1320, 840)) -> MainWindow:
    cid, _ = _collection(ctx)
    window = MainWindow(ctx, theme, list_at_start=False)
    qtbot.addWidget(window)
    window.resize(*size)
    window.show()
    window.panel.open_collection(cid)
    _settle(window, size)
    return window


def _settle(window: MainWindow, size) -> None:
    window.resize(*size)
    for _ in range(3):
        QApplication.processEvents()


@pytest.mark.parametrize("screen", LOW_WINDOWS)
def test_a_low_window_shows_at_least_eight_rows(qtbot, ctx, theme, screen):
    window = _main_window(qtbot, ctx, theme, LOW_WINDOWS[screen])
    assert window.size().toTuple() == LOW_WINDOWS[screen]
    assert window.table.whole_rows_shown() >= MIN_TRACK_ROWS, screen
    # the one line header keeps the name and the buttons
    assert window.header.compact
    for widget in (window.header.name, window.header.download, window.header.open_folder):
        assert widget.isVisibleTo(window), screen
    assert not window.header.tags_box.isVisibleTo(window)


def test_the_default_size_keeps_the_whole_header_and_the_log(qtbot, ctx, theme):
    window = _main_window(qtbot, ctx, theme)
    assert not window.header.compact and not window.log.collapsed
    assert window.header.tags_box.isVisibleTo(window)
    assert window.log.height() == theme.px(LOG_HEIGHT)
    assert window.table.whole_rows_shown() >= MIN_TRACK_ROWS


def test_what_the_window_folded_opens_again(qtbot, ctx, theme):
    window = _main_window(qtbot, ctx, theme, (1090, 544))
    assert window.header.compact and window.log.collapsed
    _settle(window, (1320, 840))
    assert not window.header.compact and not window.log.collapsed
    assert window.log.height() == theme.px(LOG_HEIGHT)


def test_what_was_folded_by_hand_stays_folded(qtbot, ctx, theme):
    window = _main_window(qtbot, ctx, theme)
    window.log.toggle.click()
    window.header.toggle.click()
    QApplication.processEvents()
    assert window.log.collapsed and window.header.compact
    for size in ((1090, 544), (1320, 900)):
        _settle(window, size)
        assert window.log.collapsed and window.header.compact, size


def test_the_header_opened_by_hand_on_a_low_window(qtbot, ctx, theme):
    # genre and comment can be edited on a small screen too, the tracks get less room
    window = _main_window(qtbot, ctx, theme, (1093, 614))
    assert window.header.compact
    window.header.toggle.click()
    QApplication.processEvents()
    assert not window.header.compact and window.header.genre.isVisibleTo(window)
    # until the next change of the height
    _settle(window, (1093, 600))
    assert window.header.compact


def test_the_height_of_the_header_is_known_without_switching(qtbot, ctx, theme):
    window = _main_window(qtbot, ctx, theme, (1320, 900))
    header = window.header
    for library in (False, True):
        if library:
            window.scope_box.setCurrentIndex(1)
            QApplication.processEvents()
        for compact in (False, True, False):
            expected = header.height_in(compact), header.height_in(not compact)
            header.set_compact(compact)
            for _ in range(3):
                QApplication.processEvents()
            assert header.height() == expected[0], (library, compact)
            assert header.height_in(not compact) == expected[1], (library, compact)
