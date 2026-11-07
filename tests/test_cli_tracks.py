"""cli twins of the track menu, the recycle bin option, the library move and the playlist list
at start (every window function has a command, PLAN.md section 0)."""

from __future__ import annotations

from pathlib import Path

from neonseek import actions
from neonseek.cli import main
from neonseek.db import repo as db
from neonseek.sources import ingest
from neonseek.sources.base import CollectionRecord, TrackRecord


def _setup(ctx, tmp_path) -> tuple[Path, list[int]]:
    library = tmp_path / "Music"
    actions.change_setting(ctx, "library_dir", str(library))
    tracks = [TrackRecord(title=t, artists=("Artist",), duration_ms=60_000)
              for t in ("One", "Two", "Three")]  # fmt: skip
    record = CollectionRecord(kind="csv", origin="file", name="mix", source_ref="C:/mix.csv",
                              snapshot_id="x", tracks=tracks)  # fmt: skip
    cid = ingest.ingest_collection(ctx.repo, record).collection_id
    ids = ctx.repo.collection_track_ids(cid)
    path = library / "tracks" / "Artist - One.flac"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * 10)
    ctx.repo.update_download(ids[0], {"status": db.DONE, "file_path": str(path), "format": "flac"})
    ctx.repo.update_download(ids[1], {"status": db.NOT_FOUND, "last_error": "NoSearchResults"})
    actions.write_playlists(ctx, [cid])
    ctx.close()
    return library, ids


def _run(capsys, ctx, *argv):
    code = main(["--data-dir", str(ctx.data_dir), *argv])
    out, err = capsys.readouterr()
    return code, out, err


def test_track_info_and_folder(capsys, ctx, tmp_path):
    library, (one, two, _) = _setup(ctx, tmp_path)
    code, out, _ = _run(capsys, ctx, "track", "info", str(one), str(two))
    assert code == 0 and "FROM THE SOURCE" in out and "DOWNLOAD" in out and "FILE" in out
    assert "Soulseek path:" in out and "waiting for WAV/AIFF" in out
    assert f"neonseek download --track {two}" in out  # the next step as a command
    code, out, _ = _run(capsys, ctx, "track", "folder", str(one))
    assert code == 0 and out.strip() == str(library / "tracks" / "Artist - One.flac")
    code, _, err = _run(capsys, ctx, "track", "folder", str(two))
    assert code == 1 and "no downloaded file" in err


def test_track_exclude_include_and_the_legend(capsys, ctx, tmp_path):
    _, (one, two, three) = _setup(ctx, tmp_path)
    code, out, _ = _run(capsys, ctx, "track", "exclude", str(one), str(three))
    assert code == 0 and "Excluded 1 track" in out and "1 track was not changed" in out
    code, out, _ = _run(capsys, ctx, "tracks", "--collection", "1", "--status", "excluded")
    assert "excluded" in out and "Three" in out and "One" not in out
    assert "neonseek track include" in out  # the legend says what to do next
    code, out, _ = _run(capsys, ctx, "track", "include", str(three), str(two))
    assert code == 0 and "Included 1 track again" in out and "1 track was not excluded" in out


def test_track_delete_file_asks_first(capsys, ctx, tmp_path, recycle_bin):
    library, (one, _, _) = _setup(ctx, tmp_path)
    path = library / "tracks" / "Artist - One.flac"
    code, out, err = _run(capsys, ctx, "track", "delete-file", str(one))
    assert code == 1 and "add --yes" in err and path.exists()  # no terminal, nothing deleted
    code, out, _ = _run(capsys, ctx, "track", "delete-file", str(one), "--yes")
    assert code == 0 and not path.exists() and recycle_bin.names() == ["Artist - One.flac"]
    assert "Recycle Bin" in out and "Playlist written again" in out
    assert actions.AppContext(ctx.data_dir).repo.get_download(one)["status"] == "excluded"


def test_delete_file_refuses_without_a_bin(capsys, ctx, tmp_path, recycle_bin):
    library, (one, _, _) = _setup(ctx, tmp_path)
    recycle_bin.without_bin(library)
    code, _, err = _run(capsys, ctx, "track", "delete-file", str(one), "--yes")
    assert code == 1 and "Recycle Bin" in err
    assert (library / "tracks" / "Artist - One.flac").exists()


def test_remove_with_files(capsys, ctx, tmp_path, recycle_bin):
    library, _ = _setup(ctx, tmp_path)
    code, _, err = _run(capsys, ctx, "remove", "1", "--delete-files")
    assert code == 1 and "add --yes" in err and actions.AppContext(ctx.data_dir).repo.count(
        "collections") == 1  # fmt: skip
    code, out, _ = _run(capsys, ctx, "remove", "1", "--delete-files", "--yes")
    assert code == 0 and "Moved to the Recycle Bin: 1 track file and 1 playlist file" in out
    assert sorted(recycle_bin.names()) == ["Artist - One.flac", "mix.m3u8"]


def test_library_move(capsys, ctx, tmp_path):
    library, (one, _, _) = _setup(ctx, tmp_path)
    new = tmp_path / "New"
    code, out, err = _run(capsys, ctx, "library", "move", str(new))
    assert code == 1 and "1 downloaded file" in out and "add --yes" in err
    code, out, _ = _run(capsys, ctx, "library", "move", str(new), "--yes")
    assert code == 0 and "The library folder is now" in out
    assert (new / "tracks" / "Artist - One.flac").is_file()
    assert (new / "playlists" / "mix.m3u8").is_file()
    assert _run(capsys, ctx, "settings", "library_dir")[1].strip() == str(new)


def test_sync_on_start_setting_and_list_only(capsys, ctx, tmp_path, fake_spotify):
    code, out, _ = _run(capsys, ctx, "settings")
    assert "sync_on_start = true" in out
    assert _run(capsys, ctx, "settings", "sync_on_start", "false")[0] == 0
    assert _run(capsys, ctx, "settings", "sync_on_start")[1].strip() == "false"
    code, _, err = _run(capsys, ctx, "settings", "sync_on_start", "maybe")
    assert code == 1 and "true, false" in err


def test_sync_list_only_reads_no_content(spotify_ctx, fake_spotify):
    report = actions.sync_spotify(spotify_ctx, list_only=True)
    assert report.playlists_listed and not report.collections
    summaries = spotify_ctx.repo.list_collections()
    assert summaries and all(not s.selected for s in summaries)  # new ones come unticked
    assert all(s.last_synced_at is None for s in summaries if s.kind == "spotify_playlist")
    # only the list and the one probe of foreign playlists, no content and no liked songs
    assert not [r for r in fake_spotify.requests if "/items" in r and not r.endswith("limit=1")]
    assert not [r for r in fake_spotify.requests if r.startswith("/me/tracks")]
