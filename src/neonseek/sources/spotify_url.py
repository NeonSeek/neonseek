"""pasted spotify links: playlist, album or single track (PLAN.md section 6.3)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from neonseek.db.repo import Repo
from neonseek.errors import NeonSeekError
from neonseek.i18n import tr
from neonseek.sources import base, ingest
from neonseek.sources.base import (
    KIND_SPOTIFY_ALBUM,
    KIND_SPOTIFY_PLAYLIST,
    ORIGIN_URL,
    CollectionRecord,
    TrackRecord,
)
from neonseek.sources.spotify_common import (
    ALBUM_TRACKS_LIMIT,
    FAILED,
    UPDATED,
    CollectionSync,
    Progress,
    fetch_playlist_tracks,
    locked_playlist_message,
    missing_playlist_message,
    noop_progress,
)
from neonseek.spotify import models
from neonseek.spotify.client import SpotifyApiError, SpotifyClient
from neonseek.spotify.urls import SpotifyRef, parse_spotify_ref

ADDED = "added"
EXISTS = "exists"

# albums have no snapshot_id, this marks one whose tracks were all read
ALBUM_COMPLETE = "album-complete"


class UnreadableLinkError(NeonSeekError):
    pass


@dataclass
class AddResult:
    text: str
    outcome: str  # added | exists | failed
    kind: str | None = None
    collection_id: int | None = None
    name: str | None = None
    tracks: int = 0
    message: str | None = None
    result: ingest.IngestResult | None = None
    # things to know about an added list (apple music, youtube, soundcloud, tidal)
    notes: list[str] = field(default_factory=list)


def canonical_url(ref: SpotifyRef) -> str:
    # the ?si= share id is never stored
    return f"{models.OPEN_URL}/{ref.kind}/{ref.id}"


def _album_name(album: dict[str, Any]) -> str:
    artists = ", ".join(a["name"] for a in album.get("artists") or [] if a.get("name"))
    name = album.get("name") or album["id"]
    return f"{artists} - {name}" if artists else name


def fetch_album(
    client: SpotifyClient, album_id: str, progress: Progress = noop_progress
) -> tuple[dict[str, Any], list[TrackRecord]]:
    """album object and its tracks with isrc. album tracks come without external_ids, so every
    track is read again on its own (the batch endpoint is gone), one after another."""
    album = client.get(f"/albums/{album_id}")
    simple = [
        t
        for t in client.items(f"/albums/{album_id}/tracks", {"limit": ALBUM_TRACKS_LIMIT})
        if isinstance(t, dict) and t.get("id")
    ]
    tracks: list[TrackRecord] = []
    for index, item in enumerate(simple, start=1):
        progress(
            tr("Reading album tracks ({index}/{total})...").format(index=index, total=len(simple))
        )
        try:
            full = client.get(f"/tracks/{item['id']}")
        except SpotifyApiError as exc:
            if exc.status != 404:
                raise
            full = None  # gone from the catalog, keep what the album listed
        track = models.parse_track(full or item, album=album)
        if track is not None:
            tracks.append(track)
    return album, tracks


def _album_record(album: dict[str, Any], tracks: list[TrackRecord], ref_url: str):
    return CollectionRecord(
        kind=KIND_SPOTIFY_ALBUM,
        origin=ORIGIN_URL,
        name=_album_name(album),
        external_id=album["id"],
        source_ref=ref_url,
        owner_id=((album.get("artists") or [{}])[0]).get("id"),
        snapshot_id=ALBUM_COMPLETE,
        image_url=models.best_image(album.get("images")),
        track_count=album.get("total_tracks"),
        tracks=tracks,
    )


def refresh_album(
    repo: Repo, client: SpotifyClient, row: sqlite3.Row, progress: Progress = noop_progress
) -> CollectionSync:
    sync = CollectionSync(row["id"], base.row_name(row), row["kind"], UPDATED)
    try:
        album, tracks = fetch_album(client, row["external_id"], progress)
    except SpotifyApiError as exc:
        sync.outcome, sync.message = FAILED, exc.message
        return sync
    ref_url = row["source_ref"] or f"{models.OPEN_URL}/album/{row['external_id']}"
    sync.result = ingest.ingest_collection(
        repo, _album_record(album, tracks, ref_url), collection_id=row["id"]
    )
    return sync


def _not_found(kind: str) -> UnreadableLinkError:
    return UnreadableLinkError(
        tr(
            "Spotify could not find this {kind}. Check that the link is complete and that the "
            "{kind} is still available on Spotify."
        ).format(kind=kind)
    )


def _add_playlist(repo: Repo, client: SpotifyClient, ref: SpotifyRef) -> AddResult:
    result = AddResult(text=ref.uri, outcome=ADDED, kind=KIND_SPOTIFY_PLAYLIST)
    try:
        obj = client.get(f"/playlists/{ref.id}")
        if not models.has_content(obj):
            raise UnreadableLinkError(locked_playlist_message())
        existing = repo.find_collection(KIND_SPOTIFY_PLAYLIST, ref.id)
        if existing is not None and existing["snapshot_id"] == obj.get("snapshot_id"):
            with repo.transaction():
                repo.update_collection(existing["id"], {"is_readable": 1})
            result.outcome = EXISTS
            result.collection_id = int(existing["id"])
            result.name = base.row_name(existing)
            result.tracks = len(repo.collection_entries(existing["id"]))
            return result
        tracks, _ = fetch_playlist_tracks(client, ref.id)
    except SpotifyApiError as exc:
        if exc.status == 404:
            raise UnreadableLinkError(missing_playlist_message()) from exc
        if exc.status == 403:
            raise UnreadableLinkError(locked_playlist_message()) from exc
        raise
    record = models.playlist_record(
        obj, origin=ORIGIN_URL, tracks=tracks, source_ref=canonical_url(ref)
    )
    outcome = ingest.ingest_collection(repo, record)
    result.outcome = ADDED if outcome.created else EXISTS
    result.collection_id = outcome.collection_id
    result.name = record.name
    result.tracks = outcome.tracks_total
    result.result = outcome
    return result


def _add_album(repo: Repo, client: SpotifyClient, ref: SpotifyRef, progress: Progress) -> AddResult:
    result = AddResult(text=ref.uri, outcome=ADDED, kind=KIND_SPOTIFY_ALBUM)
    existing = repo.find_collection(KIND_SPOTIFY_ALBUM, ref.id)
    if existing is not None and existing["snapshot_id"]:
        result.outcome = EXISTS
        result.collection_id = int(existing["id"])
        result.name = base.row_name(existing)
        result.tracks = len(repo.collection_entries(existing["id"]))
        return result
    try:
        album, tracks = fetch_album(client, ref.id, progress)
    except SpotifyApiError as exc:
        if exc.status == 404:
            raise _not_found(tr("album")) from exc
        raise
    record = _album_record(album, tracks, canonical_url(ref))
    outcome = ingest.ingest_collection(repo, record)
    result.outcome = ADDED if outcome.created else EXISTS
    result.collection_id = outcome.collection_id
    result.name = record.name
    result.tracks = outcome.tracks_total
    result.result = outcome
    return result


def _add_track(repo: Repo, client: SpotifyClient, ref: SpotifyRef) -> AddResult:
    try:
        obj = client.get(f"/tracks/{ref.id}")
    except SpotifyApiError as exc:
        if exc.status == 404:
            raise _not_found(tr("track")) from exc
        raise
    track = models.parse_track(obj)
    if track is None:
        raise UnreadableLinkError(
            tr("This Spotify link is not a music track. Paste a link to a song instead.")
        )
    single = ingest.add_single(repo, track)
    return AddResult(
        text=ref.uri,
        outcome=EXISTS if single.already_listed else ADDED,
        kind="track",
        collection_id=single.collection_id,
        name=", ".join(track.artists) + " - " + track.title if track.artists else track.title,
        tracks=1,
    )


def add_url(
    repo: Repo, client: SpotifyClient, text: str, progress: Progress = noop_progress
) -> AddResult:
    ref = parse_spotify_ref(text)
    if ref.kind == "playlist":
        result = _add_playlist(repo, client, ref)
    elif ref.kind == "album":
        result = _add_album(repo, client, ref, progress)
    else:
        result = _add_track(repo, client, ref)
    result.text = text.strip()
    return result


def add_urls(
    repo: Repo, client: SpotifyClient, texts: Iterable[str], progress: Progress = noop_progress
) -> list[AddResult]:
    """each link on its own, an error in one does not stop the others."""
    results = []
    for text in texts:
        if not text.strip():
            continue
        progress(tr("Adding {link}...").format(link=text.strip()))
        try:
            results.append(add_url(repo, client, text, progress))
        except NeonSeekError as exc:
            results.append(AddResult(text=text.strip(), outcome=FAILED, message=exc.message))
    return results
