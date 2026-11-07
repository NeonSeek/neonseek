"""apple music track lists: albums and songs from the itunes lookup api, playlists from the
public page (docs/playlist-sources-notes.md, D-230). no key and no apple account needed."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx

from neonseek.errors import NeonSeekError
from neonseek.i18n import tr
from neonseek.sources.base import TrackRecord

LOOKUP_URL = "https://itunes.apple.com/lookup"
# an album lookup returns at most this many songs, a bigger limit changes nothing
ALBUM_LOOKUP_LIMIT = 200
# ids in one lookup with several ids (150 answered in full when checked)
IDS_PER_LOOKUP = 150
# the playlist page lists at most this many tracks
PAGE_TRACKS = 300
HTTP_TIMEOUT_S = 30.0
# covers come as jpeg in any size, close to what spotify gives
COVER_SIZE = 640

_SERVER_DATA = re.compile(
    r'<script[^>]*\bid="serialized-server-data"[^>]*>(?P<json>.*?)</script>', re.S
)
_ARTWORK_SIZE = re.compile(r"/\d+x\d+bb\.(?P<ext>jpg|jpeg|png|webp)$", re.IGNORECASE)

Progress = Callable[[str], None]


class AppleError(NeonSeekError):
    pass


def new_web_client() -> httpx.Client:
    """plain client for apple music pages and the lookup api, without spotify's token."""
    return httpx.Client(timeout=HTTP_TIMEOUT_S, follow_redirects=True)


def changed_message() -> str:
    return tr(
        "Apple Music answered in a form NeonSeek cannot read, Apple may have changed its "
        "pages. Try again later or check for a newer version of NeonSeek. Meanwhile you can "
        "export the playlist from iTunes or the Music app as a .txt file and import it."
    )


def truncated_message() -> str:
    return tr(
        "Apple Music shows only the first 300 tracks of a playlist, NeonSeek read those. For "
        "the whole playlist export it from iTunes or the Music app (File > Library > Export "
        "Playlist, Unicode text) and import the .txt file, or split it into playlists of up "
        "to 300 tracks."
    )


def _not_found(what: str, country: str) -> AppleError:
    return AppleError(
        tr(
            "Apple Music could not find this {what}. Check that the link is complete and that "
            "it is available in the Apple Music store of the country in the link ({country})."
        ).format(what=what, country=country.upper())
    )


def _get(http: httpx.Client, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
    try:
        response = http.get(url, params=params)
    except httpx.TimeoutException as exc:
        raise AppleError(
            tr("Apple Music did not answer in time. Check the internet connection and try again.")
        ) from exc
    except httpx.HTTPError as exc:
        raise AppleError(
            tr(
                "No connection to Apple Music ({error}). Check the internet connection and try "
                "again."
            ).format(error=exc)
        ) from exc
    if response.status_code in (403, 429):
        raise AppleError(
            tr(
                "Apple Music refused the request (HTTP {status}). Wait a few minutes and try again."
            ).format(status=response.status_code)
        )
    if response.status_code >= 500:
        raise AppleError(
            tr("Apple Music has a problem right now (HTTP {status}). Try again later.").format(
                status=response.status_code
            )
        )
    return response


def lookup(
    http: httpx.Client,
    ids: Iterable[str],
    country: str,
    entity: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"id": ",".join(ids), "country": country}
    if entity:
        params["entity"] = entity
    if limit:
        params["limit"] = limit
    response = _get(http, LOOKUP_URL, params)
    if response.status_code != 200:
        raise AppleError(changed_message())
    try:
        results = response.json().get("results")
    except (ValueError, AttributeError) as exc:
        raise AppleError(changed_message()) from exc
    if not isinstance(results, list):
        raise AppleError(changed_message())
    return [item for item in results if isinstance(item, dict)]


def lookup_songs(
    http: httpx.Client, ids: list[str], country: str, progress: Progress
) -> dict[str, dict[str, Any]]:
    """songs by id, in batches. ids apple does not know are missing from the answer."""
    found: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), IDS_PER_LOOKUP):
        batch = ids[start : start + IDS_PER_LOOKUP]
        progress(
            tr("Reading track details from Apple Music ({done}/{total})...").format(
                done=min(start + len(batch), len(ids)), total=len(ids)
            )
        )
        for item in lookup(http, batch, country):
            if item.get("wrapperType") == "track" and item.get("trackId") is not None:
                found[str(item["trackId"])] = item
    return found


def cover_url(url: str | None) -> str | None:
    """the same artwork in a bigger size, still the jpeg apple serves."""
    if not url:
        return None
    return _ARTWORK_SIZE.sub(
        lambda m: f"/{COVER_SIZE}x{COVER_SIZE}bb.{m.group('ext')}", url, count=1
    )


def _artwork_template(artwork: Any) -> str | None:
    template = ((artwork or {}).get("dictionary") or {}).get("url")
    if not isinstance(template, str) or not template:
        return None
    return (
        template.replace("{w}", str(COVER_SIZE))
        .replace("{h}", str(COVER_SIZE))
        .replace("{f}", "jpg")
    )


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def song_record(item: dict[str, Any], album_artist: str | None = None) -> TrackRecord | None:
    """a lookup song as a track. music videos and anything without a name are left out."""
    if item.get("wrapperType") != "track" or item.get("kind") not in (None, "song"):
        return None
    title = (item.get("trackName") or "").strip()
    if not title:
        return None
    artist = (item.get("artistName") or "").strip()
    owner = (item.get("collectionArtistName") or album_artist or artist or "").strip()
    explicit = item.get("trackExplicitness")
    release = item.get("releaseDate")
    return TrackRecord(
        title=title,
        artists=(artist,) if artist else (),
        album=(item.get("collectionName") or "").strip() or None,
        album_artists=(owner,) if owner else (),
        release_date=release[:10] if isinstance(release, str) and release else None,
        duration_ms=_int(item.get("trackTimeMillis")),
        track_number=_int(item.get("trackNumber")),
        disc_number=_int(item.get("discNumber")),
        cover_url=cover_url(item.get("artworkUrl100")),
        explicit=None if explicit is None else explicit == "explicit",
        source_id=f"apple:{item['trackId']}" if item.get("trackId") is not None else None,
    )


@dataclass
class AppleList:
    external_id: str
    name: str
    tracks: list[TrackRecord]
    reported: int | None = None  # number of tracks apple reports
    image_url: str | None = None
    owner_id: str | None = None
    genre: str | None = None
    complete: bool = True
    truncated: bool = False  # the playlist page stopped at 300
    notes: list[str] = field(default_factory=list)


def _sections(html: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """the sections of a music.apple.com page and the page object around them."""
    match = _SERVER_DATA.search(html)
    if match is None:
        raise AppleError(changed_message())
    try:
        page = json.loads(match.group("json"))["data"][0]["data"]
        sections = page["sections"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AppleError(changed_message()) from exc
    if not isinstance(sections, list):
        raise AppleError(changed_message())
    return [s for s in sections if isinstance(s, dict)], page


def _section(sections: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    return next((s for s in sections if s.get("itemKind") == kind), None)


def _items(section: dict[str, Any] | None) -> list[dict[str, Any]]:
    items = (section or {}).get("items") or []
    return [item for item in items if isinstance(item, dict)]


def _song_id(item: dict[str, Any]) -> str | None:
    ids = (item.get("contentDescriptor") or {}).get("identifiers") or {}
    value = ids.get("storeAdamID")
    return str(value) if value else None


def page_song_ids(html: str) -> list[str]:
    """song ids of an album or playlist page, in page order."""
    sections, _ = _sections(html)
    return [i for i in (_song_id(item) for item in _items(_section(sections, "trackLockup"))) if i]


def fetch_song(http: httpx.Client, track_id: str, country: str) -> TrackRecord:
    for item in lookup(http, [track_id], country):
        record = song_record(item)
        if record is not None:
            return record
    raise _not_found(tr("song"), country)


def fetch_album(
    http: httpx.Client, album_id: str, country: str, page_url: str, progress: Progress
) -> AppleList:
    progress(tr("Reading the album from Apple Music..."))
    results = lookup(http, [album_id], country, entity="song", limit=ALBUM_LOOKUP_LIMIT)
    album = next((r for r in results if r.get("wrapperType") == "collection"), None)
    if album is None:
        raise _not_found(tr("album"), country)
    songs = {str(r["trackId"]): r for r in results if r.get("wrapperType") == "track"
             and r.get("trackId") is not None}  # fmt: skip
    reported = _int(album.get("trackCount"))
    if reported and len(songs) < reported:
        # the lookup stops at 200 songs, the album page lists all of them
        page = _get(http, page_url)
        missing = [i for i in page_song_ids(page.text) if i not in songs]
        songs.update(lookup_songs(http, missing, country, progress))
    ordered = sorted(
        songs.values(),
        key=lambda s: (_int(s.get("discNumber")) or 0, _int(s.get("trackNumber")) or 0),
    )
    artist = (album.get("artistName") or "").strip()
    tracks = [t for t in (song_record(s, artist) for s in ordered) if t is not None]
    name = (album.get("collectionName") or album_id).strip()
    return AppleList(
        external_id=album_id,
        name=f"{artist} - {name}" if artist else name,
        tracks=tracks,
        reported=reported,
        image_url=cover_url(album.get("artworkUrl100")),
        owner_id=str(album["artistId"]) if album.get("artistId") is not None else None,
        genre=(album.get("primaryGenreName") or "").strip() or None,
        complete=not reported or len(songs) >= reported,
    )


def _page_record(item: dict[str, Any], song_id: str | None) -> TrackRecord | None:
    """a playlist entry when the lookup does not know the song: what the page shows."""
    title = (item.get("title") or "").strip() if isinstance(item.get("title"), str) else ""
    if not title:
        return None
    artist = item.get("artistName") if isinstance(item.get("artistName"), str) else ""
    links = item.get("tertiaryLinks") or []
    album = links[0].get("title") if links and isinstance(links[0], dict) else None
    return TrackRecord(
        title=title,
        artists=(artist.strip(),) if artist and artist.strip() else (),
        album=album.strip() if isinstance(album, str) and album.strip() else None,
        duration_ms=_int(item.get("duration")),
        cover_url=_artwork_template(item.get("artwork")),
        source_id=f"apple:{song_id}" if song_id else None,
    )


def parse_playlist_page(
    html: str, playlist_id: str
) -> tuple[AppleList, list[tuple[str | None, dict]]]:
    """the playlist header and its entries as (song id, page item). the page has no genre or
    track numbers, the lookup adds those."""
    sections, page = _sections(html)
    header_items = _items(_section(sections, "containerDetailHeaderLockup"))
    header = header_items[0] if header_items else {}
    track_section = _section(sections, "trackLockup")
    reported = _int(header.get("trackCount"))
    if track_section is None and reported:
        raise AppleError(changed_message())
    entries = [(_song_id(item), item) for item in _items(track_section)]
    if reported and not entries:
        raise AppleError(changed_message())
    name = header.get("title") if isinstance(header.get("title"), str) else None
    if not name:
        seo = (page.get("seoData") or {}).get("pageTitle")
        name = seo.split(" - ")[0] if isinstance(seo, str) and seo else playlist_id
    curators = header.get("subtitleLinks") or []
    curator = curators[0].get("title") if curators and isinstance(curators[0], dict) else None
    following = page.get("nextIntent") or {}
    truncated = isinstance(following, dict) and following.get("$kind") == "PlaylistPaginationIntent"
    result = AppleList(
        external_id=playlist_id,
        name=name.strip(),
        tracks=[],
        reported=reported,
        image_url=_artwork_template(header.get("artwork")),
        owner_id=curator if isinstance(curator, str) else None,
        truncated=truncated,
        complete=not truncated,
    )
    return result, entries


def fetch_playlist(
    http: httpx.Client, playlist_id: str, country: str, page_url: str, progress: Progress
) -> AppleList:
    progress(tr("Reading the playlist from Apple Music..."))
    response = _get(http, page_url)
    if response.status_code == 404:
        raise _not_found(tr("playlist"), country)
    if response.status_code != 200:
        raise AppleError(changed_message())
    result, entries = parse_playlist_page(response.text, playlist_id)
    ids = list(dict.fromkeys(i for i, _ in entries if i))
    details = lookup_songs(http, ids, country, progress) if ids else {}
    for song_id, item in entries:
        record = song_record(details[song_id]) if song_id in details else None
        record = record or _page_record(item, song_id)
        if record is not None:
            result.tracks.append(record)
    if result.truncated:
        result.notes.append(truncated_message())
    return result
