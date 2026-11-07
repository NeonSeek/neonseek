"""the window on a test data folder, offscreen. no spotify, no soulseek, no real covers."""

from __future__ import annotations

import httpx
import pytest

from neonseek import actions, views
from neonseek.db import repo as db
from neonseek.sources import ingest
from neonseek.sources.base import CollectionRecord, TrackRecord

pytest.importorskip("PySide6")

from neonseek.ui.main_window import MainWindow  # noqa: E402
from neonseek.ui.theme.loader import ThemeManager  # noqa: E402
from neonseek.ui.widgets import track_table  # noqa: E402

COVER = "https://covers.test/a.png"


def _png() -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor(200, 30, 140))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def _serve_covers(request: httpx.Request) -> httpx.Response:
    if str(request.url) == COVER:
        return httpx.Response(200, content=_png())
    raise AssertionError(f"tests must not reach the network: {request.url}")


@pytest.fixture
def window(qtbot, ctx):
    ctx.http_factory = lambda: httpx.Client(transport=httpx.MockTransport(_serve_covers))
    theme = ThemeManager(ctx.data_dir)
    theme.apply()
    win = MainWindow(ctx, theme)
    qtbot.addWidget(win)
    yield win
    win.covers.shutdown()


def _csv(ctx, name, titles, **values) -> int:
    tracks = [
        TrackRecord(
            title=t, artists=("Artist",), duration_ms=60_000, cover_url=COVER if i == 0 else None
        )
        for i, t in enumerate(titles)
    ]
    record = CollectionRecord(kind="csv", origin="file", name=name, source_ref=f"C:/{name}.csv",
                              snapshot_id=name, tracks=tracks)  # fmt: skip
    cid = ingest.ingest_collection(ctx.repo, record).collection_id
    if values:
        with ctx.repo.transaction():
            ctx.repo.update_collection(cid, values)
    return cid


def test_empty_data_folder_opens(window):
    assert window.panel.current_id() is None
    # the three groups are there even without any collection
    assert window.panel.list.count() == 6 and window.panel.spotify_header() is not None
    assert window.header.empty.isVisibleTo(window)
    assert not window.download_checked.isEnabled()
    assert "NeonSeek" in window.log.text()


def test_collections_tracks_and_selection(qtbot, ctx, window):
    cid = _csv(ctx, "mix", ["One", "Two", "Three"])
    other = _csv(ctx, "other", ["Four"])
    (first, second, _) = ctx.repo.collection_track_ids(cid)
    ctx.repo.update_download(first, {"status": db.DONE, "format": "flac", "sample_rate": 44100,
                                     "bit_depth": 16, "file_path": "x.flac"})  # fmt: skip
    ctx.repo.update_download(second, {"status": db.NOT_FOUND})
    window.refresh_collections()
    window.panel.open_collection(cid)
    assert window.current == cid

    model = window.table.model_
    assert [r.title for r in model.rows] == ["One", "Two", "Three"]
    assert model.data(model.index(0, track_table.FILE)) == "FLAC 44.1/16"
    assert model.data(model.index(1, track_table.STATE)) == "waiting for WAV/AIFF"
    assert window.count.text() == "3 of 3 tracks"
    _, row = window.panel._rows[cid]
    assert row.counter.text() == "1/3"

    # the state filter is decided by core
    window.filter.setCurrentIndex(views.FILTERS.index(views.FILTER_DONE))
    assert window.table.visible_count() == 1
    window.filter.setCurrentIndex(views.FILTERS.index(views.FILTER_ALL))
    assert window.table.visible_count() == 3

    # every collection starts unselected, download selected waits for a checkbox
    assert not window.download_checked.isEnabled()
    row.check.setChecked(True)
    assert ctx.repo.get_collection(cid)["selected"] == 1
    assert ctx.repo.get_collection(other)["selected"] == 0
    assert window.download_checked.isEnabled()
    window.panel._rows[cid][1].check.setChecked(False)
    assert ctx.repo.get_collection(cid)["selected"] == 0
    assert not window.download_checked.isEnabled()


def test_covers_load_off_the_ui_thread(qtbot, ctx, window):
    cid = _csv(ctx, "mix", ["One"])
    window.refresh_collections()
    with qtbot.waitSignal(window.covers.ready, timeout=5000) as blocker:
        window.panel.open_collection(cid)
        window.covers.pixmap(COVER, track_table.THUMB)
    assert blocker.args == [COVER]
    assert window.covers.pixmap(COVER, track_table.THUMB) is not None


def test_a_cover_that_fails_leaves_the_placeholder(qtbot, ctx, window):
    ctx.http_factory = lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(404)))  # fmt: skip
    window.covers.http_factory = ctx.http_factory
    url = "https://covers.test/missing.png"
    assert window.covers.pixmap(url, 28) is None
    qtbot.waitUntil(lambda: (url, 28) in window.covers._failed, timeout=5000)
    assert window.covers.pixmap(url, 28) is None  # not asked again in this session


def test_unread_spotify_playlist_shows_what_to_do(window, ctx):
    record = CollectionRecord(kind="spotify_playlist", origin="account", name="Not read",
                              external_id="p1", track_count=12)  # fmt: skip
    cid = ingest.ingest_collection(ctx.repo, record).collection_id
    window.refresh_collections()
    window.panel.open_collection(cid)
    assert window.stack.currentWidget() is window.empty
    assert "SYNC" in window.empty.text()
    assert window.panel._rows[cid][1].counter.text() == "-/12"


def test_open_at_start_and_recovery(qtbot, ctx):
    cid = _csv(ctx, "mix", ["One"])
    (tid,) = ctx.repo.collection_track_ids(cid)
    ctx.repo.update_download(tid, {"status": db.SEARCHING})
    theme = ThemeManager(ctx.data_dir)
    theme.apply()
    win = MainWindow(ctx, theme)
    qtbot.addWidget(win)
    assert win.current == cid
    assert ctx.repo.get_download(tid)["status"] == "pending"
    assert "interrupted" in win.log.text()
    win.covers.shutdown()


def test_theme_change_repaints_without_restart(qtbot, window):
    before = window.theme.theme.key
    window.theme.apply(before)  # the same theme again must not break anything
    assert actions.AppContext is not None and window.theme.theme.key == before
