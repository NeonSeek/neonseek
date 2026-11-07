"""layout of the window: text size, row selection, group titles, margins (phase 3 fixes)."""

from __future__ import annotations

import struct
import zlib

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QWidget

from audio_files import PNG_1X1
from neonseek import actions, config, views
from neonseek.sources import ingest
from neonseek.sources.base import CollectionRecord, TrackRecord
from neonseek.ui.main_window import MainWindow
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets import track_table
from neonseek.ui.widgets.collection_header import TAG_FIELD_MAX
from neonseek.ui.widgets.collections_panel import ROW_HEIGHT


@pytest.fixture
def theme(ctx):
    manager = ThemeManager(ctx.data_dir)
    manager.apply()
    return manager


def _window(qtbot, ctx, theme, width=1320, height=840) -> MainWindow:
    win = MainWindow(ctx, theme)
    qtbot.addWidget(win)
    win.resize(width, height)
    win.show()
    qtbot.waitExposed(win)
    QApplication.processEvents()
    return win


def _csv(ctx, name, titles, **values) -> int:
    tracks = [TrackRecord(title=t, artists=("Artist",), duration_ms=60_000) for t in titles]
    record = CollectionRecord(kind="csv", origin="file", name=name, source_ref=f"C:/{name}.csv",
                              snapshot_id=name, tracks=tracks)  # fmt: skip
    cid = ingest.ingest_collection(ctx.repo, record).collection_id
    if values:
        with ctx.repo.transaction():
            ctx.repo.update_collection(cid, values)
    return cid


def _playlists(ctx, count: int) -> list[int]:
    ids = []
    for n in range(count):
        record = CollectionRecord(kind="spotify_playlist", origin="account",
                                  name=f"Playlist {n:02d}", external_id=f"p{n}",
                                  track_count=16)  # fmt: skip
        ids.append(ingest.ingest_collection(ctx.repo, record).collection_id)
    return ids


# text size (owner decision D-115)


def test_text_size_setting_and_values_outside_the_list(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text("ui_scale = 133\n", encoding="utf-8")
    assert config.load_settings(path).ui_scale == 100
    path.write_text("ui_scale = 125\n", encoding="utf-8")
    assert config.load_settings(path).ui_scale == 125
    settings = config.Settings()
    assert config.set_setting(settings, "ui_scale", "150%") == 150
    with pytest.raises(Exception, match="90%, 100%, 110%, 125%, 150%"):
        config.set_setting(settings, "ui_scale", "133")
    assert config.step_ui_scale(150, 1) == 150 and config.step_ui_scale(90, -1) == 90
    assert config.step_ui_scale(100, 1) == 110 and config.step_ui_scale(133, -1) == 90


def test_theme_manager_scale_outside_the_list_is_100(qapp, ctx):
    manager = ThemeManager(ctx.data_dir)
    manager.apply(scale=133)
    assert manager.percent() == 100
    manager.apply(scale=150)
    assert manager.px(12) == 18 and "font-size: 18px" in QApplication.instance().styleSheet()


def test_shortcuts_change_rebuild_and_save_the_text_size(qtbot, ctx, theme):
    cid = _csv(ctx, "mix", ["One", "Two"])
    win = _window(qtbot, ctx, theme)
    win.panel.open_collection(cid)
    sheet = QApplication.instance().styleSheet()
    assert "font-size: 12px" in sheet
    qtbot.keyClick(win, Qt.Key.Key_Plus, Qt.KeyboardModifier.ControlModifier)
    assert ctx.settings().ui_scale == 110 and theme.percent() == 110
    assert QApplication.instance().styleSheet() != sheet
    assert "font-size: 13px" in QApplication.instance().styleSheet()
    qtbot.keyClick(win, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier)
    assert ctx.settings().ui_scale == 125
    # rows, thumbnails and the list follow the text size
    assert win.table.verticalHeader().defaultSectionSize() == theme.px(track_table.ROW_HEIGHT)
    item, row = win.panel._rows[cid]
    assert item.sizeHint().height() == theme.px(ROW_HEIGHT)
    assert row.thumb.width() == theme.px(28)
    qtbot.keyClick(win, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier)
    assert ctx.settings().ui_scale == 110
    qtbot.keyClick(win, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
    assert ctx.settings().ui_scale == 100 and theme.percent() == 100
    assert "Text size: 100%" in win.log.text()


def _overlapping(parent: QWidget) -> list[tuple[str, str]]:
    """visible siblings in one layout that cover each other."""
    found = []
    for widget in parent.findChildren(QWidget):
        layout = widget.layout()
        if layout is None or not widget.isVisible():
            continue
        children = [layout.itemAt(i).widget() for i in range(layout.count())]
        children = [c for c in children if c is not None and c.isVisible()]
        for i, a in enumerate(children):
            for b in children[i + 1 :]:
                if a.geometry().intersected(b.geometry()).width() > 1 and a.geometry(
                ).intersected(b.geometry()).height() > 1:  # fmt: skip
                    found.append((type(a).__name__, type(b).__name__))
    return found


@pytest.mark.parametrize("percent", [100, 150])
def test_nothing_overlaps_and_long_names_are_cut(qtbot, ctx, theme, percent):
    actions.change_setting(ctx, "ui_scale", str(percent))
    theme.apply(scale=percent)
    long_name = "A very long collection name that never fits into the left panel " * 3
    titles = ["A title that is much too long for its column " * 4] + [f"t{n}" for n in range(120)]
    cid = _csv(ctx, long_name, titles)
    win = _window(qtbot, ctx, theme, 1320, 840)
    win.panel.open_collection(cid)
    QApplication.processEvents()
    widest = sorted(
        ((c.minimumSizeHint().width(), type(c).__name__, type(c.parent()).__name__)
         for c in win.findChildren(QWidget) if c.isVisible()),
        reverse=True,
    )[:12]  # fmt: skip
    # the default window size fits with room to spare, also at 150 % with the room around
    # texts (D-194)
    assert win.minimumSizeHint().width() <= 1250, widest
    assert _overlapping(win) == []
    # long texts are cut with an ellipsis instead of widening the window
    row = win.panel._rows[cid][1]
    assert row.name.width() < row.name.fontMetrics().horizontalAdvance(long_name)
    assert win.header.name.width() < win.header.name.fontMetrics().horizontalAdvance(long_name)
    assert win.table.textElideMode() == Qt.TextElideMode.ElideRight


# the window at 2560 px


def test_maximized_width_keeps_the_right_margin(qtbot, ctx, theme):
    cid = _csv(ctx, "mix", ["One"])
    win = _window(qtbot, ctx, theme, 2560, 1392)
    win.panel.open_collection(cid)
    QApplication.processEvents()
    margin = theme.px(12)
    for widget in (win.about_button, win.header, win.count, win.log, win.header.write_tags):
        right = widget.mapTo(win, QPoint(widget.width(), 0)).x()
        assert right <= win.width() - margin, type(widget).__name__


def test_genre_and_comment_do_not_fill_a_wide_window(qtbot, ctx, theme):
    cid = _csv(ctx, "mix", ["One"])
    win = _window(qtbot, ctx, theme, 2560, 1392)
    win.panel.open_collection(cid)
    QApplication.processEvents()
    for field in (win.header.genre, win.header.comment):
        assert field.width() <= theme.px(TAG_FIELD_MAX)
    # write tags stays next to the fields
    gap = win.header.write_tags.x() - (win.header.genre.x() + win.header.genre.width())
    assert gap < theme.px(40)


# table rows


def test_a_click_selects_the_whole_row_and_hover_is_a_row(qtbot, ctx, theme):
    cid = _csv(ctx, "mix", ["One", "Two", "Three"])
    win = _window(qtbot, ctx, theme)
    win.panel.open_collection(cid)
    table = win.table
    rect = table.visualRect(table.model().index(1, track_table.ALBUM))
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    selection = table.selectionModel()
    assert len(selection.selectedRows()) == 1
    assert len(selection.selectedIndexes()) == table.model().columnCount()
    qtbot.mouseMove(table.viewport(), rect.center())
    assert table.hover_row == 1
    sheet = QApplication.instance().styleSheet()
    assert "QTableView::item:hover" not in sheet
    assert f"background-color: {theme.theme.colors['chip.bg']}" in sheet


# left panel


def test_group_title_stays_on_top_while_scrolling(qtbot, ctx, theme):
    ids = _playlists(ctx, 40)
    record = CollectionRecord(kind="spotify_liked", origin="account", name="Liked Songs",
                              external_id="me")  # fmt: skip
    liked = ingest.ingest_collection(ctx.repo, record).collection_id
    win = _window(qtbot, ctx, theme, 1320, 700)
    panel = win.panel
    win.refresh_collections()
    QApplication.processEvents()
    # liked songs is the first collection under the spotify title
    first = panel.list.item(1)
    assert first.data(Qt.ItemDataRole.UserRole) == liked
    panel.list.scrollToTop()
    QApplication.processEvents()
    assert not panel.sticky.isVisible()
    panel.open_collection(ids[30])
    QApplication.processEvents()
    assert panel.sticky.isVisible() and panel.sticky.label.text() == "SPOTIFY"
    assert panel.sticky.geometry().top() == panel.list.viewport().geometry().top()
    # the title always belongs to the group of the first row in view
    panel.list.scrollToBottom()
    QApplication.processEvents()
    top = panel.list.itemAt(QPoint(2, 1))
    group = top.data(Qt.ItemDataRole.UserRole + 1)
    assert panel.sticky.label.text() == views.group_title(group)


def test_start_shows_the_list_from_the_top_when_it_fits(qtbot, ctx, theme):
    _playlists(ctx, 10)
    cid = _csv(ctx, "mix", ["One"], selected=1)
    win = _window(qtbot, ctx, theme, 1320, 1000)
    qtbot.waitUntil(lambda: win.panel.list.verticalScrollBar().value() == 0, timeout=2000)
    assert win.current == cid


def test_counter_says_what_the_dash_means(qtbot, ctx, theme):
    record = CollectionRecord(kind="spotify_playlist", origin="account", name="Unread",
                              external_id="p1", track_count=16)  # fmt: skip
    unread = ingest.ingest_collection(ctx.repo, record).collection_id
    read = _csv(ctx, "mix", ["One"])
    win = _window(qtbot, ctx, theme)
    counter = win.panel._rows[unread][1].counter
    assert counter.text() == "-/16" and "not read yet" in counter.toolTip()
    assert "16" in counter.toolTip() and "SYNC" in counter.toolTip()
    known = win.panel._rows[read][1].counter
    assert known.text() == "0/1" and known.toolTip() == "0 of 1 track downloaded"
    summary = next(s for s in actions.list_collections(ctx) if s.id == unread)
    assert views.counter_hint(summary) == counter.toolTip()


# test data


def test_png_fixture_has_valid_checksums():
    assert PNG_1X1.startswith(b"\x89PNG\r\n\x1a\n")
    pos = 8
    kinds = []
    while pos < len(PNG_1X1):
        (length,) = struct.unpack(">I", PNG_1X1[pos : pos + 4])
        kind = PNG_1X1[pos + 4 : pos + 8]
        data = PNG_1X1[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", PNG_1X1[pos + 8 + length : pos + 12 + length])
        assert crc == zlib.crc32(kind + data), kind
        kinds.append(kind)
        pos += 12 + length
    assert kinds == [b"IHDR", b"IDAT", b"IEND"]


# the mouse wheel (owner decision D-124)


def _wheel(widget: QWidget, down: bool = True) -> None:
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QWheelEvent

    center = QPointF(widget.width() / 2, widget.height() / 2)
    event = QWheelEvent(
        center,
        widget.mapToGlobal(center),
        QPoint(0, 0),
        QPoint(0, -120 if down else 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)


def test_wheel_scrolls_and_never_changes_a_value(qtbot, ctx, theme):
    from PySide6.QtWidgets import QComboBox, QScrollArea, QSpinBox, QVBoxLayout

    area = QScrollArea()
    qtbot.addWidget(area)
    page = QWidget()
    layout = QVBoxLayout(page)
    combo = QComboBox()
    combo.addItems(["one", "two", "three"])
    spin = QSpinBox()
    spin.setRange(1, 10)
    layout.addWidget(combo)
    layout.addWidget(spin)
    layout.addSpacing(3000)
    area.setWidget(page)
    area.resize(300, 200)
    area.show()
    qtbot.waitExposed(area)
    _wheel(combo)
    _wheel(spin)
    _wheel(spin.findChild(QWidget))  # the text field inside the number box
    assert combo.currentIndex() == 0 and spin.value() == 1
    assert area.verticalScrollBar().value() > 0  # the page scrolled instead


def test_wheel_in_settings_keeps_theme_and_text_size(qtbot, ctx, theme):
    from neonseek.ui.dialogs.settings import SettingsDialog

    dialog = SettingsDialog(ctx, theme)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    for box in (dialog.theme_box, dialog.scale_box, dialog.tags, dialog.attempts):
        _wheel(box)
    assert ctx.settings().theme == "neon" and ctx.settings().ui_scale == 100
    assert dialog.tags.currentData() == "overwrite" and dialog.attempts.value() == 3
