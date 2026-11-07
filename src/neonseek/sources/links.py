"""links of apple music, youtube, soundcloud and tidal, next to the spotify ones in spotify/urls.py.

only what the link means is kept. share and tracking parameters (si=, utm_*, ref=, feature=,
in=...) are dropped, because every service keeps just a few parameters that matter (D-227).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from neonseek.errors import NeonSeekError
from neonseek.i18n import tr
from neonseek.spotify import urls as spotify_urls

APPLE = "apple"
YOUTUBE = "youtube"
SOUNDCLOUD = "soundcloud"
TIDAL = "tidal"
SPOTIFY = "spotify"

# kinds of a link
ALBUM = "album"
PLAYLIST = "playlist"
TRACK = "track"
# a soundcloud link: a set or a track, the answer tells which
SOUNDCLOUD_LINK = "link"

APPLE_HOSTS = ("music.apple.com", "itunes.apple.com", "geo.music.apple.com")
YOUTUBE_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com")
YOUTU_BE = "youtu.be"
SOUNDCLOUD_HOSTS = ("soundcloud.com", "www.soundcloud.com", "m.soundcloud.com")
SOUNDCLOUD_SHORT = "on.soundcloud.com"
TIDAL_HOSTS = ("tidal.com", "www.tidal.com", "listen.tidal.com")
# storefront used when an apple link has no country in it
DEFAULT_COUNTRY = "us"

_APPLE_ID = re.compile(r"^\d{1,15}$")
_APPLE_PLAYLIST_ID = re.compile(r"^pl\.[0-9A-Za-z.-]{6,}$")
_COUNTRY = re.compile(r"^[a-z]{2}$")
_TIDAL_ID = re.compile(r"^\d{1,15}$")
_TIDAL_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# tidal pages that change for every listener or are not one list: only a message
_TIDAL_UNSUPPORTED = ("mix", "artist")
_VIDEO_ID = re.compile(r"^[0-9A-Za-z_-]{11}$")
_LIST_ID = re.compile(r"^[0-9A-Za-z_-]{2,}$")
# a mix youtube makes on its own: endless and different for everyone, so only the video counts
_MIX_PREFIX = "RD"
# the first path part of soundcloud pages that are not a user
_SOUNDCLOUD_PAGES = {"discover", "search", "stream", "you", "upload", "charts", "pages",
                     "settings", "messages", "notifications", "people", "tags", "stations",
                     "jobs", "imprint", "terms-of-use", "mobile"}  # fmt: skip
# second path parts of a user page that are lists of the user, not one set or track
_SOUNDCLOUD_USER_PAGES = {"likes", "tracks", "albums", "reposts", "popular-tracks", "followers",
                          "following", "comments", "spotlight", "playlists"}  # fmt: skip


class UnsupportedWebLinkError(NeonSeekError):
    pass


@dataclass(frozen=True)
class WebRef:
    service: str  # apple | youtube | soundcloud | tidal
    kind: str  # album | playlist | track | link
    id: str  # album, playlist, track or video id, the path for soundcloud
    country: str | None = None  # apple storefront from the link

    @property
    def url(self) -> str:
        """the clean link that is stored and opened, without share parameters."""
        if self.service == APPLE:
            country = self.country or DEFAULT_COUNTRY
            if self.kind == PLAYLIST:
                return f"https://music.apple.com/{country}/playlist/{self.id}"
            if self.kind == ALBUM:
                return f"https://music.apple.com/{country}/album/{self.id}"
            return f"https://music.apple.com/{country}/song/{self.id}"
        if self.service == TIDAL:
            return f"https://tidal.com/{self.kind}/{self.id}"
        if self.service == YOUTUBE:
            if self.kind == PLAYLIST:
                return f"https://www.youtube.com/playlist?list={self.id}"
            return f"https://www.youtube.com/watch?v={self.id}"
        return f"https://soundcloud.com/{self.id}"


def service_name(service: str) -> str:
    names = {APPLE: "Apple Music", YOUTUBE: "YouTube", SOUNDCLOUD: "SoundCloud", TIDAL: "TIDAL"}
    return names.get(service, "Spotify")


def _host(raw: str) -> tuple[str, list[str], dict[str, list[str]]]:
    url = raw if "://" in raw else f"https://{raw}"
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    segments = [s for s in parsed.path.split("/") if s]
    return host, segments, parse_qs(parsed.query)


def _first(query: dict[str, list[str]], name: str) -> str:
    values = query.get(name) or [""]
    return values[0].strip()


def _unsupported(service: str, raw: str) -> UnsupportedWebLinkError:
    texts = {
        APPLE: tr(
            "This Apple Music link is not an album, a playlist or a song: {text}. Open the "
            "album, playlist or song in Apple Music, use Share > Copy Link and paste it again."
        ),
        YOUTUBE: tr(
            "This YouTube link is not a playlist or a video: {text}. Open the playlist or the "
            "video, use Share > Copy and paste it again. Channels cannot be added."
        ),
        SOUNDCLOUD: tr(
            "This SoundCloud link is not a set (playlist) or a track: {text}. Open the set or "
            "the track, use Share > Copy link and paste it again. Profiles cannot be added."
        ),
        TIDAL: tr(
            "This TIDAL link is not a playlist, an album or a track: {text}. Open the playlist, "
            "album or track in TIDAL, use Share > Copy link and paste it again."
        ),
    }
    return UnsupportedWebLinkError(texts[service].format(text=raw))


def _apple(raw: str, segments: list[str], query: dict[str, list[str]]) -> WebRef:
    country = None
    if segments and _COUNTRY.match(segments[0].lower()):
        country = segments[0].lower()
        segments = segments[1:]
    if len(segments) < 2:
        raise _unsupported(APPLE, raw)
    kind, item_id = segments[0].lower(), segments[-1]
    if kind == "album" and _APPLE_ID.match(item_id):
        track = _first(query, "i")
        if track:
            if not _APPLE_ID.match(track):
                raise _unsupported(APPLE, raw)
            return WebRef(APPLE, TRACK, track, country)
        return WebRef(APPLE, ALBUM, item_id, country)
    if kind == "song" and _APPLE_ID.match(item_id):
        return WebRef(APPLE, TRACK, item_id, country)
    if kind == "playlist" and _APPLE_PLAYLIST_ID.match(item_id):
        return WebRef(APPLE, PLAYLIST, item_id, country)
    raise _unsupported(APPLE, raw)


def _youtube_ref(raw: str, video: str, playlist: str) -> WebRef:
    # a video opened from a playlist means the playlist, a mix of youtube only the video
    if playlist and not playlist.startswith(_MIX_PREFIX):
        if not _LIST_ID.match(playlist):
            raise _unsupported(YOUTUBE, raw)
        return WebRef(YOUTUBE, PLAYLIST, playlist)
    if video and _VIDEO_ID.match(video):
        return WebRef(YOUTUBE, TRACK, video)
    raise _unsupported(YOUTUBE, raw)


def _youtube(raw: str, host: str, segments: list[str], query: dict[str, list[str]]) -> WebRef:
    playlist = _first(query, "list")
    if host == YOUTU_BE:
        return _youtube_ref(raw, segments[0] if segments else "", playlist)
    first = segments[0].lower() if segments else ""
    if first in ("playlist", "watch"):
        return _youtube_ref(raw, _first(query, "v"), playlist)
    if first in ("shorts", "live", "embed", "v") and len(segments) > 1:
        return _youtube_ref(raw, segments[1], "")
    raise _unsupported(YOUTUBE, raw)


def _soundcloud(raw: str, host: str, segments: list[str]) -> WebRef:
    if host == SOUNDCLOUD_SHORT:
        if not segments:
            raise _unsupported(SOUNDCLOUD, raw)
        # a short link points to a set or a track, yt-dlp follows it
        return WebRef(SOUNDCLOUD, SOUNDCLOUD_LINK, f"short:{segments[0]}")
    if len(segments) < 2 or segments[0].lower() in _SOUNDCLOUD_PAGES:
        raise _unsupported(SOUNDCLOUD, raw)
    second = segments[1].lower()
    if second == "sets":
        if len(segments) < 3:
            raise _unsupported(SOUNDCLOUD, raw)
        # a private set has its secret token as the fourth part
        return WebRef(SOUNDCLOUD, PLAYLIST, "/".join(segments[:4]))
    if second in _SOUNDCLOUD_USER_PAGES:
        raise _unsupported(SOUNDCLOUD, raw)
    return WebRef(SOUNDCLOUD, TRACK, "/".join(segments[:3]))


def _tidal(raw: str, segments: list[str]) -> WebRef:
    parts = [s.lower() for s in segments]
    if parts and parts[0] == "browse":
        parts, segments = parts[1:], segments[1:]
    if parts and parts[-1] == "u":
        # the share link ends with /u, it means nothing
        parts, segments = parts[:-1], segments[:-1]
    if parts and parts[0] in _TIDAL_UNSUPPORTED:
        raise UnsupportedWebLinkError(
            tr(
                "TIDAL mixes and artist pages cannot be added: {text}. Open a playlist, an album "
                "or a track in TIDAL, use Share > Copy link and paste it again."
            ).format(text=raw)
        )
    if len(parts) == 2 and parts[0] == "playlist" and _TIDAL_UUID.match(parts[1]):
        return WebRef(TIDAL, PLAYLIST, parts[1])
    if len(parts) == 2 and parts[0] in ("album", "track") and _TIDAL_ID.match(parts[1]):
        return WebRef(TIDAL, ALBUM if parts[0] == "album" else TRACK, parts[1])
    if (
        len(parts) == 4
        and (parts[0], parts[2]) == ("album", "track")
        and _TIDAL_ID.match(parts[1])
        and _TIDAL_ID.match(parts[3])
    ):
        return WebRef(TIDAL, TRACK, parts[3])
    raise _unsupported(TIDAL, raw)


def service_of(text: str) -> str | None:
    """which service a pasted piece of text belongs to, none when it is not a link of one."""
    raw = text.strip()
    if not raw:
        return None
    if raw.lower().startswith("spotify:"):
        return SPOTIFY
    host, _, _ = _host(raw)
    if host == "open.spotify.com" or host in ("spotify.link", "spotify.app.link"):
        return SPOTIFY
    if host in APPLE_HOSTS:
        return APPLE
    if host in YOUTUBE_HOSTS or host == YOUTU_BE:
        return YOUTUBE
    if host in SOUNDCLOUD_HOSTS or host == SOUNDCLOUD_SHORT:
        return SOUNDCLOUD
    if host in TIDAL_HOSTS:
        return TIDAL
    return None


def parse_web_ref(text: str) -> WebRef:
    raw = text.strip()
    host, segments, query = _host(raw)
    if host in APPLE_HOSTS:
        return _apple(raw, segments, query)
    if host in YOUTUBE_HOSTS or host == YOUTU_BE:
        return _youtube(raw, host, segments, query)
    if host in SOUNDCLOUD_HOSTS or host == SOUNDCLOUD_SHORT:
        return _soundcloud(raw, host, segments)
    if host in TIDAL_HOSTS:
        return _tidal(raw, segments)
    raise UnsupportedWebLinkError(not_a_link_message(raw))


def not_a_link_message(text: str) -> str:
    return tr(
        "This is not a link NeonSeek can read: {text}. Paste a link to a playlist, album or "
        "track from Spotify, Apple Music, YouTube, SoundCloud or TIDAL."
    ).format(text=text)


def soundcloud_url(ref: WebRef) -> str:
    """the address yt-dlp reads, also for a short link."""
    if ref.id.startswith("short:"):
        return f"https://{SOUNDCLOUD_SHORT}/{ref.id.removeprefix('short:')}"
    return ref.url


def find_links(text: str) -> list[str]:
    """pieces of pasted text that look like links of a supported service, valid or not.
    adding them reports a problem per link, so nothing is dropped quietly."""
    found = [
        piece
        for piece in text.split()
        if spotify_urls.find_links(piece)
        or service_of(piece) in (APPLE, YOUTUBE, SOUNDCLOUD, TIDAL)
    ]
    return list(dict.fromkeys(found))
