"""what the cli and the gui show: groups, counters, track rows, states, labels and search."""

from __future__ import annotations

import json

from neonseek import actions, views
from neonseek.db import repo as db
from neonseek.sources import ingest
from neonseek.sources.base import CollectionRecord, TrackRecord


def _add(ctx, kind, origin, name, tracks=None, **values) -> int:
    record = CollectionRecord(kind=kind, origin=origin, name=name, external_id=values.pop(
        "external_id", None), source_ref=values.pop("source_ref", None), snapshot_id="s",
        tracks=tracks)  # fmt: skip
    cid = ingest.ingest_collection(ctx.repo, record).collection_id
    if values:
        with ctx.repo.transaction():
            ctx.repo.update_collection(cid, values)
    return cid


def _track(artist, title, album=None, seconds=None, **extra) -> TrackRecord:
    return TrackRecord(title=title, artists=(artist,) if artist else (), album=album,
                       duration_ms=seconds * 1000 if seconds else None, **extra)  # fmt: skip


def test_groups_follow_kind_and_origin(ctx):
    _add(ctx, "spotify_playlist", "account", "zeta", external_id="p1")
    _add(ctx, "spotify_liked", "account", "Liked Songs", external_id="me")
    _add(ctx, "spotify_playlist", "url", "From a link", external_id="p2")
    _add(ctx, "singles", "builtin", "Single tracks")
    _add(ctx, "spotify_album", "url", "An Album", external_id="a1")
    _add(ctx, "csv", "file", "my list", source_ref="C:/my list.csv")
    groups = views.collection_groups(actions.list_collections(ctx))
    assert [g.key for g in groups] == ["spotify", "links", "csv"]
    assert [g.title for g in groups] == ["SPOTIFY", "LINKS", "CSV"]
    names = [[c.name for c in g.collections] for g in groups]
    # liked songs first, single tracks last, the rest by name
    assert names == [["Liked Songs", "zeta"], ["An Album", "From a link", "Single tracks"],
                     ["my list"]]  # fmt: skip


def test_empty_groups_stay_with_a_hint(ctx):
    _add(ctx, "csv", "file", "list", source_ref="C:/list.csv")
    groups = views.collection_groups(actions.list_collections(ctx))
    assert [(g.key, len(g.collections)) for g in groups] == [("spotify", 0), ("links", 0),
                                                             ("csv", 1)]  # fmt: skip
    assert "+ ADD" in views.empty_group_hint("links")


def test_counter_before_and_after_reading(ctx):
    cid = _add(ctx, "spotify_playlist", "account", "p", external_id="p1", track_count=75)
    unread = actions.list_collections(ctx)[0]
    assert views.collection_counter(unread) == "-/75"
    assert "SYNC" in views.empty_hint(unread)
    with ctx.repo.transaction():
        ctx.repo.update_collection(cid, {"track_count": None})
    assert views.collection_counter(actions.list_collections(ctx)[0]) == "-"

    csv_id = _add(ctx, "csv", "file", "list", [_track("A", "One"), _track("B", "Two")],
                  source_ref="C:/l.csv")  # fmt: skip
    first = ctx.repo.collection_track_ids(csv_id)[0]
    ctx.repo.update_download(first, {"status": db.DONE})
    summary = next(c for c in actions.list_collections(ctx) if c.id == csv_id)
    assert views.collection_counter(summary) == "1/2"


def test_locked_collection_has_a_hint(ctx):
    _add(ctx, "spotify_playlist", "account", "theirs", external_id="p1", is_readable=0)
    summary = actions.list_collections(ctx)[0]
    hint = views.locked_hint(summary)
    assert hint and "CSV" in hint
    assert views.empty_hint(summary) == hint


def test_track_rows_show_state_reason_and_file(ctx):
    cid = _add(ctx, "csv", "file", "list", [
        _track("Alpha", "One", "Album A", 60, cover_url="https://covers.test/1.jpg"),
        TrackRecord(title="Two", artists=("Beta", "Gamma"), duration_ms=None),
        _track("Delta", "Three", seconds=200),
        _track("Eps", "Four", seconds=100),
        _track("Zeta", "Five", seconds=100),
    ], source_ref="C:/l.csv")  # fmt: skip
    one, two, three, four, five = ctx.repo.collection_track_ids(cid)
    ctx.repo.update_download(one, {"status": db.DONE, "format": "flac", "sample_rate": 44100,
                                   "bit_depth": 16, "file_path": "D:/x/one.flac",
                                   "length_verified": 1})  # fmt: skip
    ctx.repo.update_download(two, {"status": db.NOT_FOUND, "last_error": "NoSearchResults"})
    ctx.repo.add_failure(three, "AllDownloadsFailed")
    ctx.repo.update_download(four, {"status": db.NOT_FOUND, "stage": db.STAGE_FALLBACK})
    ctx.repo.update_download(five, {"status": db.DONE, "format": "wav", "sample_rate": 48000,
                                    "bit_depth": 24, "file_path": "D:/x/five.wav",
                                    "length_verified": 0})  # fmt: skip

    rows = views.collection_tracks(ctx, cid)
    assert [r.number for r in rows] == [1, 2, 3, 4, 5]
    r1, r2, r3, r4, r5 = rows
    assert (r1.state, r1.state_label, r1.file_label) == ("done", "done", "FLAC 44.1/16")
    assert r1.length_label == "1:00" and r1.album == "Album A" and r1.label == "Alpha - One"
    assert r1.cover_url == "https://covers.test/1.jpg" and r1.file_path == "D:/x/one.flac"
    # not found in the flac stage waits for the wav/aiff pass, it is not final
    assert r2.state == views.WAITING_FALLBACK and r2.state_label == "waiting for WAV/AIFF"
    assert r2.artists == "Beta, Gamma" and r2.length_label == "-"
    assert r2.reason == "nothing found on Soulseek"
    assert (r3.state, r3.state_label) == ("failed", "failed 1/3")
    assert r3.reason == "every download attempt failed"
    # not found in the fallback stage is final
    assert r4.state == "not_found" and "Download this track only" in r4.hint
    assert r5.file_label == "WAV 48/24 (length not checked, the source has none)"
    assert views.stats_text(rows) == (
        "5 tracks, 2 done, 1 waiting for WAV/AIFF, 1 not found, 1 failed"
    )


def test_waiting_state_follows_the_setting(ctx):
    cid = _add(ctx, "csv", "file", "list", [_track("A", "One")], source_ref="C:/l.csv")
    (tid,) = ctx.repo.collection_track_ids(cid)
    ctx.repo.update_download(tid, {"status": db.NOT_FOUND})
    assert views.collection_tracks(ctx, cid)[0].state == views.WAITING_FALLBACK
    actions.change_setting(ctx, "on_missing_flac", "not_found")
    assert views.collection_tracks(ctx, cid)[0].state == "not_found"


def test_filters_and_search_words():
    def row(state, title="t", artists="", album=""):
        return views.TrackRow(1, 1, title, artists, album, None, None, state, "flac", 0, state,
                              "", "", "", None)  # fmt: skip

    assert views.state_matches("pending", views.FILTER_TODO)
    assert views.state_matches(views.WAITING_FALLBACK, views.FILTER_TODO)
    assert views.state_matches(views.WAITING_FALLBACK, views.FILTER_PROBLEMS)
    assert not views.state_matches("done", views.FILTER_PROBLEMS)
    assert views.state_matches("done", views.FILTER_DONE)
    assert all(views.state_matches(s, views.FILTER_ALL) for s in views.STATES)

    track = row("done", "Zażółć gęślą", "Kestrel, Iris Kwan", "BLOOM")
    assert views.track_matches(track, "")
    assert views.track_matches(track, "zazolc")  # case and accents do not matter
    assert views.track_matches(track, "kwan bloom")  # every word, in any field
    assert not views.track_matches(track, "kwan techno")
    assert views.track_matches(row("done", "Rebound", "KRØMA"), "kroma rebound")


def test_first_collection_prefers_selected_with_tracks(ctx):
    empty = _add(ctx, "spotify_playlist", "account", "a empty", external_id="p1")
    assert views.first_collection(actions.list_collections(ctx)) == empty
    full = _add(ctx, "csv", "file", "full", [_track("A", "One")], source_ref="C:/f.csv")
    assert views.first_collection(actions.list_collections(ctx)) == full
    chosen = _add(ctx, "csv", "file", "chosen", [_track("B", "Two")], source_ref="C:/c.csv",
                  selected=1)  # fmt: skip
    assert views.first_collection(actions.list_collections(ctx)) == chosen
    assert views.first_collection([]) is None


def test_last_run_text(ctx):
    assert views.last_run_text(ctx) is None
    run_id = ctx.repo.start_run()
    ctx.repo.finish_run(run_id, {"ended": "completed", "counts": {"done": 3, "not_found": 1}})
    text = views.last_run_text(ctx)
    assert "completed" in text and "3 done, 1 not found" in text


def test_labels():
    assert views.length_label(None) == "-"
    assert views.length_label(61_400) == "1:01"
    assert views.length_label(3_725_000) == "1:02:05"
    assert views.duration_label(220) == "3 min 40 s"
    assert views.duration_label(60) == "1 min"
    assert views.file_label(None, None, None) == ""
    assert views.file_label("aiff", None, None) == "AIFF ?/?"
    assert views.local_time(None) == ""
    assert views.local_time("not a time") == "not a time"


def test_recover_interrupted_resets_tracks_left_in_progress(ctx):
    cid = _add(ctx, "csv", "file", "l", [_track("A", "One"), _track("B", "Two")],
               source_ref="C:/l.csv")  # fmt: skip
    one, two = ctx.repo.collection_track_ids(cid)
    ctx.repo.update_download(one, {"status": db.SEARCHING})
    ctx.repo.update_download(two, {"status": db.DOWNLOADING, "stage": db.STAGE_FALLBACK,
                                   "attempts": 2})  # fmt: skip
    assert actions.recover_interrupted(ctx) == 2
    states = ctx.repo.track_states([one, two])
    assert states[one]["status"] == "pending"
    assert (states[two]["status"], states[two]["stage"], states[two]["attempts"]) == (
        "pending",
        "fallback",
        2,
    )


def test_recover_interrupted_skips_while_a_download_holds_the_lock(ctx):
    from neonseek.run_lock import RunLock

    cid = _add(ctx, "csv", "file", "l", [_track("A", "One")], source_ref="C:/l.csv")
    (tid,) = ctx.repo.collection_track_ids(cid)
    ctx.repo.update_download(tid, {"status": db.DOWNLOADING})
    with RunLock(ctx.data_dir / "download.lock"):
        assert actions.recover_interrupted(ctx) == 0
    assert ctx.repo.get_download(tid)["status"] == "downloading"


def test_summary_carries_the_collection_image(ctx):
    _add(ctx, "spotify_playlist", "account", "p", external_id="p1",
         image_url="https://img.test/p.jpg")  # fmt: skip
    assert actions.list_collections(ctx)[0].image_url == "https://img.test/p.jpg"
    assert json.dumps(views.collection_kind_label("spotify_playlist", "account"))
