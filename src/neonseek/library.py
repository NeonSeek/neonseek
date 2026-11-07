"""files neonseek placed in the library, and moving them to another library folder.

only files the app put there are ever touched: the file of a downloaded track in
<library>\\tracks and the playlist of a collection in <library>\\playlists (PLAN.md section 8).
a move writes a journal row before a file is touched and drops it in the same transaction
that saves the new path, so an interrupted move never loses a file (D-128).
"""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from neonseek.db import repo as db
from neonseek.i18n import tr
from neonseek.pipeline import playlists
from neonseek.pipeline.postprocess.place import TRACKS_DIR
from neonseek.recycle import MAX_PATH_CHARS

PLAYLISTS_DIR = playlists.PLAYLISTS_DIR
STAGING_DIR = ".staging"
# a copy to another drive is written under this name first, renamed when it is complete
TEMP_SUFFIX = ".neonseek-move"
# windows: ERROR_NOT_SAME_DEVICE, the move needs a copy
_OTHER_DRIVE = 17


def norm(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def is_inside(path: str | Path, folder: str | Path) -> bool:
    """the path lies somewhere below the folder (not the folder itself)."""
    inner, outer = norm(path), norm(folder)
    return inner.startswith(outer.rstrip(os.sep) + os.sep)


def tracks_dir(library: Path) -> Path:
    return Path(library) / TRACKS_DIR


def owned_file(file_path: str | None, library: Path) -> bool:
    """a track file neonseek placed in this library (step place puts every file there)."""
    return bool(file_path) and is_inside(file_path, tracks_dir(library))


def temp_path(target: Path) -> Path:
    return target.with_name(target.name + TEMP_SUFFIX)


# journal recovery


@dataclass
class Recovered:
    track_ids: list[int] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.track_ids)


def recover_moves(repo: db.Repo) -> Recovered:
    """settles moves an interruption left in the journal with what is on the disk."""
    found = Recovered()
    for row in repo.pending_moves():
        source, target = Path(row["from_path"]), Path(row["to_path"])
        keep = source
        if target.exists():
            if not source.exists():
                keep = target
            elif _same_size(source, target):
                # the copy was complete, it is only renamed to its final name when it is
                keep = target
                try:
                    source.unlink()
                except OSError:
                    # the source is in use: it stays, our copy goes, the next move tries again
                    keep = source
                    _drop_copy(target)
            # different sizes: something else changed a file, both stay and the source counts
        # a copy that never got its final name is our own unfinished work
        with contextlib.suppress(OSError):
            temp_path(target).unlink(missing_ok=True)
        repo.finish_move(row["track_id"], str(keep))
        found.track_ids.append(row["track_id"])
    return found


def _drop_copy(copy: Path) -> None:
    # only ever a copy this move made while the source is still there, nothing is lost
    with contextlib.suppress(OSError):
        copy.unlink()


def _same_size(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size
    except OSError:
        return False


# the move


def target_problem(old: Path, new: Path) -> str | None:
    """a library folder the files can move to, or why not."""
    if norm(old) == norm(new):
        return tr("This is already the library folder.")
    for sub in (TRACKS_DIR, PLAYLISTS_DIR, STAGING_DIR):
        if norm(new) == norm(old / sub) or is_inside(new, old / sub):
            return tr(
                "The new folder is inside the {name} folder of the current library. Choose "
                "another folder."
            ).format(name=sub)
    return None


@dataclass
class MovePlan:
    old: Path
    new: Path
    files: list[tuple[int, Path]]  # track id and its file, only files neonseek placed
    size: int  # bytes of these files
    playlists: list[Path]  # playlist files of collections in the old library
    missing: int  # downloaded tracks whose file is no longer there
    outside: int  # downloaded tracks whose file is outside the old library, they stay
    same_drive: bool
    free: int | None  # free bytes on the drive of the new folder
    # files whose path in the new folder would be longer than windows allows: path, length
    too_long: list[tuple[Path, int]] = field(default_factory=list)

    @property
    def nothing(self) -> bool:
        return not self.files and not self.playlists

    @property
    def needs_space(self) -> bool:
        return not self.same_drive and self.free is not None and self.free < self.size


def _drive(path: Path) -> str:
    return os.path.splitdrive(norm(path))[0]


def _existing_parent(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return path


def plan_move(repo: db.Repo, old: Path, new: Path) -> MovePlan:
    files: list[tuple[int, Path]] = []
    size = missing = outside = 0
    for row in repo.downloaded_files():
        path = Path(row["file_path"])
        if not owned_file(row["file_path"], old):
            outside += 1
        elif path.is_file():
            files.append((row["track_id"], path))
            size += path.stat().st_size
        else:
            missing += 1
    found_playlists = []
    for summary in repo.list_collections():
        path = playlists.playlist_path(repo, summary.id, old)
        if path.is_file():
            found_playlists.append(path)
    try:
        free = shutil.disk_usage(_existing_parent(new)).free
    except OSError:
        free = None
    same_drive = _drive(old) == _drive(new)
    return MovePlan(
        old=old,
        new=new,
        files=files,
        size=size,
        playlists=found_playlists,
        missing=missing,
        outside=outside,
        same_drive=same_drive,
        free=free,
        too_long=_too_long(repo, new, files, found_playlists, old, same_drive),
    )


def _too_long(
    repo: db.Repo,
    new: Path,
    files: list[tuple[int, Path]],
    found_playlists: list[Path],
    old: Path,
    same_drive: bool,
) -> list[tuple[Path, int]]:
    """files that would get a path over the windows limit in the new folder. counted with
    the longest path the move itself uses: the track id a taken name gets, and the temporary
    copy on another drive (playlists are written through a .tmp file)."""
    limit = MAX_PATH_CHARS
    folder = tracks_dir(new)
    copy = 0 if same_drive else len(TEMP_SUFFIX)
    found = []
    for track_id, source in files:
        target = folder / source.name
        longest = len(norm(target)) + copy
        if target.exists():
            longest += len(f" [{track_id:06d}]")
        if longest > limit:
            found.append((source, longest))
    for source in found_playlists:
        longest = len(norm(new / PLAYLISTS_DIR / source.name)) + len(".tmp")
        if longest > limit:
            found.append((source, longest))
    return found


@dataclass
class MoveResult:
    moved: list[int] = field(default_factory=list)  # track ids
    failed: list[tuple[str, str]] = field(default_factory=list)  # file, reason
    cancelled: bool = False

    def complete(self, plan: MovePlan) -> bool:
        return not self.failed and not self.cancelled and len(self.moved) == len(plan.files)


def _free_name(folder: Path, source: Path, track_id: int, taken: set[str]) -> Path | None:
    """the same name in the new folder, with the track id when it is taken. never a file
    that is already there."""
    for name in (source.name, f"{source.stem} [{track_id:06d}]{source.suffix}"):
        candidate = folder / name
        if norm(candidate) not in taken and not candidate.exists():
            return candidate
    return None


def _move_one(source: Path, target: Path) -> None:
    try:
        # the same drive: one rename, windows never replaces an existing target here
        os.rename(source, target)
        return
    except OSError as exc:
        if getattr(exc, "winerror", None) != _OTHER_DRIVE and exc.errno != errno.EXDEV:
            raise
    temp = temp_path(target)
    shutil.copy2(source, temp)
    # the source goes for good only after the copy is checked (owner decision, D-153)
    if not _same_size(source, temp):
        _drop_copy(temp)
        raise OSError(errno.EIO, tr("the copy is not complete, the file stays where it was"))
    os.rename(temp, target)
    # the copy is complete and has its name, only now the source goes
    try:
        source.unlink()
    except OSError:
        _drop_copy(target)
        raise


def move_files(
    repo: db.Repo,
    plan: MovePlan,
    progress: Callable[[int, int, str], None] = lambda done, total, name: None,
    cancelled: Callable[[], bool] = lambda: False,
) -> MoveResult:
    """moves every planned file, one at a time, each through the journal."""
    result = MoveResult()
    folder = tracks_dir(plan.new)
    folder.mkdir(parents=True, exist_ok=True)
    # paths other tracks own are taken even when their file is missing
    taken = {norm(row["file_path"]) for row in repo.file_paths()}
    total = len(plan.files)
    for index, (track_id, source) in enumerate(plan.files):
        if cancelled():
            result.cancelled = True
            break
        progress(index, total, source.name)
        target = _free_name(folder, source, track_id, taken)
        if target is None:
            result.failed.append(
                (str(source), tr("a file with this name is already in the new folder"))
            )
            continue
        repo.start_move(track_id, str(source), str(target))
        try:
            _move_one(source, target)
        except OSError as exc:
            # nothing moved or a partial copy: the journal settles it, the file stays put
            recover_moves(repo)
            result.failed.append((str(source), exc.strerror or str(exc)))
            continue
        repo.finish_move(track_id, str(target))
        taken.add(norm(target))
        result.moved.append(track_id)
    progress(len(result.moved), total, "")
    return result


def move_playlists(repo: db.Repo, collection_ids: Iterable[int], old: Path, new: Path) -> None:
    """the playlist files of these collections go to the new library, written again later."""
    for cid in collection_ids:
        source = playlists.playlist_path(repo, cid, old)
        if not source.is_file():
            continue
        target = playlists.playlist_path(repo, cid, new)
        target.parent.mkdir(parents=True, exist_ok=True)
        # a playlist of this collection in the new folder is ours, written again anyway
        shutil.move(str(source), str(target))


def playlist_collections(repo: db.Repo, library: Path, track_ids: Iterable[int]) -> list[int]:
    """collections whose playlist exists in this library or that hold one of the tracks."""
    ids = set(repo.collections_of_tracks(track_ids))
    for summary in repo.list_collections():
        if playlists.playlist_path(repo, summary.id, library).is_file():
            ids.add(summary.id)
    return sorted(ids)
