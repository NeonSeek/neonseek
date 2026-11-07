"""tracks in core: excluded status, delete file into the (fake) recycle bin, removing a
collection with its files, properties, state words and the track menu (D-125 to D-129)."""

from __future__ import annotations

from pathlib import Path

import pytest

from neonseek import actions, config, outcomes, recycle, views
from neonseek.db import repo as db
from neonseek.pipeline import playlists, policy
from neonseek.run_lock import RunLock
from neonseek.sources import ingest
from neonseek.sources.base import CollectionRecord, TrackRecord


def _csv(ctx, name, titles, **values) -> int:
    tracks = [TrackRecord(title=t, artists=("Artist",), duration_ms=60_000) for t in titles]
    record = CollectionRecord(kind="csv", origin="file", name=name, source_ref=f"C:/{name}.csv",
                              snapshot_id=name, tracks=tracks)  # fmt: skip
    cid = ingest.ingest_collection(ctx.repo, record).collection_id
    if values:
        with ctx.repo.transaction():
            ctx.repo.update_collection(cid, values)
    return cid


@pytest.fixture
def library(ctx, tmp_path) -> Path:
    folder = tmp_path / "Music"
    actions.change_setting(ctx, "library_dir", str(folder))
    return folder


def _downloaded(ctx, library: Path, track_id: int, name: str, data: bytes = b"audio") -> Path:
    """a track as if step place had put its file into the library."""
    path = library / "tracks" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    values = {"status": db.DONE, "file_path": str(path), "format": "flac", "sample_rate": 44100,
              "bit_depth": 16, "source_user": "uploader", "source_path": "@@x\\a.flac"}  # fmt: skip
    ctx.repo.update_download(track_id, values)
    return path


def _state(ctx, track_id):
    return ctx.repo.get_download(track_id)


# excluded (D-126)


def test_exclude_keeps_the_status_and_include_brings_it_back(ctx):
    cid = _csv(ctx, "mix", ["One", "Two", "Three", "Four"])
    one, two, three, four = ctx.repo.collection_track_ids(cid)
    ctx.repo.update_download(two, {"status": db.NOT_FOUND, "stage": db.STAGE_FALLBACK,
                                   "last_error": "NoSearchResults"})  # fmt: skip
    ctx.repo.add_failure(three, "AllDownloadsFailed")
    ctx.repo.update_download(four, {"status": db.DONE, "file_path": "x.flac"})
    changed = actions.exclude_tracks(ctx, [one, two, three, four])
    assert changed == [one, two, three]  # a downloaded track is not excluded
    assert [_state(ctx, t)["status"] for t in (one, two, three, four)] == [
        "excluded", "excluded", "excluded", "done"]  # fmt: skip
    assert _state(ctx, two)["excluded_from"] == "not_found"
    assert actions.include_tracks(ctx, [one, two, three, four]) == [one, two, three]
    back = _state(ctx, two)
    # nothing is searched again: the known result comes back with its stage and attempts
    assert (back["status"], back["stage"], back["last_error"]) == (
        "not_found", "fallback", "NoSearchResults")  # fmt: skip
    assert _state(ctx, three)["attempts"] == 1 and _state(ctx, one)["status"] == "pending"
    assert _state(ctx, one)["excluded_from"] is None


def test_excluded_tracks_are_never_downloaded(ctx):
    settings = config.Settings()
    assert not policy.wants_pass1(db.EXCLUDED, db.STAGE_FLAC, 0, settings)
    assert not policy.wants_pass2(db.EXCLUDED, db.STAGE_FALLBACK, 0, settings)
    assert not policy.waiting_for_fallback(db.EXCLUDED, db.STAGE_FLAC, 9, settings)
    cid = _csv(ctx, "mix", ["One"])
    (tid,) = ctx.repo.collection_track_ids(cid)
    ctx.repo.update_download(tid, {"stage": db.STAGE_FALLBACK})
    ctx.repo.exclude([tid])
    # on_missing_flac = not_found leaves excluded tracks alone
    assert ctx.repo.revert_fallback() == 0
    assert _state(ctx, tid)["status"] == "excluded"


def test_nothing_changes_tracks_during_a_download(ctx, library):
    cid = _csv(ctx, "mix", ["One"])
    (tid,) = ctx.repo.collection_track_ids(cid)
    with RunLock(ctx.data_dir / "download.lock"):
        for call in (actions.exclude_tracks, actions.include_tracks,
                     actions.delete_track_files):  # fmt: skip
            with pytest.raises(actions.LibraryBusyError, match="library move is running"):
                call(ctx, [tid])
        with pytest.raises(actions.LibraryBusyError):
            actions.remove_collections_and_files(ctx, [cid])
        with pytest.raises(actions.LibraryBusyError):
            actions.move_library(ctx, str(library.parent / "New"))
    assert _state(ctx, tid)["status"] == "pending"


def test_unknown_track_ids_say_where_to_find_them(ctx):
    with pytest.raises(actions.CollectionError, match="no track with id 99"):
        actions.exclude_tracks(ctx, [99])


def test_state_for_its_own_download():
    own = policy.state_for_own_download
    assert own(db.NOT_FOUND, db.STAGE_FALLBACK, 2, None) == ("pending", "flac", 0)
    assert own(db.FAILED, db.STAGE_FLAC, 3, None) == ("pending", "flac", 0)
    assert own(db.PENDING, db.STAGE_FALLBACK, 1, None) == ("pending", "fallback", 1)
    assert own(db.EXCLUDED, db.STAGE_FLAC, 0, "not_found") == ("pending", "flac", 0)
    assert own(db.EXCLUDED, db.STAGE_FALLBACK, 1, "pending") == ("pending", "fallback", 1)
    assert own(db.DONE, db.STAGE_FLAC, 0, None) is None


# delete file (D-127)


def test_delete_file_goes_to_the_recycle_bin_and_excludes(ctx, library, recycle_bin):
    one_id = _csv(ctx, "one", ["Song"])
    two_id = _csv(ctx, "two", ["Song", "Other"])
    song, other = ctx.repo.collection_track_ids(two_id)
    assert ctx.repo.collection_track_ids(one_id) == [song]  # the same track in both lists
    path = _downloaded(ctx, library, song, "Artist - Song.flac")
    _downloaded(ctx, library, other, "Artist - Other.flac")
    actions.write_playlists(ctx, [one_id, two_id])
    plan = actions.delete_plan(ctx, [song])
    assert [ref.path for ref in plan.files] == [path] and plan.problem is None
    assert "Move 1 file to the Windows Recycle Bin?" in outcomes.delete_question(plan)
    outcome = actions.delete_track_files(ctx, [song])
    assert recycle_bin.names() == ["Artist - Song.flac"] and not path.exists()
    state = _state(ctx, song)
    assert (state["status"], state["excluded_from"], state["stage"], state["attempts"]) == (
        "excluded", "pending", "flac", 0)  # fmt: skip
    assert state["file_path"] is None and state["format"] is None
    # the playlists of every collection of the track are written again at once
    assert {p.name for p in outcome.playlists} == {"one.m3u8", "two.m3u8"}
    text = playlists.playlist_path(ctx.repo, two_id, library).read_text(encoding="utf-8")
    assert "Song.flac" not in text and "Other.flac" in text
    assert "Moved 1 file to the Recycle Bin" in outcomes.texts(outcomes.delete_lines(outcome))


def test_delete_file_without_a_recycle_bin_deletes_nothing(ctx, library, recycle_bin):
    cid = _csv(ctx, "mix", ["One"])
    (tid,) = ctx.repo.collection_track_ids(cid)
    path = _downloaded(ctx, library, tid, "Artist - One.flac")
    recycle_bin.without_bin(library)
    plan = actions.delete_plan(ctx, [tid])
    assert "Recycle Bin" in plan.problem and "USB" in plan.problem
    with pytest.raises(recycle.RecycleError, match="Nothing was deleted"):
        actions.delete_track_files(ctx, [tid])
    assert path.exists() and _state(ctx, tid)["status"] == "done" and not recycle_bin.items


def test_delete_file_touches_only_files_neonseek_placed(ctx, library, recycle_bin, tmp_path):
    cid = _csv(ctx, "mix", ["Mine", "Elsewhere", "Gone", "Waiting"])
    mine, elsewhere, gone, waiting = ctx.repo.collection_track_ids(cid)
    _downloaded(ctx, library, mine, "Artist - Mine.flac")
    outside = tmp_path / "Desktop" / "Elsewhere.flac"
    outside.parent.mkdir()
    outside.write_bytes(b"x")
    ctx.repo.update_download(elsewhere, {"status": db.DONE, "file_path": str(outside)})
    missing = _downloaded(ctx, library, gone, "Artist - Gone.flac")
    missing.unlink()
    plan = actions.delete_plan(ctx, [mine, elsewhere, gone, waiting])
    assert [r.track_id for r in plan.files] == [mine]
    assert [r.track_id for r in plan.outside] == [elsewhere]
    assert [r.track_id for r in plan.gone] == [gone] and plan.without_file == [waiting]
    outcome = actions.delete_track_files(ctx, [mine, elsewhere, gone, waiting])
    assert outside.exists() and recycle_bin.names() == ["Artist - Mine.flac"]
    assert _state(ctx, elsewhere)["status"] == "done"
    assert _state(ctx, gone)["status"] == "excluded"  # its file was gone, it is excluded too
    assert _state(ctx, waiting)["status"] == "pending"
    assert len(outcome.recycled) == 1 and len(outcome.gone) == 1


def test_a_locked_file_stays_and_is_reported(ctx, library, recycle_bin):
    cid = _csv(ctx, "mix", ["One", "Two"])
    one, two = ctx.repo.collection_track_ids(cid)
    locked = _downloaded(ctx, library, one, "Artist - One.flac")
    _downloaded(ctx, library, two, "Artist - Two.flac")
    recycle_bin.lock(locked)
    outcome = actions.delete_track_files(ctx, [one, two])
    assert locked.exists() and _state(ctx, one)["status"] == "done"
    assert _state(ctx, two)["status"] == "excluded"
    assert outcome.failed and "Not deleted" in outcomes.texts(outcomes.delete_lines(outcome))


# remove a collection with its files (D-127)


def test_remove_with_files_takes_only_tracks_of_no_other_collection(ctx, library, recycle_bin):
    keep_id = _csv(ctx, "keep", ["Shared"])
    gone_id = _csv(ctx, "gone", ["Shared", "Only here"])
    shared, only = ctx.repo.collection_track_ids(gone_id)
    shared_file = _downloaded(ctx, library, shared, "Artist - Shared.flac")
    only_file = _downloaded(ctx, library, only, "Artist - Only here.flac")
    playlist = actions.write_playlists(ctx, [gone_id])[0]
    plan = actions.remove_plan(ctx, [gone_id])
    assert [r.track_id for r in plan.files] == [only] and plan.playlists == [playlist]
    assert plan.shared == 1 and plan.count == 2
    assert "(2 files)" in outcomes.remove_choice_text(plan)
    outcome = actions.remove_collections_and_files(ctx, [gone_id])
    assert ctx.repo.get_collection(gone_id) is None and ctx.repo.get_collection(keep_id)
    assert not only_file.exists() and not playlist.exists() and shared_file.exists()
    assert sorted(recycle_bin.names()) == ["Artist - Only here.flac", "gone.m3u8"]
    # in no collection now: not downloaded, a list added again downloads it again
    assert _state(ctx, only)["status"] == "pending" and _state(ctx, only)["file_path"] is None
    assert _state(ctx, shared)["status"] == "done"
    assert "Removed collections" in outcomes.texts(outcomes.remove_lines(outcome))


def test_remove_without_the_option_keeps_every_file(ctx, library, recycle_bin):
    cid = _csv(ctx, "mix", ["One"])
    (tid,) = ctx.repo.collection_track_ids(cid)
    path = _downloaded(ctx, library, tid, "Artist - One.flac")
    actions.remove_collections(ctx, [cid])
    assert path.exists() and not recycle_bin.items and _state(ctx, tid)["status"] == "done"


def test_remove_with_files_refuses_a_drive_without_bin(ctx, library, recycle_bin):
    cid = _csv(ctx, "mix", ["One"])
    (tid,) = ctx.repo.collection_track_ids(cid)
    path = _downloaded(ctx, library, tid, "Artist - One.flac")
    recycle_bin.without_bin(library, "network drive")
    with pytest.raises(recycle.RecycleError, match="network drive"):
        actions.remove_collections_and_files(ctx, [cid])
    assert path.exists() and ctx.repo.get_collection(cid) is not None


# properties (D-125)


def test_track_info_has_everything(ctx, library):
    record = CollectionRecord(
        kind="csv", origin="file", name="mix", source_ref="C:/mix.csv", snapshot_id="x",
        tracks=[TrackRecord(title="One", artists=("A", "B"), album="Album", isrc="USABC1234567",
                            spotify_id="4uLU6hMCjMI75M1A2tKUQC", duration_ms=61_000,
                            track_number=3, release_date="2024-05-01")],
    )  # fmt: skip
    cid = ingest.ingest_collection(ctx.repo, record).collection_id
    (tid,) = ctx.repo.collection_track_ids(cid)
    path = _downloaded(ctx, library, tid, "A - One.flac")
    info = actions.track_info(ctx, tid)
    assert info.file_exists and info.collections == [(cid, "mix")]
    sections = dict(views.track_info_sections(info.row, info.collections, info.file_exists,
                                              ctx.settings()))  # fmt: skip
    values = {label: value for rows in sections.values() for label, value in rows}
    assert values["ISRC"] == "USABC1234567" and values["Artists"] == "A, B"
    assert values["Spotify"] == "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"
    assert values["Status"] == "done" and values["Path"] == str(path)
    assert values["Format"] == "FLAC 44.1/16" and values["Soulseek user"] == "uploader"
    assert values["Soulseek path"] == "@@x\\a.flac" and values["Stage"] == "FLAC pass"
    assert values["Collection"] == f"[{cid}] mix" and "of 3" in values["Attempts"]
    assert "Show in folder" in values["What next"]
    path.unlink()
    info = actions.track_info(ctx, tid)
    assert info.file_exists is False


# words and hints (D-129)


def test_pending_reads_not_downloaded_everywhere():
    assert views.state_label(db.PENDING) == "not downloaded"
    assert views.state_count_text(db.PENDING, 2) == "2 not downloaded"
    assert views.state_label(db.EXCLUDED) == "excluded"


@pytest.mark.parametrize("state", views.STATES)
def test_every_state_says_what_it_means_and_what_next(state):
    gui = views.state_hint(state, db.STAGE_FLAC, attempts=3, max_attempts=3)
    cli = views.state_hint(state, db.STAGE_FLAC, gui=False, attempts=3, max_attempts=3,
                           track_id=7, collection_id=2)  # fmt: skip
    assert gui and cli
    if state in (db.PENDING, db.EXCLUDED, db.NOT_FOUND, db.FAILED, views.WAITING_FALLBACK):
        assert "neonseek" in cli and "neonseek" not in gui  # a command for the cli
    assert views.state_hint(db.PENDING, db.STAGE_FLAC).endswith("Press DOWNLOAD to download it.")


def test_excluded_filter_and_counter(ctx):
    cid = _csv(ctx, "mix", ["One", "Two", "Three"])
    one, two, three = ctx.repo.collection_track_ids(cid)
    ctx.repo.update_download(one, {"status": db.DONE, "file_path": "x"})
    ctx.repo.exclude([two])
    rows = views.collection_tracks(ctx, cid)
    assert [r.state for r in rows if views.state_matches(r.state, views.FILTER_EXCLUDED)] == [
        "excluded"]  # fmt: skip
    assert not views.state_matches(db.EXCLUDED, views.FILTER_TODO)
    (summary,) = ctx.repo.list_collections()
    # excluded tracks are not wanted, so they do not count
    assert views.collection_counter(summary) == "1/2"
    assert "1 more is excluded" in views.counter_hint(summary)
    assert "1 excluded" in views.stats_text(rows)


# the track menu (D-125)


def _rows(ctx, ids):
    return [views.tracks_for(ctx, ids, {})[t] for t in ids]


def test_track_menu_items_follow_the_states(ctx):
    cid = _csv(ctx, "mix", ["Done", "Waiting", "Gone", "Out"])
    done, waiting, missing, out = ctx.repo.collection_track_ids(cid)
    ctx.repo.update_download(done, {"status": db.DONE, "file_path": "x.flac"})
    ctx.repo.update_download(missing, {"status": db.NOT_FOUND})
    ctx.repo.exclude([out])
    choice = views.track_choice(_rows(ctx, [done]), downloading=False)
    assert views.menu_reason(views.MENU_DOWNLOAD, choice) == "Already downloaded."
    assert views.menu_reason(views.MENU_FOLDER, choice) is None
    assert views.menu_reason(views.MENU_DELETE, choice) is None
    assert "Delete file" in views.menu_reason(views.MENU_EXCLUDE, choice)
    choice = views.track_choice(_rows(ctx, [waiting, missing, out]), downloading=False)
    assert choice.download == [waiting, missing, out] and choice.exclude == [waiting, missing]
    assert choice.include == [out]
    assert views.menu_label(views.MENU_DOWNLOAD, choice) == "Download these 3 tracks only"
    assert "file" in views.menu_reason(views.MENU_FOLDER, choice)
    assert views.menu_reason(views.MENU_DELETE, choice)
    one = views.track_choice(_rows(ctx, [waiting]), downloading=False)
    assert views.menu_label(views.MENU_DOWNLOAD, one) == "Download this track only"
    busy = views.track_choice(_rows(ctx, [waiting, done]), downloading=True)
    for item in (views.MENU_DOWNLOAD, views.MENU_EXCLUDE, views.MENU_INCLUDE, views.MENU_DELETE):
        assert "library move is running" in views.menu_reason(item, busy)
    assert views.menu_reason(views.MENU_PROPERTIES, busy) is None
    assert views.menu_reason(views.MENU_FOLDER, busy) is None


# the summary and other words (D-133)


def test_summary_mentions_wav_aiff_only_with_lossless():
    plan = actions.DownloadPlan([(3, "Mix")], [1], 1, 0, 0, 1, [], 220)
    assert "WAV/AIFF" in outcomes.texts(outcomes.plan_lines(plan))
    plan.fallback_pass = False
    text = outcomes.texts(outcomes.plan_lines(plan))
    assert "WAV/AIFF" not in text and "Download time is not included" in text
    assert "FLAC pass: 1 track to search" in text


def test_summary_of_chosen_tracks():
    chosen = [(5, "A - One"), (6, "A - Two"), (7, "A - Done")]
    plan = actions.DownloadPlan([(3, "Mix")], [5, 6], 2, 0, 1, 3, [], 220, chosen=chosen)
    text = outcomes.texts(outcomes.plan_lines(plan))
    assert "Tracks in this download:" in text and "[5] A - One" in text
    assert "Already downloaded, skipped: 1 track." in text
    assert "Playlists written again afterwards: [3] Mix" in text
    assert "Collections in this download" not in text


def test_one_track_is_singular():
    assert outcomes.retry_line(1).text == "1 track starts again from FLAC at the next download."
    assert outcomes.retry_line(2).text.startswith("2 tracks start again")
    assert outcomes.report_count_text(1) == "1 track in the report"


def test_sync_on_start_setting(ctx):
    assert ctx.settings().sync_on_start is True
    assert actions.change_setting(ctx, "sync_on_start", "no") is False
    assert ctx.settings().sync_on_start is False
    with pytest.raises(actions.ConfigError, match="true, false"):
        actions.change_setting(ctx, "sync_on_start", "maybe")
