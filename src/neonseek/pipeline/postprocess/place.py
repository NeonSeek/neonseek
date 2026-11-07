"""place: moves the file to <library>\\tracks\\{first artist} - {title}.{ext} (PLAN.md 8)."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from neonseek.db.repo import same_path
from neonseek.i18n import tr
from neonseek.pipeline.postprocess import base
from neonseek.pipeline.postprocess.base import StepContext, StepError, TrackFile

TRACKS_DIR = "tracks"
# full paths stay below the classic windows limit of 260 characters
MAX_PATH = 250
MAX_NAME = 150
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}  # fmt: skip


def safe_name(text: str, limit: int = MAX_NAME) -> str:
    """a file name windows accepts: no forbidden characters, no reserved names, not too long."""
    name = " ".join(_BAD.sub("_", text).split())
    name = name[:limit].rstrip(" .")
    if not name:
        name = "track"
    if name.split(".")[0].upper() in _RESERVED:
        name += "_"
    return name


def track_stem(track) -> str:
    artists = json.loads(track["artists_json"]) if track["artists_json"] else []
    first = artists[0] if artists else ""
    return f"{first} - {track['title']}" if first else track["title"]


def target_path(ctx: StepContext, track_file: TrackFile) -> Path:
    track = ctx.repo.get_track(track_file.track_id)
    folder = ctx.library_dir / TRACKS_DIR
    ext = base.EXTENSIONS[track_file.format or ""]
    room = MAX_PATH - len(str(folder)) - 1 - len(ext) - len(" [000000]")
    stem = safe_name(track_stem(track), max(20, min(MAX_NAME, room)))
    owners = {}
    for row in ctx.repo.file_paths():
        owners[os.path.normcase(os.path.normpath(row["file_path"]))] = row["track_id"]

    def taken(path: Path) -> bool:
        owner = owners.get(os.path.normcase(os.path.normpath(str(path))))
        if owner == track_file.track_id:
            return False  # our own earlier, interrupted placement
        return owner is not None or path.exists()

    path = folder / f"{stem}{ext}"
    if taken(path):
        path = folder / f"{stem} [{track_file.track_id:06d}]{ext}"
    return path


class Place:
    name = "place"
    fatal = True

    def process(self, track_file: TrackFile, ctx: StepContext) -> TrackFile:
        if track_file.format is None:
            raise StepError(tr("The file was not checked, it cannot go to the library."))
        target = target_path(ctx, track_file)
        if same_path(str(target), str(track_file.path)):
            return track_file
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # saved before the move, so an interrupted move is recognized as ours next time
            ctx.claim_path(track_file.track_id, target)
            # target_path gives an existing path only when this track owns it
            target.unlink(missing_ok=True)
            shutil.move(str(track_file.path), str(target))
        except OSError as exc:
            raise StepError(
                tr("Could not move the file to the library ({error}). Check the free space "
                   "and that the library folder is writable.").format(error=exc.strerror or exc)
            ) from exc  # fmt: skip
        track_file.path = target
        return track_file
