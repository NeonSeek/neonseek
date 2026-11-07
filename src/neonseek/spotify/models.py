"""turns spotify api objects into source records. shapes checked in docs/spotify-notes.md."""

from __future__ import annotations

from typing import Any

from neonseek.sources.base import KIND_SPOTIFY_PLAYLIST, CollectionRecord, TrackRecord

OPEN_URL = "https://open.spotify.com"


def entry_object(entry: dict[str, Any]) -> dict[str, Any] | None:
    # playlist entries carry the new "item" and the old "track", saved tracks only "track"
    obj = entry.get("item")
    if obj is None:
        obj = entry.get("track")
    return obj if isinstance(obj, dict) else None


def best_image(images: list[dict[str, Any]] | None) -> str | None:
    if not images:
        return None
    best = max(images, key=lambda image: (image.get("width") or 0) * (image.get("height") or 0))
    return best.get("url")


def _names(people: list[dict[str, Any]] | None) -> tuple[str, ...]:
    return tuple(p["name"] for p in people or [] if isinstance(p, dict) and p.get("name"))


def parse_track(
    obj: dict[str, Any] | None,
    *,
    album: dict[str, Any] | None = None,
    added_at: str | None = None,
) -> TrackRecord | None:
    """a track record, or none for episodes, local files and broken entries.

    album overrides the track's own album object (album tracks come without one).
    """
    if not obj or obj.get("type", "track") != "track" or obj.get("is_local"):
        return None
    if not obj.get("id") or not (obj.get("name") or "").strip():
        return None
    album = album if album is not None else obj.get("album") or {}
    return TrackRecord(
        title=obj["name"].strip(),
        artists=_names(obj.get("artists")),
        album=album.get("name") or None,
        album_artists=_names(album.get("artists")),
        release_date=album.get("release_date") or None,
        duration_ms=obj.get("duration_ms"),
        track_number=obj.get("track_number"),
        disc_number=obj.get("disc_number"),
        cover_url=best_image(album.get("images")),
        explicit=obj.get("explicit"),
        spotify_id=obj["id"],
        isrc=(obj.get("external_ids") or {}).get("isrc"),
        added_at=added_at,
    )


def parse_entry(entry: dict[str, Any]) -> TrackRecord | None:
    """one element of /playlists/{id}/items or /me/tracks."""
    if not isinstance(entry, dict) or entry.get("is_local"):
        return None
    return parse_track(entry_object(entry), added_at=entry.get("added_at"))


def parse_entries(entries: list[dict[str, Any]]) -> tuple[list[TrackRecord], int]:
    """track records and the number of skipped entries (episodes, local files)."""
    tracks: list[TrackRecord] = []
    skipped = 0
    for entry in entries:
        track = parse_entry(entry)
        if track is None:
            skipped += 1
        else:
            tracks.append(track)
    return tracks, skipped


def content_total(obj: dict[str, Any]) -> int | None:
    # listing and full objects carry the count under "items" (new) and "tracks" (old)
    for key in ("items", "tracks"):
        value = obj.get(key)
        if isinstance(value, dict) and isinstance(value.get("total"), int):
            return value["total"]
    return None


def has_content(playlist: dict[str, Any]) -> bool:
    """get /playlists/{id} of a readable playlist has an items paging object with a list."""
    value = playlist.get("items")
    return isinstance(value, dict) and isinstance(value.get("items"), list)


def playlist_url(playlist_id: str) -> str:
    return f"{OPEN_URL}/playlist/{playlist_id}"


def playlist_record(
    obj: dict[str, Any],
    *,
    origin: str,
    is_readable: bool = True,
    tracks: list[TrackRecord] | None = None,
    source_ref: str | None = None,
) -> CollectionRecord:
    return CollectionRecord(
        kind=KIND_SPOTIFY_PLAYLIST,
        origin=origin,
        name=obj.get("name") or obj["id"],
        external_id=obj["id"],
        source_ref=source_ref,
        owner_id=(obj.get("owner") or {}).get("id"),
        is_readable=is_readable,
        snapshot_id=obj.get("snapshot_id"),
        image_url=best_image(obj.get("images")),
        track_count=content_total(obj),
        tracks=tracks,
    )
