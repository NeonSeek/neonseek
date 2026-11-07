"""playlists of the logged in account plus liked songs (PLAN.md section 6.2)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from neonseek.db.repo import Repo
from neonseek.i18n import tr
from neonseek.sources import base, ingest
from neonseek.sources.base import (
    KIND_SPOTIFY_ALBUM,
    KIND_SPOTIFY_LIKED,
    KIND_SPOTIFY_PLAYLIST,
    ORIGIN_ACCOUNT,
    CollectionRecord,
)
from neonseek.sources.spotify_common import (
    FAILED,
    LOCKED,
    LOCKED_STATUSES,
    PLAYLISTS_LIMIT,
    SAVED_TRACKS_LIMIT,
    UNCHANGED,
    UPDATED,
    CollectionSync,
    Progress,
    fetch_playlist_tracks,
    locked_playlist_message,
    missing_playlist_message,
    noop_progress,
)
from neonseek.sources.spotify_url import refresh_album
from neonseek.spotify import models
from neonseek.spotify.client import SpotifyApiError, SpotifyClient

LIKED_NAME = base.LIKED_NAME


@dataclass
class SyncReport:
    playlists_listed: int = 0
    playlists_new: int = 0
    locked: list[str] = field(default_factory=list)  # names of playlists spotify does not share
    collections: list[CollectionSync] = field(default_factory=list)


def liked_fingerprint(first_page: dict[str, Any]) -> str:
    """saved tracks have no snapshot_id. a like adds a newest entry, an unlike lowers total."""
    total = first_page.get("total") or 0
    items = first_page.get("items") or []
    if not items:
        return f"liked:{total}"
    first = items[0]
    obj = models.entry_object(first) or {}
    return f"liked:{total}:{first.get('added_at')}:{obj.get('id')}"


def _probe_readable(client: SpotifyClient, playlist_id: str) -> bool | None:
    """true or false when spotify answers clearly, none when we could not tell."""
    try:
        client.get(f"/playlists/{playlist_id}/items", {"limit": 1})
    except SpotifyApiError as exc:
        if exc.status in LOCKED_STATUSES:
            return False
        return None
    return True


def _set_readable(repo: Repo, collection_id: int, readable: bool) -> None:
    with repo.transaction():
        repo.update_collection(collection_id, {"is_readable": int(readable)})


def _locked(repo: Repo, sync: CollectionSync, status: int | None) -> CollectionSync:
    _set_readable(repo, sync.collection_id, False)
    sync.outcome = LOCKED
    sync.message = missing_playlist_message() if status == 404 else locked_playlist_message()
    return sync


def sync_playlist(
    repo: Repo,
    client: SpotifyClient,
    row: sqlite3.Row,
    listed_obj: dict[str, Any] | None = None,
) -> CollectionSync:
    """reads the content of one playlist when its snapshot differs from the stored one."""
    playlist_id = row["external_id"]
    sync = CollectionSync(row["id"], base.row_name(row), row["kind"], UNCHANGED)
    obj = listed_obj
    try:
        if obj is None:
            # added by url or gone from the account, ask for it directly
            obj = client.get(f"/playlists/{playlist_id}")
            if not models.has_content(obj):
                return _locked(repo, sync, 403)
        if row["snapshot_id"] and row["snapshot_id"] == obj.get("snapshot_id"):
            return sync
        tracks, skipped = fetch_playlist_tracks(client, playlist_id)
    except SpotifyApiError as exc:
        if exc.status in LOCKED_STATUSES:
            return _locked(repo, sync, exc.status)
        sync.outcome, sync.message = FAILED, exc.message
        return sync

    record = models.playlist_record(obj, origin=row["origin"], tracks=tracks)
    sync.result = ingest.ingest_collection(repo, record, collection_id=row["id"])
    sync.name = record.name
    sync.outcome = UPDATED
    sync.skipped_entries = skipped
    return sync


def _sync_liked(repo: Repo, client: SpotifyClient, row: sqlite3.Row) -> CollectionSync:
    sync = CollectionSync(row["id"], base.row_name(row), row["kind"], UNCHANGED)
    try:
        first = client.get("/me/tracks", {"limit": SAVED_TRACKS_LIMIT})
        fingerprint = liked_fingerprint(first)
        if row["snapshot_id"] == fingerprint:
            return sync
        entries = list(first.get("items") or [])
        next_url = first.get("next")
        while next_url:
            page = client.get(next_url)
            entries.extend(page.get("items") or [])
            next_url = page.get("next")
    except SpotifyApiError as exc:
        sync.outcome, sync.message = FAILED, exc.message
        return sync
    tracks, skipped = models.parse_entries(entries)
    record = CollectionRecord(
        kind=KIND_SPOTIFY_LIKED,
        origin=ORIGIN_ACCOUNT,
        name=base.row_name(row),
        external_id=row["external_id"],
        owner_id=row["owner_id"],
        snapshot_id=fingerprint,
        track_count=first.get("total"),
        tracks=tracks,
    )
    sync.result = ingest.ingest_collection(repo, record, collection_id=row["id"])
    sync.outcome = UPDATED
    sync.skipped_entries = skipped
    return sync


def refresh_account_list(
    repo: Repo,
    client: SpotifyClient,
    me: dict[str, Any],
    report: SyncReport,
    progress: Progress = noop_progress,
) -> dict[str, dict[str, Any]]:
    """saves metadata of every playlist on the account. content is read separately."""
    my_id = me["id"]
    progress(tr("Reading your Spotify playlists..."))
    listed: dict[str, dict[str, Any]] = {}
    for obj in client.items("/me/playlists", {"limit": PLAYLISTS_LIMIT}):
        if isinstance(obj, dict) and obj.get("id"):
            listed[obj["id"]] = obj
    report.playlists_listed = len(listed)

    for playlist_id, obj in listed.items():
        existing = repo.find_collection(KIND_SPOTIFY_PLAYLIST, playlist_id)
        owned = (obj.get("owner") or {}).get("id") == my_id or bool(obj.get("collaborative"))
        if owned:
            readable = True
        elif existing is not None:
            readable = bool(existing["is_readable"])
        else:
            # first sight of someone else's playlist: ask once instead of guessing
            readable = _probe_readable(client, playlist_id) is not False
        if existing is None:
            report.playlists_new += 1
        if not readable:
            report.locked.append(obj.get("name") or playlist_id)
        record = models.playlist_record(obj, origin=ORIGIN_ACCOUNT, is_readable=readable)
        ingest.ingest_collection(repo, record)

    if repo.find_collection(KIND_SPOTIFY_LIKED, my_id) is None:
        ingest.ingest_collection(
            repo,
            CollectionRecord(
                kind=KIND_SPOTIFY_LIKED,
                origin=ORIGIN_ACCOUNT,
                name=LIKED_NAME,
                external_id=my_id,
                owner_id=my_id,
            ),
        )
    return listed


def refresh_list(repo: Repo, client: SpotifyClient, progress: Progress | None = None) -> SyncReport:
    """only the list of playlists on the account, no content (D-130). new ones come unselected
    like every collection."""
    report = SyncReport()
    me = client.get("/me")
    refresh_account_list(repo, client, me, report, progress or noop_progress)
    return report


def sync_account(
    repo: Repo,
    client: SpotifyClient,
    *,
    collection_id: int | None = None,
    progress: Progress | None = None,
) -> SyncReport:
    """refreshes the playlist list, then the content of selected collections that changed.

    with collection_id only that collection gets its content, selected or not.
    """
    progress = progress or noop_progress
    report = SyncReport()
    me = client.get("/me")
    listed = refresh_account_list(repo, client, me, report, progress)

    if collection_id is not None:
        row = repo.get_collection(collection_id)
        targets = [row] if row is not None else []
    else:
        targets = [
            row
            for kind in (KIND_SPOTIFY_LIKED, KIND_SPOTIFY_PLAYLIST, KIND_SPOTIFY_ALBUM)
            for row in repo.collections_of_kind(kind)
            if row["selected"]
        ]

    for row in targets:
        progress(tr("Syncing {name}...").format(name=base.row_name(row)))
        if row["kind"] == KIND_SPOTIFY_PLAYLIST:
            sync = sync_playlist(repo, client, row, listed.get(row["external_id"]))
        elif row["kind"] == KIND_SPOTIFY_LIKED:
            if row["external_id"] != me["id"]:
                continue  # liked songs of an account that was logged in before
            sync = _sync_liked(repo, client, row)
        elif row["kind"] == KIND_SPOTIFY_ALBUM:
            # albums do not change, only an incomplete one is read again
            if row["snapshot_id"]:
                sync = CollectionSync(row["id"], base.row_name(row), row["kind"], UNCHANGED)
            else:
                sync = refresh_album(repo, client, row, progress)
        else:
            continue  # csv and singles have nothing to sync
        report.collections.append(sync)
    return report
