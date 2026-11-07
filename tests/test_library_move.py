"""moving the library to another folder (D-128): only files neonseek placed move, an
interruption at any point loses no file, and the database and the playlists agree with the
disk on both sides."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from neonseek import actions, library, outcomes
from neonseek.db import repo as db
from neonseek.errors import ConfigError
from neonseek.pipeline import playlists
from neonseek.sources import ingest
from neonseek.sources.base import CollectionRecord, TrackRecord


@pytest.fixture
def old(ctx, tmp_path) -> Path:
    folder = tmp_path / "Old Music"
    actions.change_setting(ctx, "library_dir", str(folder))
    return folder


def _collection(ctx, old: Path, name: str, titles: list[str]) -> tuple[int, list[int]]:
    tracks = [TrackRecord(title=t, artists=("Artist",), duration_ms=60_000) for t in titles]
    record = CollectionRecord(kind="csv", origin="file", name=name, source_ref=f"C:/{name}.csv",
                              snapshot_id=name, tracks=tracks)  # fmt: skip
    cid = ingest.ingest_collection(ctx.repo, record).collection_id
    ids = ctx.repo.collection_track_ids(cid)
    for tid, title in zip(ids, titles, strict=True):
        path = old / "tracks" / f"Artist - {title}.flac"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(title.encode() * 50)
        ctx.repo.update_download(tid, {"status": db.DONE, "file_path": str(path), "format": "flac"})
    actions.write_playlists(ctx, [cid])
    return cid, ids


def _paths(ctx, ids) -> list[Path]:
    return [Path(ctx.repo.get_download(t)["file_path"]) for t in ids]


def _playlist_entries(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line and not line.startswith("#")]


def _resolves(playlist: Path) -> bool:
    """every entry of the playlist points at a file that is there."""
    return all((playlist.parent / entry).is_file() for entry in _playlist_entries(playlist))


def test_move_on_the_same_drive(ctx, old, tmp_path):
    cid, ids = _collection(ctx, old, "Mix", ["One", "Two"])
    mine = old / "tracks" / "my own file.flac"
    mine.write_bytes(b"not from neonseek")
    new = tmp_path / "New Music"
    plan = actions.library_move_plan(ctx, str(new))
    assert len(plan.files) == 2 and plan.same_drive and len(plan.playlists) == 1
    assert "2 downloaded files" in outcomes.texts(outcomes.move_plan_lines(plan))
    seen = []
    outcome = actions.move_library(ctx, str(new), lambda done, total, name: seen.append(done))
    assert outcome.complete and ctx.settings().library_dir == str(new)
    assert all(library.is_inside(p, new / "tracks") and p.is_file() for p in _paths(ctx, ids))
    assert not (old / "tracks" / "Artist - One.flac").exists()
    assert mine.exists()  # files the user put there stay
    playlist = playlists.playlist_path(ctx.repo, cid, new)
    assert playlist.is_file() and _resolves(playlist)
    assert not playlists.playlist_path(ctx.repo, cid, old).exists()  # moved, not copied
    assert seen and ctx.repo.pending_moves() == []
    text = outcomes.texts(outcomes.move_lines(outcome, gui=True))
    assert "Moved 2 of 2 files" in text and "Relocate" in text


def test_move_to_another_drive_copies_then_removes(ctx, old, tmp_path, monkeypatch):
    _, ids = _collection(ctx, old, "Mix", ["One"])
    real_rename = os.rename

    def other_drive(source, target):
        # the first rename of a file is the plain move, another drive refuses it
        if str(target).endswith(".flac") and not str(source).endswith(library.TEMP_SUFFIX):
            error = OSError("not the same device")
            error.winerror = 17
            raise error
        return real_rename(source, target)

    monkeypatch.setattr(library.os, "rename", other_drive)
    outcome = actions.move_library(ctx, str(tmp_path / "New"))
    (path,) = _paths(ctx, ids)
    assert outcome.complete and path.read_bytes() == b"One" * 50
    assert not list((tmp_path / "New" / "tracks").glob("*" + library.TEMP_SUFFIX))
    assert not (old / "tracks" / "Artist - One.flac").exists()


def test_a_copy_that_is_not_complete_never_removes_the_source(ctx, old, tmp_path, monkeypatch):
    # the source of another drive goes only after the copy is checked (D-153)
    cid, ids = _collection(ctx, old, "Mix", ["One"])
    source = _paths(ctx, ids)[0]
    real_rename = os.rename

    def other_drive(src, target):
        if str(target).endswith(".flac") and not str(src).endswith(library.TEMP_SUFFIX):
            error = OSError("not the same device")
            error.winerror = 17
            raise error
        return real_rename(src, target)

    def short_copy(src, target):
        # a disk that filled up or a copy that broke off: half of the file arrived
        Path(target).write_bytes(Path(src).read_bytes()[:10])

    monkeypatch.setattr(library.os, "rename", other_drive)
    monkeypatch.setattr(library.shutil, "copy2", short_copy)
    new = tmp_path / "New"
    outcome = actions.move_library(ctx, str(new))
    assert not outcome.complete and "not complete" in outcome.result.failed[0][1]
    assert source.read_bytes() == b"One" * 50 and _paths(ctx, ids) == [source]
    assert not list((new / "tracks").glob("*"))  # neither the copy nor a final file
    assert ctx.repo.pending_moves() == [] and ctx.settings().library_dir == str(old)
    assert _resolves(playlists.playlist_path(ctx.repo, cid, old))


def test_the_question_says_when_old_files_go_for_good(ctx, old, tmp_path, monkeypatch):
    _collection(ctx, old, "Mix", ["One"])
    same = outcomes.texts(outcomes.move_plan_lines(actions.library_move_plan(ctx, str(tmp_path))))
    assert "for good" not in same  # a rename on the same drive deletes nothing
    monkeypatch.setattr(library, "_drive", lambda path: "X:" if "New" in str(path) else "C:")
    plan = actions.library_move_plan(ctx, str(tmp_path / "New"))
    text = outcomes.texts(outcomes.move_plan_lines(plan, gui=True))
    assert "the copy is checked, and only then the old file is deleted for good" in text


def test_a_name_taken_in_the_new_folder_is_never_overwritten(ctx, old, tmp_path):
    _, ids = _collection(ctx, old, "Mix", ["One"])
    new = tmp_path / "New"
    taken = new / "tracks" / "Artist - One.flac"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"somebody else")
    outcome = actions.move_library(ctx, str(new))
    (path,) = _paths(ctx, ids)
    assert outcome.complete and taken.read_bytes() == b"somebody else"
    assert path.name == f"Artist - One [{ids[0]:06d}].flac" and path.is_file()


def test_a_stopped_move_keeps_the_old_library_and_its_playlists_right(ctx, old, tmp_path):
    cid, ids = _collection(ctx, old, "Mix", ["One", "Two", "Three"])
    new = tmp_path / "New"
    outcome = actions.move_library(ctx, str(new), None, lambda: len(ctx.repo.pending_moves()) > 0
                                   or bool(list((new / "tracks").glob("*.flac"))))  # fmt: skip
    assert not outcome.complete and outcome.result.cancelled
    assert ctx.settings().library_dir == str(old)
    moved = [p for p in _paths(ctx, ids) if library.is_inside(p, new)]
    stayed = [p for p in _paths(ctx, ids) if library.is_inside(p, old)]
    assert len(moved) == 1 and len(stayed) == 2
    assert all(p.is_file() for p in _paths(ctx, ids))
    # the playlist of the old library points to the files where they are now
    playlist = playlists.playlist_path(ctx.repo, cid, old)
    assert _resolves(playlist) and len(_playlist_entries(playlist)) == 3
    assert "To finish, choose the same new folder" in outcomes.texts(
        outcomes.move_lines(outcome, gui=True)
    )
    # moving again to the same folder finishes the job
    again = actions.move_library(ctx, str(new))
    assert again.complete and ctx.settings().library_dir == str(new)
    assert all(library.is_inside(p, new) and p.is_file() for p in _paths(ctx, ids))
    assert _resolves(playlists.playlist_path(ctx.repo, cid, new))


def test_a_file_that_cannot_move_stays_and_the_folder_does_not_change(ctx, old, tmp_path,
                                                                     monkeypatch):  # fmt: skip
    _, ids = _collection(ctx, old, "Mix", ["One", "Two"])
    real_rename = os.rename

    def busy(source, target):
        if Path(source).name == "Artist - Two.flac":
            raise PermissionError(13, "used by another program")
        return real_rename(source, target)

    monkeypatch.setattr(library.os, "rename", busy)
    outcome = actions.move_library(ctx, str(tmp_path / "New"))
    assert not outcome.complete and len(outcome.result.failed) == 1
    assert ctx.settings().library_dir == str(old) and ctx.repo.pending_moves() == []
    assert all(p.is_file() for p in _paths(ctx, ids))
    assert "Not moved" in outcomes.texts(outcomes.move_lines(outcome, gui=False))


def test_journal_after_a_crash(ctx, old, tmp_path):
    _, ids = _collection(ctx, old, "Mix", ["Moved", "Not yet", "Copied", "Half"])
    moved, not_yet, copied, half = ids
    new = tmp_path / "New" / "tracks"
    new.mkdir(parents=True)
    sources = dict(zip(ids, _paths(ctx, ids), strict=True))

    def journal(tid):
        target = new / sources[tid].name
        ctx.repo.start_move(tid, str(sources[tid]), str(target))
        return target

    # renamed before the crash, the database still had the old path
    target = journal(moved)
    os.rename(sources[moved], target)
    # crashed before anything happened
    journal(not_yet)
    # a full copy got its name, the source was not removed yet
    target_copied = journal(copied)
    target_copied.write_bytes(sources[copied].read_bytes())
    # a copy that never finished
    target_half = journal(half)
    library.temp_path(target_half).write_bytes(b"Ha")

    assert actions.recover_library_move(ctx) == 4
    assert ctx.repo.pending_moves() == []
    paths = dict(zip(ids, _paths(ctx, ids), strict=True))
    assert paths[moved] == new / "Artist - Moved.flac" and paths[moved].is_file()
    assert paths[not_yet] == sources[not_yet] and paths[not_yet].is_file()
    assert paths[copied] == target_copied and not sources[copied].exists()
    assert paths[half] == sources[half] and not library.temp_path(target_half).exists()
    for path in paths.values():
        assert path.is_file()


def test_bad_targets_and_space(ctx, old, tmp_path, monkeypatch):
    _collection(ctx, old, "Mix", ["One"])
    with pytest.raises(ConfigError, match="already the library"):
        actions.library_move_plan(ctx, str(old))
    with pytest.raises(ConfigError, match="inside the tracks folder"):
        actions.library_move_plan(ctx, str(old / "tracks" / "x"))
    with pytest.raises(ConfigError, match="full path"):
        actions.library_move_plan(ctx, "relative")
    # a subfolder of the library itself is fine
    assert actions.library_move_plan(ctx, str(old / "NeonSeek")).files
    plan = actions.library_move_plan(ctx, str(tmp_path / "New"))
    monkeypatch.setattr(library.shutil, "disk_usage", lambda path: type("U", (), {"free": 1})())
    monkeypatch.setattr(library, "_drive", lambda path: "X:" if "New" in str(path) else "C:")
    plan = actions.library_move_plan(ctx, str(tmp_path / "New"))
    assert plan.needs_space
    with pytest.raises(ConfigError, match="Free some space"):
        actions.move_library(ctx, str(tmp_path / "New"))
    assert ctx.settings().library_dir == str(old)


def test_files_outside_the_library_and_missing_ones_stay(ctx, old, tmp_path):
    _, ids = _collection(ctx, old, "Mix", ["In", "Out", "Missing"])
    inside, outside, missing = ids
    elsewhere = tmp_path / "Elsewhere" / "Out.flac"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"x")
    ctx.repo.update_download(outside, {"file_path": str(elsewhere)})
    _paths(ctx, [missing])[0].unlink()
    plan = actions.library_move_plan(ctx, str(tmp_path / "New"))
    assert (len(plan.files), plan.outside, plan.missing) == (1, 1, 1)
    actions.move_library(ctx, str(tmp_path / "New"))
    assert _paths(ctx, [outside]) == [elsewhere] and elsewhere.exists()


def test_cli_setting_change_hints_at_the_move(ctx, old, tmp_path, capsys):
    from neonseek.cli import main

    _collection(ctx, old, "Mix", ["One"])
    ctx.close()
    code = main(["--data-dir", str(ctx.data_dir), "settings", "library_dir", str(tmp_path / "N")])
    out = capsys.readouterr().out
    assert code == 0 and "1 downloaded file stays" in out and "neonseek library move" in out


def test_paths_over_the_windows_limit_are_refused_before_anything_moves(ctx, old, tmp_path):
    _, ids = _collection(ctx, old, "Mix", ["One", "Two"])
    before = _paths(ctx, ids)
    deep = tmp_path / ("x" * 120) / ("y" * 110)
    with pytest.raises(ConfigError) as caught:
        actions.library_move_plan(ctx, str(deep))
    message = caught.value.message
    assert "longer than 259 characters" in message and "Nothing was moved" in message
    assert "Artist - One.flac" in message and "Artist - Two.flac" in message
    with pytest.raises(ConfigError, match="longer than 259"):
        actions.move_library(ctx, str(deep))
    assert _paths(ctx, ids) == before and all(p.is_file() for p in before)
    assert ctx.settings().library_dir == str(old) and not deep.exists()


def test_the_limit_counts_the_copy_and_a_taken_name(ctx, old, tmp_path, monkeypatch):
    _, (tid,) = _collection(ctx, old, "Mix", ["One"])
    name = "Artist - One.flac"
    # a folder that leaves the plain target exactly at the limit
    room = 259 - len(str(tmp_path / "tracks" / name)) - 1
    new = tmp_path / ("z" * room)
    assert len(library.norm(new / "tracks" / name)) == 259
    assert not actions.library_move_plan(ctx, str(new)).too_long  # the same drive fits
    monkeypatch.setattr(library, "_drive", lambda path: "X:" if "zzz" in str(path) else "C:")
    plan = library.plan_move(ctx.repo, old, new)
    assert plan.too_long and plan.too_long[0][1] == 259 + len(library.TEMP_SUFFIX)
