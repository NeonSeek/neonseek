"""the recycle bin decision on made up drives. the real bin is never touched: conftest makes
shell_delete fail every test that would reach it, and the tests that delete use a stand in
that writes a recycle bin record into a folder of the test."""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import pytest

from neonseek import actions, outcomes, recycle
from neonseek.db import repo as db
from neonseek.sources import ingest
from neonseek.sources.base import CollectionRecord, TrackRecord

GIB = recycle.GIB
FIXED = recycle.VolumeFacts("D:\\", recycle.DRIVE_FIXED, False, False, None, 500 * GIB)
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the windows recycle bin")


def _bin(facts: recycle.VolumeFacts, delete=None) -> recycle.WindowsRecycleBin:
    return recycle.WindowsRecycleBin(lambda path: facts, delete)


def _record(path: Path, size: int, when: float, version: int = 2) -> bytes:
    """a $I file the way windows writes it."""
    filetime = int((when + 11_644_473_600) * 10_000_000)
    head = b"".join(v.to_bytes(8, "little") for v in (version, size, filetime))
    text = (str(path) + "\0").encode("utf-16-le")
    if version == 2:
        return head + (len(text) // 2).to_bytes(4, "little") + text
    return head + text.ljust(520, b"\0")


def _into_bin(root: Path, when: float | None = None, size: int | None = None, flat=False):
    """a stand in for windows: the file goes into root\\$Recycle.Bin with its $I record. ntfs
    keeps a folder per user, fat32 and exfat (flat) keep the records right in $Recycle.Bin."""

    def delete(path: Path) -> None:
        folder = root / "$Recycle.Bin"
        if not flat:
            folder = folder / "S-1-5-21-test"
        folder.mkdir(parents=True, exist_ok=True)
        record = _record(path, size if size is not None else path.stat().st_size,
                         when if when is not None else time.time())  # fmt: skip
        (folder / "$IABC123.flac").write_bytes(record)
        shutil.move(str(path), str(folder / "$RABC123.flac"))

    return delete


def _song(tmp_path: Path, size: int = 5) -> Path:
    song = tmp_path / "Music" / "tracks" / "song.flac"
    song.parent.mkdir(parents=True, exist_ok=True)
    song.write_bytes(b"x" * size)
    return song


@pytest.mark.parametrize(
    ("facts", "words"),
    [
        (recycle.VolumeFacts("E:\\", 2, False, False, None), "USB stick"),
        (recycle.VolumeFacts("\\\\nas\\music\\", 4, False, False, None), "network drive"),
        (recycle.VolumeFacts("S:\\", recycle.DRIVE_FIXED, True, False, None), "subst"),
        (recycle.VolumeFacts("D:\\", recycle.DRIVE_FIXED, False, True, None), "turned off"),
        (recycle.VolumeFacts("D:\\", recycle.DRIVE_FIXED, False, False, 3), "bigger than"),
    ],
)
@windows_only
def test_drives_without_a_bin_are_refused(tmp_path, facts, words):
    song = _song(tmp_path)
    problem = _bin(facts).problem(song)
    assert words in problem and "Nothing was deleted" in problem
    with pytest.raises(recycle.RecycleError, match=words):
        _bin(facts).recycle(song)
    assert song.exists()


# a file bigger than the bin: windows would delete it for good, neonseek refuses first


def test_default_bin_size_follows_windows():
    # 10 % of the first 40 GB and 5 % of the rest, the same as the registry of a real drive
    assert recycle.default_capacity(32 * GIB) == 32 * GIB // 10
    assert recycle.default_capacity(1000 * GIB) == 4 * GIB + 960 * GIB // 20


@windows_only
def test_a_file_bigger_than_the_bin_is_refused(tmp_path):
    song = _song(tmp_path, size=2000)
    set_by_user = recycle.VolumeFacts("D:\\", recycle.DRIVE_FIXED, False, False, 1000)
    assert "bigger than the Recycle Bin" in _bin(set_by_user).problem(song)
    # never set: the windows default for a drive this small is 100 bytes
    small_drive = recycle.VolumeFacts("D:\\", recycle.DRIVE_FIXED, False, False, None, 1000)
    assert "bigger than the Recycle Bin" in _bin(small_drive).problem(song)
    unknown = recycle.VolumeFacts("D:\\", recycle.DRIVE_FIXED, False, False, None, None)
    assert "does not tell the size" in _bin(unknown).problem(song)
    for facts in (set_by_user, small_drive, unknown):
        with pytest.raises(recycle.RecycleError):
            _bin(facts, _into_bin(tmp_path)).recycle(song)
    assert song.exists()
    assert _bin(FIXED).problem(song) is None  # a normal bin takes it


# a path longer than 260 characters: the recycle bin call cannot take it


@windows_only
def test_a_path_longer_than_max_path_is_refused_before_anything():
    long_path = Path("C:\\Music\\" + "x" * 300 + ".flac")
    problem = _bin(FIXED).problem(long_path)
    assert "longer than 259 characters" in problem and "Nothing was deleted" in problem
    with pytest.raises(recycle.RecycleError, match="longer than 259"):
        _bin(FIXED, lambda path: pytest.fail("must not delete")).recycle(long_path)
    assert recycle.path_problem(Path("C:\\Music\\" + "x" * 200 + ".flac")) is None


# the proof that a file really went into the bin


def test_bin_records_of_both_windows_versions():
    when = 1_760_000_000.0
    for version in (1, 2):
        record = recycle.read_bin_record(_record(Path("C:\\M\\a.flac"), 42, when, version))
        assert record.path == "C:\\M\\a.flac" and record.size == 42
        assert abs(record.deleted - when) < 0.001
    assert recycle.read_bin_record(b"junk") is None
    assert recycle.read_bin_record(b"\x07" + b"\0" * 40) is None


@windows_only
def test_a_file_counts_as_recycled_only_with_its_record(tmp_path):
    root = tmp_path / "volume"
    facts = recycle.VolumeFacts(str(root), recycle.DRIVE_FIXED, False, False, None, 500 * GIB)
    song = _song(root)
    _bin(facts, _into_bin(root)).recycle(song)  # moved, and the bin has its record
    assert not song.exists()

    unconfirmed = [
        lambda path: path.unlink(),  # gone for good, no record
        _into_bin(root, when=time.time() - 3600),  # an old deletion
        _into_bin(root, size=999),  # another file of that name
    ]
    for delete in unconfirmed:
        shutil.rmtree(root / "$Recycle.Bin", ignore_errors=True)
        song = _song(root)
        with pytest.raises(recycle.UnconfirmedError, match="could not confirm"):
            _bin(facts, delete).recycle(song)
    shutil.rmtree(root / "$Recycle.Bin", ignore_errors=True)
    song = _song(root)
    with pytest.raises(recycle.RecycleError, match="still in the library") as caught:
        _bin(facts, lambda path: None).recycle(song)  # windows did nothing
    assert not isinstance(caught.value, recycle.UnconfirmedError)


@windows_only
def test_a_drive_whose_bin_has_no_user_folders(tmp_path):
    # the case of the owner's exfat drive D: the $I and $R files lie right in $Recycle.Bin,
    # next to another user's folder that cannot be read
    root = tmp_path / "exfat"
    facts = recycle.VolumeFacts(str(root), recycle.DRIVE_FIXED, False, False, None, 4000 * GIB)
    other = root / "$Recycle.Bin" / "S-1-5-21-other"
    other.mkdir(parents=True)
    (root / "$Recycle.Bin" / "desktop.ini").write_text("[.ShellClassInfo]")
    song = root / "Music" / "Library" / "tracks" / "Nightline - Concrete Pulse.flac"
    song.parent.mkdir(parents=True)
    song.write_bytes(b"f" * 4096)
    _bin(facts, _into_bin(root, flat=True)).recycle(song)  # confirmed, no error
    assert not song.exists() and (root / "$Recycle.Bin" / "$IABC123.flac").is_file()


def _one_downloaded(ctx, tmp_path) -> tuple[int, Path]:
    library = tmp_path / "volume" / "Music"
    actions.change_setting(ctx, "library_dir", str(library))
    tracks = [TrackRecord(title="One", artists=("A",))]
    record = CollectionRecord(kind="csv", origin="file", name="mix", source_ref="C:/mix.csv",
                              snapshot_id="x", tracks=tracks)  # fmt: skip
    cid = ingest.ingest_collection(ctx.repo, record).collection_id
    (tid,) = ctx.repo.collection_track_ids(cid)
    song = _song(tmp_path / "volume")
    ctx.repo.update_download(tid, {"status": db.DONE, "file_path": str(song)})
    return tid, song


@windows_only
def test_a_gone_file_without_a_bin_record_is_excluded_with_a_warning(ctx, tmp_path):
    tid, song = _one_downloaded(ctx, tmp_path)
    facts = recycle.VolumeFacts(str(tmp_path / "volume"), recycle.DRIVE_FIXED, False, False,
                                None, 500 * GIB)  # fmt: skip
    ctx.recycle_bin = _bin(facts, lambda path: path.unlink())
    outcome = actions.delete_track_files(ctx, [tid])
    # the file left the library, so the track is never done again
    assert not song.exists() and ctx.repo.get_download(tid)["status"] == "excluded"
    assert not outcome.failed and len(outcome.unconfirmed) == 1
    text = outcomes.texts(outcomes.delete_lines(outcome))
    assert "could not confirm it in the Recycle Bin" in text and "Check the Recycle Bin" in text


@windows_only
def test_the_exfat_bin_confirms_and_excludes_at_once(ctx, tmp_path):
    tid, song = _one_downloaded(ctx, tmp_path)
    root = tmp_path / "volume"
    facts = recycle.VolumeFacts(str(root), recycle.DRIVE_FIXED, False, False, None, 500 * GIB)
    ctx.recycle_bin = _bin(facts, _into_bin(root, flat=True))
    outcome = actions.delete_track_files(ctx, [tid])
    assert len(outcome.recycled) == 1 and not outcome.unconfirmed and not outcome.failed
    assert ctx.repo.get_download(tid)["status"] == "excluded"
    assert "Moved 1 file to the Recycle Bin" in outcomes.texts(outcomes.delete_lines(outcome))


@windows_only
def test_a_fixed_drive_goes_to_the_bin_through_shell32_only(tmp_path):
    song = _song(tmp_path)
    assert _bin(FIXED).problem(song) is None
    # the call reaches shell_delete, which the tests replace with a failure
    with pytest.raises(AssertionError, match="must not reach the windows recycle bin"):
        _bin(FIXED).recycle(song)
    assert song.exists()


def test_every_context_uses_the_fake_bin(ctx, recycle_bin):
    assert ctx.recycle_bin is recycle_bin
    assert ctx.fork().recycle_bin is recycle_bin
    assert actions.AppContext(ctx.data_dir).recycle_bin is recycle_bin


@windows_only
def test_the_temp_folder_is_on_a_fixed_drive(tmp_path):
    # only reads what windows says about the drive, nothing is deleted
    facts = recycle.volume_facts(tmp_path)
    assert facts.root and facts.drive_type == recycle.DRIVE_FIXED and not facts.subst
    assert facts.total_bytes and recycle.bin_capacity(facts)
