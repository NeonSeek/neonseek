"""pasted apple music, youtube, soundcloud and tidal links: playlists, albums, sets and single
tracks.

only the list of tracks comes from these services, the audio from soulseek. a collection
starts unselected like every other one, a sync reads selected ones again (D-227).
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from neonseek.db.repo import Repo
from neonseek.errors import NeonSeekError
from neonseek.i18n import ntr, tr
from neonseek.sources import apple, base, ingest, tidal
from neonseek.sources import links as weblinks
from neonseek.sources.base import (
    KIND_APPLE_ALBUM,
    KIND_APPLE_PLAYLIST,
    KIND_SOUNDCLOUD_SET,
    KIND_TIDAL_ALBUM,
    KIND_TIDAL_PLAYLIST,
    KIND_YOUTUBE_PLAYLIST,
    ORIGIN_URL,
    WEB_KINDS,
    CollectionRecord,
    TrackRecord,
)
from neonseek.sources.spotify_url import ADDED, EXISTS, AddResult
from neonseek.sources.titles import split_artist_title, strip_artist_prefix
from neonseek.sources.ytdlp import BatchUnavailable, Extractor, WebSourceError, problem, what_label

Progress = Callable[[str], None]

UPDATED = "updated"
UNCHANGED = "unchanged"
FAILED = "failed"

# an apple or tidal album whose tracks were all read, like spotify albums it does not change
ALBUM_COMPLETE = "album-complete"
# longer entries are mixes or whole sets, soulseek rarely has them as one file (D-229)
LONG_ENTRY_S = 20 * 60
# the slow soundcloud path: this many tracks, then a pause, so soundcloud is not hammered
SLOW_CHUNK = 5
SLOW_PAUSE_S = 3.0

KIND_SERVICE = {
    KIND_APPLE_ALBUM: weblinks.APPLE,
    KIND_APPLE_PLAYLIST: weblinks.APPLE,
    KIND_YOUTUBE_PLAYLIST: weblinks.YOUTUBE,
    KIND_SOUNDCLOUD_SET: weblinks.SOUNDCLOUD,
    KIND_TIDAL_ALBUM: weblinks.TIDAL,
    KIND_TIDAL_PLAYLIST: weblinks.TIDAL,
}
# names on these services are exact, nothing is guessed from upload titles
EXACT_SERVICES = (weblinks.APPLE, weblinks.TIDAL)
ALBUM_KINDS = (KIND_APPLE_ALBUM, KIND_TIDAL_ALBUM)


def noop_progress(_: str) -> None:
    pass


@dataclass
class WebClients:
    """what reading these services needs. tests put fakes in, nothing may go online there."""

    http: httpx.Client  # apple music and tidal
    extractor: Extractor  # yt-dlp for youtube and soundcloud
    sleep: Callable[[float], None] = field(default=lambda seconds: time.sleep(seconds))
    clock: Callable[[], float] = field(default=time.monotonic)
    tidal_pacer: tidal.Pacer = field(init=False)

    def __post_init__(self) -> None:
        self.tidal_pacer = tidal.Pacer(sleep=self.sleep, clock=self.clock)


@dataclass
class WebList:
    kind: str
    external_id: str
    name: str
    source_ref: str
    tracks: list[TrackRecord]
    image_url: str | None = None
    owner_id: str | None = None
    reported: int | None = None
    genre: str | None = None
    complete: bool = True
    notes: list[str] = field(default_factory=list)


@dataclass
class WebSync:
    collection_id: int
    name: str
    kind: str
    outcome: str  # updated | unchanged | failed
    result: ingest.IngestResult | None = None
    message: str | None = None
    notes: list[str] = field(default_factory=list)


def is_web_kind(kind: str | None) -> bool:
    return kind in WEB_KINDS


def kind_service(kind: str) -> str:
    return KIND_SERVICE[kind]


# youtube and soundcloud entries


def _seconds(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def _ms(value: Any) -> int | None:
    seconds = _seconds(value)
    return round(seconds * 1000) if seconds else None


def _date(timestamp: Any) -> str | None:
    if not isinstance(timestamp, (int, float)) or timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, UTC).date().isoformat()


def _names(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return ()
    return tuple(v.strip() for v in value if isinstance(v, str) and v.strip())


_GONE_TITLES = ("[deleted video]", "[private video]", "[unavailable video]")


def youtube_record(entry: dict[str, Any]) -> TrackRecord | None:
    """a video as a track, none when it is deleted or private."""
    video_id = entry.get("id")
    title = entry.get("title")
    if not video_id or not isinstance(title, str) or not title.strip():
        return None
    if title.strip().casefold() in _GONE_TITLES:
        return None
    channel = entry.get("channel") or entry.get("uploader")
    artists = _names(entry.get("artists")) or _names(entry.get("artist"))
    track = entry.get("track")
    if artists and isinstance(track, str) and track.strip():
        # youtube music knows the track: no guessing needed
        song = track.strip()
    else:
        parsed = split_artist_title(title, channel if isinstance(channel, str) else None)
        artists, song = parsed.artists, parsed.title
    album = entry.get("album")
    return TrackRecord(
        title=song,
        artists=artists,
        album=album.strip() if isinstance(album, str) and album.strip() else None,
        duration_ms=_ms(entry.get("duration")),
        source_id=f"youtube:{video_id}",
    )


def _soundcloud_cover(info: dict[str, Any]) -> str | None:
    thumbs = [t for t in info.get("thumbnails") or [] if isinstance(t, dict) and t.get("url")]
    for wanted in ("t500x500", "crop", "original"):
        found = next((t["url"] for t in thumbs if t.get("id") == wanted), None)
        if found:
            return found
    return None


def soundcloud_record(info: dict[str, Any]) -> TrackRecord | None:
    track_id = info.get("id")
    title = info.get("title") or info.get("track")
    if not track_id or not isinstance(title, str) or not title.strip():
        return None
    uploader = info.get("uploader") if isinstance(info.get("uploader"), str) else None
    publisher = _names(info.get("artists"))
    if publisher:
        # the label filled in the artist, the title often repeats it
        artists, song = publisher, strip_artist_prefix(title.strip(), publisher[0])
    else:
        parsed = split_artist_title(title, uploader)
        artists, song = parsed.artists, parsed.title
    album = info.get("album")
    return TrackRecord(
        title=song,
        artists=artists,
        album=album.strip() if isinstance(album, str) and album.strip() else None,
        release_date=_date(info.get("release_timestamp")),
        duration_ms=_ms(info.get("duration")),
        cover_url=info.get("cover_url") or _soundcloud_cover(info),
        isrc=info.get("isrc") if isinstance(info.get("isrc"), str) else None,
        source_id=f"soundcloud:{track_id}",
    )


def _long_note(tracks: list[TrackRecord]) -> list[str]:
    count = sum(1 for t in tracks if t.duration_ms and t.duration_ms > LONG_ENTRY_S * 1000)
    if not count:
        return []
    return [
        ntr(
            count,
            "{count} entry is longer than 20 minutes, probably a mix or a whole set. Soulseek "
            "rarely has those as one file, exclude it if you do not need it.",
            "{count} entries are longer than 20 minutes, probably mixes or whole sets. Soulseek "
            "rarely has those as one file, exclude them if you do not need them.",
        ).format(count=count)
    ]


def guess_note() -> str:
    return tr(
        "Artist and title were taken from the titles of the uploads, so Soulseek finds fewer "
        "of these tracks than of Spotify or Apple Music ones. Check them before downloading: "
        "right-click a track and choose Edit artist and title."
    )


def _thumbnail(info: dict[str, Any]) -> str | None:
    thumbs = [t for t in info.get("thumbnails") or [] if isinstance(t, dict) and t.get("url")]
    if not thumbs:
        return None
    best = max(thumbs, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
    return best["url"]


def _extract(web: WebClients, service: str, url: str, playlist: bool) -> dict[str, Any]:
    try:
        info = web.extractor.extract(url)
    except NeonSeekError:
        raise
    except Exception as exc:  # noqa: BLE001 - yt-dlp raises many kinds, none may crash the app
        raise problem(service, exc, what_label(service, playlist)) from exc
    if not isinstance(info, dict):
        raise WebSourceError(
            tr("{service} answered in a form NeonSeek cannot read. Try again later.").format(
                service=weblinks.service_name(service)
            )
        )
    return info


def _youtube_list(info: dict[str, Any]) -> WebList:
    entries = [e for e in info.get("entries") or [] if isinstance(e, dict)]
    tracks = [t for t in (youtube_record(e) for e in entries) if t is not None]
    notes: list[str] = []
    gone = len(entries) - len(tracks)
    if gone:
        notes.append(
            ntr(
                gone,
                "{count} video of this playlist is unavailable (deleted or private) and was "
                "left out.",
                "{count} videos of this playlist are unavailable (deleted or private) and were "
                "left out.",
            ).format(count=gone)
        )
    reported = info.get("playlist_count") if isinstance(info.get("playlist_count"), int) else None
    complete = reported is None or len(entries) >= reported
    if not complete:
        notes.append(
            tr(
                "YouTube listed {got} of {count} entries of this playlist. Sync it later to "
                "read the rest."
            ).format(got=len(entries), count=reported)
        )
    notes += _long_note(tracks)
    playlist_id = str(info.get("id") or "")
    return WebList(
        kind=KIND_YOUTUBE_PLAYLIST,
        external_id=playlist_id,
        name=(info.get("title") or playlist_id).strip(),
        source_ref=weblinks.WebRef(weblinks.YOUTUBE, weblinks.PLAYLIST, playlist_id).url,
        tracks=tracks,
        image_url=_thumbnail(info),
        owner_id=info.get("channel_id") or info.get("uploader_id"),
        reported=reported,
        complete=complete,
        notes=notes,
    )


def _soundcloud_details(
    web: WebClients, entries: list[dict[str, Any]], progress: Progress
) -> tuple[dict[str, dict[str, Any]], bool]:
    """metadata of the set's tracks: one request per 50 tracks, or one by one when yt-dlp
    changed inside (with pauses). the flag tells whether the slow way was taken."""
    ids = [str(e["id"]) for e in entries if e.get("id")]
    progress(tr("Reading track details from SoundCloud..."))
    try:
        return web.extractor.soundcloud_tracks(ids), False
    except BatchUnavailable:
        pass
    except NeonSeekError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise problem(weblinks.SOUNDCLOUD, exc, tr("set")) from exc
    found: dict[str, dict[str, Any]] = {}
    for start in range(0, len(entries), SLOW_CHUNK):
        if start:
            web.sleep(SLOW_PAUSE_S)
        progress(
            tr("Reading SoundCloud tracks one by one ({done}/{total})...").format(
                done=min(start + SLOW_CHUNK, len(entries)), total=len(entries)
            )
        )
        for entry in entries[start : start + SLOW_CHUNK]:
            url = entry.get("url") or entry.get("webpage_url")
            if not url or not entry.get("id"):
                continue
            try:
                info = web.extractor.resolve(url)
            except Exception:  # noqa: BLE001 - one track that fails is counted, not fatal
                continue
            if isinstance(info, dict):
                found[str(entry["id"])] = info
    return found, True


def _soundcloud_list(web: WebClients, info: dict[str, Any], progress: Progress) -> WebList:
    entries = [e for e in info.get("entries") or [] if isinstance(e, dict)]
    details, slow = _soundcloud_details(web, entries, progress)
    tracks: list[TrackRecord] = []
    for entry in entries:
        found = details.get(str(entry.get("id")))
        record = soundcloud_record({**entry, **found}) if found else None
        if record is not None:
            tracks.append(record)
    notes: list[str] = []
    missing = len(entries) - len(tracks)
    if missing:
        notes.append(
            ntr(
                missing,
                "{count} track of this set could not be read (removed, private or blocked in "
                "your country) and was left out.",
                "{count} tracks of this set could not be read (removed, private or blocked in "
                "your country) and were left out.",
            ).format(count=missing)
        )
    if slow:
        notes.append(
            tr(
                "SoundCloud details were read track by track, which is slower. A newer version "
                "of NeonSeek may read them faster again."
            )
        )
    reported = info.get("playlist_count") if isinstance(info.get("playlist_count"), int) else None
    notes += _long_note(tracks)
    set_id = str(info.get("id") or "")
    page = info.get("webpage_url") if isinstance(info.get("webpage_url"), str) else None
    return WebList(
        kind=KIND_SOUNDCLOUD_SET,
        external_id=set_id,
        name=(info.get("title") or set_id).strip(),
        source_ref=page or f"https://soundcloud.com/{set_id}",
        tracks=tracks,
        image_url=_thumbnail(info),
        owner_id=info.get("uploader_id"),
        reported=reported,
        complete=missing == 0,
        notes=notes,
    )


def _apple_list(web: WebClients, ref: weblinks.WebRef, progress: Progress) -> WebList:
    country = ref.country or weblinks.DEFAULT_COUNTRY
    if ref.kind == weblinks.ALBUM:
        found = apple.fetch_album(web.http, ref.id, country, ref.url, progress)
        kind = KIND_APPLE_ALBUM
    else:
        found = apple.fetch_playlist(web.http, ref.id, country, ref.url, progress)
        kind = KIND_APPLE_PLAYLIST
    return WebList(
        kind=kind,
        external_id=found.external_id,
        name=found.name,
        source_ref=ref.url,
        tracks=found.tracks,
        image_url=found.image_url,
        owner_id=found.owner_id,
        reported=found.reported,
        genre=found.genre,
        complete=found.complete,
        notes=found.notes,
    )


def _tidal_list(web: WebClients, ref: weblinks.WebRef, progress: Progress) -> WebList:
    album = ref.kind == weblinks.ALBUM
    found = tidal.fetch_list(web.http, web.tidal_pacer, ref.id, album, progress)
    return WebList(
        kind=KIND_TIDAL_ALBUM if album else KIND_TIDAL_PLAYLIST,
        external_id=found.external_id,
        name=found.name,
        source_ref=ref.url,
        tracks=found.tracks,
        image_url=found.image_url,
        owner_id=found.owner_id,
        complete=found.complete,
        notes=found.notes,
    )


def read(web: WebClients, ref: weblinks.WebRef, progress: Progress) -> WebList | TrackRecord:
    """a whole list, or one track for a single song, video or soundcloud track."""
    if ref.service == weblinks.TIDAL:
        if ref.kind == weblinks.TRACK:
            return tidal.fetch_track(web.http, web.tidal_pacer, ref.id, progress)
        return _tidal_list(web, ref, progress)
    if ref.service == weblinks.APPLE:
        if ref.kind == weblinks.TRACK:
            return apple.fetch_song(web.http, ref.id, ref.country or weblinks.DEFAULT_COUNTRY)
        return _apple_list(web, ref, progress)
    playlist = ref.kind == weblinks.PLAYLIST
    if ref.service == weblinks.YOUTUBE:
        progress(tr("Reading the playlist from YouTube...") if playlist else
                 tr("Reading the video from YouTube..."))  # fmt: skip
        info = _extract(web, ref.service, ref.url, playlist)
        if info.get("_type") == "playlist":
            return _youtube_list(info)
        record = youtube_record(info)
    else:
        progress(tr("Reading from SoundCloud..."))
        info = _extract(web, ref.service, weblinks.soundcloud_url(ref), playlist)
        if info.get("_type") == "playlist":
            return _soundcloud_list(web, info, progress)
        record = soundcloud_record(info)
    if record is None:
        raise WebSourceError(
            tr("{service} shares no title for this {what}, it may be deleted or private.").format(
                service=weblinks.service_name(ref.service),
                what=what_label(ref.service, playlist),
            )
        )
    return record


def snapshot(found: WebList) -> str | None:
    """the same tracks in the same order read before: nothing to save again (D-227)."""
    if found.kind in ALBUM_KINDS:
        return ALBUM_COMPLETE if found.complete else None
    digest = hashlib.sha256()
    for track in found.tracks:
        key = (track.source_id or "", track.title, "|".join(track.artists),
               str(track.duration_ms or ""))  # fmt: skip
        digest.update("\x1f".join(key).encode("utf-8") + b"\x1e")
    return f"web:{len(found.tracks)}:{digest.hexdigest()[:32]}"


def _record(found: WebList) -> CollectionRecord:
    return CollectionRecord(
        kind=found.kind,
        origin=ORIGIN_URL,
        name=found.name,
        external_id=found.external_id,
        source_ref=found.source_ref,
        owner_id=found.owner_id,
        snapshot_id=snapshot(found),
        image_url=found.image_url,
        track_count=found.reported if found.reported is not None else len(found.tracks),
        tracks=found.tracks,
    )


def _ingest(repo: Repo, found: WebList, collection_id: int | None = None) -> ingest.IngestResult:
    result = ingest.ingest_collection(repo, _record(found), collection_id=collection_id)
    if result.created and found.genre:
        # the genre apple gives an album is the starting tag, the user can change it
        with repo.transaction():
            repo.update_collection(result.collection_id, {"genre": found.genre})
    return result


def _track_name(track: TrackRecord) -> str:
    return ", ".join(track.artists) + " - " + track.title if track.artists else track.title


def add_web_url(
    repo: Repo, web: WebClients, text: str, progress: Progress = noop_progress
) -> AddResult:
    ref = weblinks.parse_web_ref(text)
    found = read(web, ref, progress)
    if isinstance(found, TrackRecord):
        single = ingest.add_single(repo, found)
        result = AddResult(
            text=text.strip(),
            outcome=EXISTS if single.already_listed else ADDED,
            kind="track",
            collection_id=single.collection_id,
            name=_track_name(found),
            tracks=1,
        )
        if ref.service not in EXACT_SERVICES:
            result.notes.append(guess_note())
        return result
    if not found.external_id:
        raise WebSourceError(apple.changed_message() if ref.service == weblinks.APPLE else
                             tr("This list has no id, NeonSeek cannot keep it."))  # fmt: skip
    existing = repo.find_collection(found.kind, found.external_id)
    outcome = _ingest(repo, found)
    result = AddResult(
        text=text.strip(),
        outcome=ADDED if existing is None else EXISTS,
        kind=found.kind,
        collection_id=outcome.collection_id,
        name=found.name,
        tracks=outcome.tracks_total,
        result=outcome,
        notes=list(found.notes),
    )
    if ref.service not in EXACT_SERVICES and found.tracks:
        result.notes.append(guess_note())
    return result


def _ref_of(row: sqlite3.Row) -> weblinks.WebRef:
    return weblinks.parse_web_ref(row["source_ref"] or "")


def unexpected_message(exc: BaseException) -> str:
    return tr(
        "Reading this link went wrong in a way NeonSeek did not expect ({error}). Try again "
        "later. If it keeps happening, copy the diagnostics from About and report it."
    ).format(error=f"{type(exc).__name__}: {exc}"[:200])


def refresh(
    repo: Repo, web: WebClients, row: sqlite3.Row, progress: Progress = noop_progress
) -> WebSync:
    """reads one collection again. the same content is not saved again."""
    sync = WebSync(int(row["id"]), base.row_name(row), row["kind"], UPDATED)
    if row["kind"] in ALBUM_KINDS and row["snapshot_id"] == ALBUM_COMPLETE:
        sync.outcome = UNCHANGED  # albums do not change, only an incomplete one is read again
        return sync
    try:
        found = read(web, _ref_of(row), progress)
    except NeonSeekError as exc:
        sync.outcome, sync.message = FAILED, exc.message
        return sync
    except Exception as exc:  # noqa: BLE001 - a source that changed must not stop the sync
        sync.outcome, sync.message = FAILED, unexpected_message(exc)
        return sync
    if isinstance(found, TrackRecord):
        sync.outcome, sync.message = FAILED, tr("This link is a single track now, not a list.")
        return sync
    if row["snapshot_id"] and snapshot(found) == row["snapshot_id"]:
        sync.outcome = UNCHANGED
        return sync
    # the notes were shown when the list was added, again only when it changed
    sync.notes = list(found.notes)
    sync.result = _ingest(repo, found, collection_id=int(row["id"]))
    return sync


def sync_web(
    repo: Repo,
    web_factory: Callable[[], WebClients],
    *,
    collection_id: int | None = None,
    progress: Progress = noop_progress,
) -> list[WebSync]:
    """selected apple music, youtube, soundcloud and tidal collections, or the one given."""
    if collection_id is not None:
        row = repo.get_collection(collection_id)
        targets = [row] if row is not None and is_web_kind(row["kind"]) else []
    else:
        targets = [row for kind in WEB_KINDS for row in repo.collections_of_kind(kind)
                   if row["selected"]]  # fmt: skip
    if not targets:
        return []
    web = web_factory()
    results = []
    for row in targets:
        progress(tr("Syncing {name}...").format(name=base.row_name(row)))
        results.append(refresh(repo, web, row, progress))
    return results
