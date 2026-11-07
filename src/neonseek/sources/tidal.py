"""tidal track lists from the public embed pages (embed.tidal.com), without an account or a key
(docs/tidal-notes.md, D-238). only the track list comes from tidal, the audio from soulseek.

the embed page is html built on the server: one <list-item> per entry with the title (version
or remix included), artist links and m:ss, plus a json object with album and artwork per track.
it lists at most 50 entries and says nothing about the rest (D-240).
"""

from __future__ import annotations

import html as htmllib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from neonseek.errors import NeonSeekError
from neonseek.i18n import ntr, tr
from neonseek.sources.base import TrackRecord

EMBED_URL = "https://embed.tidal.com"
# the embed page stops at this many entries, with no hint about more
PAGE_LIMIT = 50
# a short pause between two requests to tidal, so a sync of many lists does not hammer it
PAUSE_S = 1.0
# covers come as jpeg in fixed sizes, this one is close to what spotify gives
COVER_SIZE = 640

_LIST = re.compile(r"<media-item-list\b[^>]*>(?P<body>.*?)</media-item-list>", re.S)
_ITEM = re.compile(r"<list-item\b(?P<attrs>[^>]*)>(?P<body>.*?)</list-item>", re.S)
_ATTR = re.compile(r'([\w-]+)="([^"]*)"')
_TITLE_SLOT = re.compile(r'<span\b[^>]*\bslot="title"[^>]*>(?P<text>.*?)</span>', re.S)
_ARTIST_SLOT = re.compile(r'<span\b[^>]*\bslot="artist"[^>]*>(?P<text>.*?)</span>', re.S)
_DURATION_SLOT = re.compile(r'<time\b[^>]*\bslot="duration"[^>]*>(?P<text>.*?)</time>', re.S)
_ARTIST_LINK = re.compile(r'<a\b[^>]*\bhref="[^"]*/artist/[^"]*"[^>]*>(?P<name>.*?)</a>', re.S)
_HEADING = re.compile(r"<h1\b(?P<attrs>[^>]*)>(?P<text>.*?)</h1>", re.S)
_HEAD_ARTIST = re.compile(r'<span\b[^>]*\bclass="media-artist"[^>]*>(?P<text>.*?)</span>', re.S)
_COVER = re.compile(r'<img\b[^>]*\bclass="cover-art"[^>]*>', re.S)
_SRCSET = re.compile(r'\bsrcset="(?P<set>[^"]*)"')
_TRACK_SECONDS = re.compile(r"<tidal-duration-time>\s*(?P<s>\d+)\s*</tidal-duration-time>")
_METADATA = re.compile(r"window\.tidalMediaSessionMetadata\s*=\s*")
_TAG = re.compile(r"<[^>]+>")
_ERROR_PAGE = "embed-player--showing-error"
_EXPLICIT = re.compile(r'\bslot="explicit-badge"|class="badge explicit"')

Progress = Callable[[str], None]


class TidalError(NeonSeekError):
    pass


def changed_message() -> str:
    return tr(
        "TIDAL answered in a form NeonSeek cannot read, TIDAL may have changed its pages. Try "
        "again later or check for a newer version of NeonSeek. Meanwhile you can export the "
        "playlist to a CSV file and import that."
    )


def truncated_message() -> str:
    return tr(
        "TIDAL shows at most 50 tracks of a playlist or album, NeonSeek read those. The list "
        "may be longer. Split the playlist on TIDAL into playlists of up to 50 tracks, or "
        "export it to a CSV file and import that."
    )


def not_found_message(what: str) -> str:
    return tr(
        "TIDAL could not find this {what}. Check that the link is complete. A playlist must be "
        "public, NeonSeek cannot read private ones."
    ).format(what=what)


@dataclass
class Pacer:
    """keeps PAUSE_S between two requests to tidal."""

    sleep: Callable[[float], None]
    clock: Callable[[], float] = time.monotonic
    gap: float = PAUSE_S
    last: float | None = None

    def wait(self) -> None:
        if self.last is not None:
            left = self.gap - (self.clock() - self.last)
            if left > 0:
                self.sleep(left)
        self.last = self.clock()


@dataclass
class TidalList:
    external_id: str
    name: str
    tracks: list[TrackRecord]
    image_url: str | None = None
    owner_id: str | None = None
    entries: int = 0  # list items on the page, videos included
    videos: int = 0
    complete: bool = True
    notes: list[str] = field(default_factory=list)


def _text(fragment: str | None) -> str:
    """plain text of a piece of html, entities decoded."""
    if not fragment:
        return ""
    return " ".join(htmllib.unescape(_TAG.sub("", fragment)).split())


def _attrs(raw: str) -> dict[str, str]:
    return {name: htmllib.unescape(value) for name, value in _ATTR.findall(raw)}


def _artists(fragment: str | None) -> tuple[str, ...]:
    """names of the artist links, one link per artist. plain text when there are no links."""
    if not fragment:
        return ()
    names = [_text(m.group("name")) for m in _ARTIST_LINK.finditer(fragment)]
    if not names:
        names = [_text(fragment)]
    return _unique(n for n in names if n)


def _unique(names) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    for name in names:
        seen.setdefault(name.casefold(), name)
    return tuple(seen.values())


def duration_ms(text: str | None) -> int | None:
    """m:ss or h:mm:ss as milliseconds."""
    parts = (text or "").strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds * 1000 if seconds > 0 else None


def _metadata(page: str) -> dict[str, dict[str, Any]]:
    """album and artwork per track id. a page without it still lists the tracks."""
    match = _METADATA.search(page)
    if match is None:
        return {}
    try:
        found, _ = json.JSONDecoder().raw_decode(page, match.end())
    except ValueError:
        return {}
    if not isinstance(found, dict):
        return {}
    return {str(k): v for k, v in found.items() if isinstance(v, dict)}


def _size(label: Any) -> int:
    match = re.match(r"(\d+)", str(label or ""))
    return int(match.group(1)) if match else 0


def _pick(sized: list[tuple[int, str]]) -> str | None:
    """the smallest picture of at least COVER_SIZE, the biggest one when all are smaller."""
    sized = [(size, url) for size, url in sized if url]
    if not sized:
        return None
    big = sorted((s for s in sized if s[0] >= COVER_SIZE), key=lambda s: s[0])
    return big[0][1] if big else max(sized, key=lambda s: s[0])[1]


def _artwork(meta: dict[str, Any]) -> str | None:
    items = [a for a in meta.get("artwork") or [] if isinstance(a, dict)]
    return _pick(
        [(_size(a.get("sizes")), a.get("src")) for a in items if isinstance(a.get("src"), str)]
    )


def _header_cover(page: str) -> str | None:
    img = _COVER.search(page)
    if img is None:
        return None
    srcset = _SRCSET.search(img.group(0))
    if srcset is None:
        src = _attrs(img.group(0)).get("src")
        return src or None
    sized = []
    for piece in htmllib.unescape(srcset.group("set")).split(","):
        bits = piece.split()
        if len(bits) == 2:
            sized.append((_size(bits[1]), bits[0]))
    return _pick(sized)


def _heading(page: str) -> str:
    match = _HEADING.search(page)
    return _text(match.group("text")) if match else ""


def _header_artists(page: str) -> tuple[str, ...]:
    match = _HEAD_ARTIST.search(page)
    return _artists(match.group("text")) if match else ()


def _meta_artists(meta: dict[str, Any]) -> tuple[str, ...]:
    """the artists of the metadata, only when the html has none: one text joined by commas,
    so a name with a comma in it would be split."""
    text = meta.get("artist")
    if not isinstance(text, str):
        return ()
    return _unique(name.strip() for name in text.split(", ") if name.strip())


def _album_text(meta: dict[str, Any]) -> str | None:
    album = meta.get("album")
    return album.strip() if isinstance(album, str) and album.strip() else None


def _check_page(page: str, what: str) -> None:
    if _ERROR_PAGE in page:
        raise TidalError(not_found_message(what))


def parse_list(page: str, list_id: str, album: bool) -> TidalList:
    """an album or playlist embed page. videos are counted and left out."""
    _check_page(page, tr("album") if album else tr("playlist"))
    body = _LIST.search(page)
    if body is None:
        raise TidalError(changed_message())
    meta = _metadata(page)
    main_artists = _header_artists(page) if album else ()
    found = TidalList(external_id=list_id, name=_heading(page) or list_id, tracks=[])
    items = list(_ITEM.finditer(body.group("body")))
    found.entries = len(items)
    unreadable = 0
    for item in items:
        attrs = _attrs(item.group("attrs"))
        kind = attrs.get("product-type", "track")
        if kind == "video":
            found.videos += 1
            continue
        track_id = attrs.get("product-id", "")
        title = _TITLE_SLOT.search(item.group("body"))
        if kind != "track" or not track_id or title is None or not _text(title.group("text")):
            unreadable += 1
            continue
        artist = _ARTIST_SLOT.search(item.group("body"))
        duration = _DURATION_SLOT.search(item.group("body"))
        info = meta.get(track_id, {})
        # an album page leaves its own artist out of each track, guests stay
        artists = _unique(main_artists + _artists(artist.group("text") if artist else ""))
        found.tracks.append(
            TrackRecord(
                title=_text(title.group("text")),
                artists=artists or _meta_artists(info),
                album=_album_text(info) or (found.name if album else None),
                album_artists=main_artists,
                duration_ms=duration_ms(duration.group("text") if duration else None),
                cover_url=_artwork(info),
                explicit=bool(_EXPLICIT.search(item.group("body"))),
                source_id=f"tidal:{track_id}",
            )
        )
    if unreadable and not found.tracks:
        # entries are there, but none could be read: the page changed
        raise TidalError(changed_message())
    if unreadable:
        found.notes.append(
            ntr(
                unreadable,
                "{count} entry of this list could not be read and was left out.",
                "{count} entries of this list could not be read and were left out.",
            ).format(count=unreadable)
        )
    found.image_url = _header_cover(page)
    if album:
        artist = ", ".join(main_artists)
        found.name = f"{artist} - {found.name}" if artist else found.name
    if found.videos:
        found.notes.append(
            ntr(
                found.videos,
                "{count} video of this list was left out, NeonSeek reads only tracks.",
                "{count} videos of this list were left out, NeonSeek reads only tracks.",
            ).format(count=found.videos)
        )
    if found.entries >= PAGE_LIMIT:
        found.complete = False
        found.notes.append(truncated_message())
    return found


def parse_track(page: str, track_id: str) -> TrackRecord:
    _check_page(page, tr("track"))
    title = ""
    for match in _HEADING.finditer(page):
        if "media-title" in match.group("attrs"):
            title = _text(match.group("text"))
            break
    if not title:
        raise TidalError(changed_message())
    seconds = _TRACK_SECONDS.search(page)
    info = _metadata(page).get(track_id, {})
    return TrackRecord(
        title=title,
        artists=_header_artists(page),
        album=_album_text(info),
        duration_ms=int(seconds.group("s")) * 1000 if seconds and int(seconds.group("s")) else None,
        cover_url=_artwork(info) or _header_cover(page),
        explicit=bool(_EXPLICIT.search(page)),
        source_id=f"tidal:{track_id}",
    )


def _get(http: httpx.Client, pacer: Pacer, url: str, what: str) -> str:
    pacer.wait()
    try:
        response = http.get(url)
    except httpx.TimeoutException as exc:
        raise TidalError(
            tr("TIDAL did not answer in time. Check the internet connection and try again.")
        ) from exc
    except httpx.HTTPError as exc:
        raise TidalError(
            tr("No connection to TIDAL ({error}). Check the internet connection and try again.")
            .format(error=exc)
        ) from exc  # fmt: skip
    status = response.status_code
    if status == 404:
        raise TidalError(not_found_message(what))
    if status in (401, 403, 429):
        raise TidalError(
            tr(
                "TIDAL refused the request (HTTP {status}). Wait a few minutes and try again."
            ).format(status=status)
        )
    if status >= 500:
        raise TidalError(
            tr("TIDAL has a problem right now (HTTP {status}). Try again later.").format(
                status=status
            )
        )
    if status != 200:
        raise TidalError(changed_message())
    return response.text


def fetch_list(
    http: httpx.Client, pacer: Pacer, list_id: str, album: bool, progress: Progress
) -> TidalList:
    if album:
        progress(tr("Reading the album from TIDAL..."))
        page = _get(http, pacer, f"{EMBED_URL}/albums/{list_id}", tr("album"))
    else:
        progress(tr("Reading the playlist from TIDAL..."))
        page = _get(http, pacer, f"{EMBED_URL}/playlists/{list_id}", tr("playlist"))
    return parse_list(page, list_id, album)


def fetch_track(http: httpx.Client, pacer: Pacer, track_id: str, progress: Progress) -> TrackRecord:
    progress(tr("Reading the track from TIDAL..."))
    return parse_track(_get(http, pacer, f"{EMBED_URL}/tracks/{track_id}", tr("track")), track_id)
