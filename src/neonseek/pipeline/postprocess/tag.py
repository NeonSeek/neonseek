"""tag: writes tags rekordbox reads (PLAN.md section 8, D-061 to D-067).

flac gets vorbis comments, aiff id3, wav riff info plus id3. never id3 in flac. fields from the
source follow tags_from_source, genre and comment of the collections always win when set.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields
from pathlib import Path

import mutagen.id3 as id3
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.wave import WAVE

from neonseek.config import TAGS_FILL, TAGS_OFF
from neonseek.db.repo import Repo
from neonseek.pipeline.postprocess import base, riff_info
from neonseek.pipeline.postprocess.base import StepContext, TrackFile

ID3_VERSION = 3  # D-063
JOIN = ", "


@dataclass(frozen=True)
class TagValues:
    # from the source, written according to tags_from_source
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    date: str | None = None
    track_number: str | None = None
    disc_number: str | None = None
    isrc: str | None = None
    # set by the user per collection, always written when set
    genre: str | None = None
    comment: str | None = None


USER_FIELDS = ("genre", "comment")
SOURCE_FIELDS = tuple(f.name for f in fields(TagValues) if f.name not in USER_FIELDS)

# the first name is written, the others only count as "already filled"
VORBIS = {
    "title": ("TITLE",),
    "artist": ("ARTIST",),
    "album": ("ALBUM",),
    "album_artist": ("ALBUMARTIST", "ALBUM ARTIST"),
    "date": ("DATE", "YEAR"),
    "track_number": ("TRACKNUMBER",),
    "disc_number": ("DISCNUMBER",),
    "isrc": ("ISRC",),
    "genre": ("GENRE",),
    "comment": ("COMMENT",),
}
ID3_FRAMES = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "album_artist": "TPE2",
    "date": "TDRC",  # saved as TYER (and TDAT) in id3 2.3
    "track_number": "TRCK",
    "disc_number": "TPOS",
    "isrc": "TSRC",
    "genre": "TCON",
    "comment": "COMM",
}
# riff info has no album artist, disc or isrc (its ISRC key means "source")
RIFF_KEYS = {
    "title": "INAM",
    "artist": "IART",
    "album": "IPRD",
    "date": "ICRD",
    "track_number": "ITRK",
    "genre": "IGNR",
    "comment": "ICMT",
}


def join_unique(values: Iterable[str | None]) -> str | None:
    """unique values in their order, compared without case, joined with ", "."""
    seen: dict[str, str] = {}
    for value in values:
        text = " ".join((value or "").split())
        if text and text.casefold() not in seen:
            seen[text.casefold()] = text
    return JOIN.join(seen.values()) or None


def _names(raw: str | None) -> str | None:
    names = json.loads(raw) if raw else []
    return JOIN.join(n for n in names if n) or None


def tag_values(repo: Repo, track_id: int) -> TagValues:
    track = repo.get_track(track_id)
    if track is None:
        return TagValues()
    collections = repo.collections_of_track(track_id)
    return TagValues(
        title=track["title"] or None,
        artist=_names(track["artists_json"]),
        album=track["album"] or None,
        album_artist=_names(track["album_artists_json"]),
        date=track["release_date"] or None,
        track_number=str(track["track_number"]) if track["track_number"] else None,
        disc_number=str(track["disc_number"]) if track["disc_number"] else None,
        isrc=track["isrc"] or None,
        genre=join_unique(c["genre"] for c in collections),
        comment=join_unique(c["comment"] for c in collections),
    )


def wanted(
    values: TagValues, mode: str, filled: Callable[[str], bool], known: Iterable[str]
) -> dict[str, str]:
    """fields to write into one container. known limits them to what the container has."""
    updates = {}
    for name in known:
        value = getattr(values, name)
        if not value:
            continue  # nothing from the source or the user never clears a field
        if name in SOURCE_FIELDS and (mode == TAGS_OFF or (mode == TAGS_FILL and filled(name))):
            continue
        updates[name] = value
    return updates


def _write_vorbis(path: Path, values: TagValues, mode: str) -> bool:
    audio = FLAC(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    def filled(name: str) -> bool:
        return any(v.strip() for key in VORBIS[name] for v in tags.get(key, []))

    changed = False
    for name, value in wanted(values, mode, filled, VORBIS).items():
        key, *aliases = VORBIS[name]
        stale = [alias for alias in aliases if alias in tags]
        if tags.get(key) == [value] and not stale:
            continue
        # exactly one value under one name: repeated fields and other spellings go (D-084)
        for alias in stale:
            del tags[alias]
        tags[key] = [value]
        changed = True
    if changed:
        audio.save()
    return changed


def _comment_frames(tags: id3.ID3) -> list[str]:
    # the plain comment has an empty description, other COMM frames belong to other programs
    return [key for key, frame in tags.items() if key.startswith("COMM") and frame.desc == ""]


def _write_id3(path: Path, fmt: str, values: TagValues, mode: str) -> bool:
    audio = WAVE(path) if fmt == base.WAV else AIFF(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    def texts(name: str) -> list[str]:
        if name == "comment":
            frames = [tags[key] for key in _comment_frames(tags)]
        else:
            frames = tags.getall(ID3_FRAMES[name])
        return [str(t) for frame in frames for t in frame.text]

    def filled(name: str) -> bool:
        return any(t.strip() for t in texts(name))

    changed = False
    for name, value in wanted(values, mode, filled, ID3_FRAMES).items():
        if texts(name) == [value]:
            continue
        if name == "comment":
            for key in _comment_frames(tags):
                del tags[key]
            tags.add(id3.COMM(encoding=id3.Encoding.UTF16, lang="eng", desc="", text=[value]))
        else:
            frame = getattr(id3, ID3_FRAMES[name])
            tags.setall(ID3_FRAMES[name], [frame(encoding=id3.Encoding.UTF16, text=[value])])
        changed = True
    if changed:
        audio.save(v2_version=ID3_VERSION)
    return changed


def _write_riff_info(path: Path, values: TagValues, mode: str) -> bool:
    info = riff_info.read_info(path)

    def filled(name: str) -> bool:
        return bool(info.get(RIFF_KEYS[name], "").strip())

    updated = dict(info)
    for name, value in wanted(values, mode, filled, RIFF_KEYS).items():
        updated[RIFF_KEYS[name]] = value
    if updated == info:
        return False
    riff_info.write_info(path, updated)
    return True


def write_tags(path: Path, fmt: str, values: TagValues, mode: str) -> bool:
    """returns true when the file changed."""
    if fmt == base.FLAC:
        return _write_vorbis(path, values, mode)
    changed = False
    if fmt == base.WAV:
        # rekordbox reads only riff info in wav (D-062). it goes first, because the rewrite
        # also adds a missing pad byte that mutagen would trip over
        changed = _write_riff_info(path, values, mode)
    return _write_id3(path, fmt, values, mode) or changed


class WriteTags:
    name = "tag"
    fatal = False

    def process(self, track_file: TrackFile, ctx: StepContext) -> TrackFile:
        if track_file.format is None:
            return track_file
        values = tag_values(ctx.repo, track_file.track_id)
        write_tags(track_file.path, track_file.format, values, ctx.settings.tags_from_source)
        return track_file
