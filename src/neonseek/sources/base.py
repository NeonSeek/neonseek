"""types shared by every source. sources only build these, writing goes through ingest."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

# collections.kind
KIND_SPOTIFY_PLAYLIST = "spotify_playlist"
KIND_SPOTIFY_LIKED = "spotify_liked"
KIND_SPOTIFY_ALBUM = "spotify_album"
KIND_SINGLES = "singles"
KIND_CSV = "csv"
# track lists read from links of other services, the audio still comes from soulseek
KIND_APPLE_ALBUM = "apple_album"
KIND_APPLE_PLAYLIST = "apple_playlist"
KIND_YOUTUBE_PLAYLIST = "youtube_playlist"
KIND_SOUNDCLOUD_SET = "soundcloud_set"
KIND_TIDAL_ALBUM = "tidal_album"
KIND_TIDAL_PLAYLIST = "tidal_playlist"
WEB_KINDS = (
    KIND_APPLE_ALBUM,
    KIND_APPLE_PLAYLIST,
    KIND_YOUTUBE_PLAYLIST,
    KIND_SOUNDCLOUD_SET,
    KIND_TIDAL_ALBUM,
    KIND_TIDAL_PLAYLIST,
)

# names neonseek gives its own lists. the database keeps them in english, every place that shows
# a collection name to the user goes through display_name (D-180)
SINGLES_NAME = "Single tracks"
LIKED_NAME = "Liked Songs"


def display_name(kind: str | None, name: str | None) -> str:
    """the name a user reads: the built in lists in the language of the window."""
    from neonseek.i18n import tr

    if kind == KIND_SINGLES and name in (None, "", SINGLES_NAME):
        return tr("Single tracks")
    if kind == KIND_SPOTIFY_LIKED and name in (None, "", LIKED_NAME):
        return tr("Liked Songs")
    return name or ""


def row_name(row) -> str:
    """display_name of a collection row from the database."""
    return display_name(row["kind"], row["name"])


# collections.origin
ORIGIN_ACCOUNT = "account"
ORIGIN_URL = "url"
ORIGIN_FILE = "file"
ORIGIN_BUILTIN = "builtin"


@dataclass(frozen=True)
class TrackRecord:
    title: str
    artists: tuple[str, ...] = ()
    album: str | None = None
    album_artists: tuple[str, ...] = ()
    release_date: str | None = None
    duration_ms: int | None = None
    track_number: int | None = None
    disc_number: int | None = None
    cover_url: str | None = None
    explicit: bool | None = None
    spotify_id: str | None = None
    isrc: str | None = None
    # "youtube:<id>", "soundcloud:<id>", "apple:<id>" or "tidal:<id>": finds the same track on
    # the next sync
    source_id: str | None = None
    # belongs to the position in the collection, not to the track
    added_at: str | None = None


@dataclass
class CollectionRecord:
    kind: str
    origin: str
    name: str
    external_id: str | None = None
    source_ref: str | None = None
    owner_id: str | None = None
    is_readable: bool = True
    snapshot_id: str | None = None
    import_map: dict[str, Any] | None = None
    image_url: str | None = None
    track_count: int | None = None
    # none means the content was not read, existing links stay as they are
    tracks: list[TrackRecord] | None = field(default=None)


class Source(Protocol):
    """anything that turns some input into collections with ordered tracks."""

    def collections(self) -> Iterable[CollectionRecord]: ...
