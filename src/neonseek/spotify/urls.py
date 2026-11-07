"""parsing of spotify links and uris (PLAN.md section 6.3)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from neonseek.errors import NeonSeekError
from neonseek.i18n import tr

SUPPORTED_KINDS = ("playlist", "album", "track")
# kinds we know about but do not import in the mvp
KNOWN_KINDS = (*SUPPORTED_KINDS, "artist", "show", "episode", "user", "audiobook", "chapter")
_ID = re.compile(r"^[0-9A-Za-z]{22}$")
_LANG = re.compile(r"^intl-[a-z]{2}(?:-[a-z]{2})?$", re.IGNORECASE)
_SHORT_HOSTS = ("spotify.link", "spotify.app.link")


class UnsupportedLinkError(NeonSeekError):
    pass


@dataclass(frozen=True)
class SpotifyRef:
    kind: str  # playlist | album | track
    id: str

    @property
    def uri(self) -> str:
        return f"spotify:{self.kind}:{self.id}"


def _unsupported_kind(kind: str) -> UnsupportedLinkError:
    return UnsupportedLinkError(
        tr(
            "This is a Spotify {kind} link. NeonSeek can add playlists, albums and single "
            "tracks. Paste a link to one of those instead."
        ).format(kind=kind)
    )


def _not_spotify(text: str) -> UnsupportedLinkError:
    return UnsupportedLinkError(
        tr(
            "This does not look like a Spotify link: {text}. Copy the link with "
            "Share > Copy link in Spotify and paste it again."
        ).format(text=text)
    )


def _bad_id(text: str) -> UnsupportedLinkError:
    return UnsupportedLinkError(
        tr("The Spotify link {text} is incomplete. Copy it again from Spotify.").format(text=text)
    )


def _check(kind: str, item_id: str, text: str) -> SpotifyRef:
    kind = kind.lower()
    if kind not in KNOWN_KINDS:
        raise _not_spotify(text)
    if kind not in SUPPORTED_KINDS:
        raise _unsupported_kind(kind)
    if not _ID.match(item_id):
        raise _bad_id(text)
    return SpotifyRef(kind, item_id)


def parse_spotify_ref(text: str) -> SpotifyRef:
    """accepts open.spotify.com links (with language segment and query) and spotify: uris."""
    raw = text.strip()
    if not raw:
        raise _not_spotify(raw)

    if raw.lower().startswith("spotify:"):
        parts = raw.split(":")
        # spotify:user:<name>:playlist:<id> is an old playlist uri form
        if len(parts) == 5 and parts[1].lower() == "user" and parts[3].lower() == "playlist":
            return _check("playlist", parts[4], raw)
        if len(parts) != 3:
            raise _not_spotify(raw)
        return _check(parts[1], parts[2], raw)

    url = raw if "://" in raw else f"https://{raw}"
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host in _SHORT_HOSTS:
        raise UnsupportedLinkError(
            tr(
                "Short spotify.link links are not supported. Open the link in a browser and "
                "copy the full open.spotify.com address, or use Share > Copy link in the "
                "Spotify desktop app."
            )
        )
    if host != "open.spotify.com":
        raise _not_spotify(raw)

    segments = [s for s in parsed.path.split("/") if s]
    if segments and _LANG.match(segments[0]):
        segments = segments[1:]
    # /user/<name>/playlist/<id> is an old playlist url form
    if len(segments) == 4 and segments[0].lower() == "user" and segments[2].lower() == "playlist":
        segments = segments[2:]
    if len(segments) < 2:
        if segments and segments[0].lower() in KNOWN_KINDS:
            raise _bad_id(raw)
        raise _not_spotify(raw)
    return _check(segments[0], segments[1], raw)


def parse_track_id(text: str | None) -> str | None:
    """spotify track id from a bare id, uri or url, or none. used for csv columns."""
    if not text:
        return None
    raw = text.strip()
    if _ID.match(raw):
        return raw
    try:
        ref = parse_spotify_ref(raw)
    except UnsupportedLinkError:
        return None
    return ref.id if ref.kind == "track" else None


def find_links(text: str) -> list[str]:
    """pieces of pasted text that look like spotify links or uris, valid or not. adding them
    reports a problem per link, so nothing that looks like spotify is dropped quietly."""
    found = []
    for piece in text.split():
        lower = piece.lower()
        if lower.startswith("spotify:") or any(host in lower for host in
                                               ("open.spotify.com", *_SHORT_HOSTS)):  # fmt: skip
            found.append(piece)
    return list(dict.fromkeys(found))
