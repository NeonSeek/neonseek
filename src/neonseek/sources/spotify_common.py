"""pieces shared by the spotify account and spotify url sources."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from neonseek.i18n import tr
from neonseek.sources import ingest
from neonseek.sources.base import TrackRecord
from neonseek.spotify import models
from neonseek.spotify.client import SpotifyClient

# largest page sizes the api accepts, checked in docs/spotify-notes.md
PLAYLISTS_LIMIT = 50
PLAYLIST_ITEMS_LIMIT = 100
SAVED_TRACKS_LIMIT = 50
ALBUM_TRACKS_LIMIT = 50

# answers that mean "spotify does not share this", anything else is a normal error
LOCKED_STATUSES = (403, 404)

Progress = Callable[[str], None]

UPDATED = "updated"
UNCHANGED = "unchanged"
LOCKED = "locked"
FAILED = "failed"


def noop_progress(_: str) -> None:
    pass


@dataclass
class CollectionSync:
    collection_id: int
    name: str
    kind: str
    outcome: str  # updated | unchanged | locked | failed
    result: ingest.IngestResult | None = None
    skipped_entries: int = 0  # episodes and local files
    message: str | None = None


def fetch_playlist_tracks(client: SpotifyClient, playlist_id: str) -> tuple[list[TrackRecord], int]:
    entries = list(client.items(f"/playlists/{playlist_id}/items", {"limit": PLAYLIST_ITEMS_LIMIT}))
    return models.parse_entries(entries)


def locked_playlist_message() -> str:
    # 403 or a playlist object without items
    return tr(
        "Spotify does not share the tracks of this playlist. Copy its tracks into your own "
        "playlist in Spotify, or import them from a CSV file."
    )


def missing_playlist_message() -> str:
    # spotify answers 404 both for its own playlists and for ones that do not exist
    return tr(
        "Spotify does not share this playlist. Playlists made by Spotify itself (like Daily "
        "Mix, Discover Weekly, Release Radar or editorial playlists), private playlists of "
        "other people and deleted playlists cannot be read. Copy the tracks into your own "
        "playlist in Spotify, or import them from a CSV file."
    )
