"""the only writer of collections and tracks. every source goes through here (PLAN.md section 5)."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from typing import Any

from neonseek.db.repo import Repo, now_iso
from neonseek.sources import base
from neonseek.sources.base import (
    KIND_CSV,
    KIND_SINGLES,
    ORIGIN_ACCOUNT,
    ORIGIN_BUILTIN,
    CollectionRecord,
    TrackRecord,
)

# two lengths further apart than this are different recordings
LENGTH_TOLERANCE_MS = 3000

# the database keeps the english name, base.display_name translates it (D-180)
SINGLES_NAME = base.SINGLES_NAME

_FEAT = re.compile(r"(?<!\w)(?:featuring|feat|ft)(?:\.|(?=\s))", re.IGNORECASE)
_OPENERS = "(["
_CLOSERS = ")]"


def _strip_feat(text: str) -> str:
    # drops "feat. x" up to the end of its bracket, or up to " - ", a bracket or the end
    while match := _FEAT.search(text):
        start = match.start()
        opener = max(text.rfind(ch, 0, start) for ch in _OPENERS)
        inside = opener >= 0 and not any(ch in text[opener:start] for ch in _CLOSERS)
        if inside:
            closes = [i for i in (text.find(ch, start) for ch in _CLOSERS) if i >= 0]
            close = min(closes) if closes else len(text)
            if text[opener + 1 : start].strip():
                text = text[:start] + text[close:]
            else:
                text = text[:opener] + text[close + 1 :]
        else:
            stops = [i for i in (text.find(ch, start) for ch in _OPENERS) if i >= 0]
            dash = text.find(" - ", start)
            if dash >= 0:
                stops.append(dash)
            text = text[:start] + text[min(stops) if stops else len(text) :]
    return text


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", _strip_feat(text)).casefold()
    text = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    return " ".join(text.split())


def match_key(first_artist: str | None, title: str) -> str:
    """normalized "first artist|title". version tags like extended mix stay in."""
    return f"{_normalize(first_artist or '')}|{_normalize(title)}"


def normalize_isrc(value: str | None) -> str | None:
    if not value:
        return None
    isrc = value.strip().upper().replace("-", "").replace(" ", "")
    return isrc if isrc.isalnum() else None


def _json_list(values: tuple[str, ...]) -> str | None:
    return json.dumps(list(values), ensure_ascii=False) if values else None


def track_values(rec: TrackRecord) -> dict[str, Any]:
    title = rec.title.strip()
    artists = tuple(a.strip() for a in rec.artists if a and a.strip())
    return {
        "spotify_id": rec.spotify_id or None,
        "isrc": normalize_isrc(rec.isrc),
        "title": title,
        "artists_json": _json_list(artists),
        "album": rec.album or None,
        "album_artists_json": _json_list(rec.album_artists),
        "release_date": rec.release_date or None,
        "duration_ms": rec.duration_ms if rec.duration_ms and rec.duration_ms > 0 else None,
        "track_number": rec.track_number,
        "disc_number": rec.disc_number,
        "cover_url": rec.cover_url or None,
        "explicit": None if rec.explicit is None else int(rec.explicit),
        "match_key": match_key(artists[0] if artists else None, title),
    }


def lengths_match(a: int | None, b: int | None) -> bool:
    return a is None or b is None or abs(a - b) <= LENGTH_TOLERANCE_MS


def _has_ids(values: sqlite3.Row | dict[str, Any]) -> bool:
    return bool(values["spotify_id"] or values["isrc"])


def find_existing_track(repo: Repo, values: dict[str, Any]) -> sqlite3.Row | None:
    # 1. same spotify id
    if values["spotify_id"]:
        row = repo.find_track_by_spotify_id(values["spotify_id"])
        if row is not None:
            return row
    # 2. same isrc and compatible length
    if values["isrc"]:
        for row in repo.find_tracks_by_isrc(values["isrc"]):
            if lengths_match(row["duration_ms"], values["duration_ms"]):
                return row
    # 3. same match key and compatible length, only if one side has no ids at all
    for row in repo.find_tracks_by_match_key(values["match_key"]):
        if _has_ids(values) and _has_ids(row):
            continue
        if lengths_match(row["duration_ms"], values["duration_ms"]):
            return row
    return None


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == "[]"


def _fill_missing(row: sqlite3.Row, values: dict[str, Any]) -> dict[str, Any]:
    updates = {
        col: new
        for col, new in values.items()
        if col != "match_key" and new is not None and _empty(row[col])
    }
    if "artists_json" in updates:
        artists = json.loads(updates["artists_json"])
        updates["match_key"] = match_key(artists[0] if artists else None, row["title"])
    return updates


def resolve_track(repo: Repo, rec: TrackRecord) -> tuple[int, bool]:
    """returns (track id, created). an existing track only gets its empty fields filled."""
    values = track_values(rec)
    # a track seen before on the same service is that track, whatever the user renamed it to
    row = repo.find_track_by_source_id(rec.source_id) if rec.source_id else None
    if row is None:
        row = find_existing_track(repo, values)
    if row is None:
        track_id, created = repo.insert_track(values), True
    else:
        repo.update_track(row["id"], _fill_missing(row, values))
        track_id, created = int(row["id"]), False
    if rec.source_id:
        repo.add_track_source(rec.source_id, track_id)
    return track_id, created


@dataclass
class IngestResult:
    collection_id: int
    created: bool
    content_updated: bool = False
    tracks_total: int = 0  # entries in the collection after ingest
    tracks_new: int = 0  # tracks that did not exist in the library before
    tracks_existing: int = 0  # tracks matched to existing rows
    links_removed: int = 0  # tracks no longer in this collection (files and downloads stay)


def _find_collection(repo: Repo, record: CollectionRecord) -> sqlite3.Row | None:
    if record.external_id:
        return repo.find_collection(record.kind, record.external_id)
    if record.kind == KIND_SINGLES:
        return repo.find_collection_by_kind(KIND_SINGLES)
    if record.kind == KIND_CSV and record.source_ref:
        return repo.find_collection_by_path(KIND_CSV, record.source_ref)
    return None


def _collection_values(record: CollectionRecord) -> dict[str, Any]:
    return {
        "kind": record.kind,
        "origin": record.origin,
        "external_id": record.external_id,
        "source_ref": record.source_ref,
        "name": record.name,
        "owner_id": record.owner_id,
        "is_readable": int(record.is_readable),
        "import_map_json": (
            json.dumps(record.import_map, ensure_ascii=False, sort_keys=True)
            if record.import_map is not None
            else None
        ),
        "image_url": record.image_url,
        "track_count": record.track_count,
    }


def ingest_collection(
    repo: Repo,
    record: CollectionRecord,
    *,
    collection_id: int | None = None,
) -> IngestResult:
    """creates or updates one collection and, when the record has tracks, replaces its content.

    a new collection always starts unselected and an update never changes the selection,
    only the user picks what gets downloaded (PLAN.md section 8).
    """
    with repo.transaction():
        existing = (
            repo.get_collection(collection_id)
            if collection_id is not None
            else _find_collection(repo, record)
        )
        values = _collection_values(record)
        if existing is None:
            cid = repo.insert_collection({k: v for k, v in values.items() if v is not None})
            result = IngestResult(collection_id=cid, created=True)
        else:
            cid = int(existing["id"])
            # a playlist seen on the account stays an account playlist when added by url
            if existing["origin"] == ORIGIN_ACCOUNT:
                values["origin"] = ORIGIN_ACCOUNT
            repo.update_collection(cid, {k: v for k, v in values.items() if v is not None})
            result = IngestResult(collection_id=cid, created=False)

        if record.tracks is None:
            return result

        before = {row["track_id"] for row in repo.collection_entries(cid)}
        entries: list[tuple[int, str | None]] = []
        new_ids: set[int] = set()
        for rec in record.tracks:
            track_id, created = resolve_track(repo, rec)
            if created:
                new_ids.add(track_id)
            entries.append((track_id, rec.added_at))
        repo.replace_collection_entries(cid, entries)
        repo.update_collection(
            cid, {"snapshot_id": record.snapshot_id, "last_synced_at": now_iso()}
        )

        after = {track_id for track_id, _ in entries}
        result.content_updated = True
        result.tracks_total = len(entries)
        result.tracks_new = len(new_ids)
        result.tracks_existing = len(after - new_ids)
        result.links_removed = len(before - after)
        return result


@dataclass
class SingleResult:
    collection_id: int
    track_id: int
    track_created: bool
    already_listed: bool


def add_single(repo: Repo, rec: TrackRecord) -> SingleResult:
    """appends a track to the builtin singles collection, without repeats."""
    with repo.transaction():
        row = repo.find_collection_by_kind(KIND_SINGLES)
        if row is None:
            cid = repo.insert_collection(
                {
                    "kind": KIND_SINGLES,
                    "origin": ORIGIN_BUILTIN,
                    "name": SINGLES_NAME,
                }
            )
        else:
            cid = int(row["id"])
        track_id, created = resolve_track(repo, rec)
        listed = any(e["track_id"] == track_id for e in repo.collection_entries(cid))
        if not listed:
            repo.append_collection_entry(cid, track_id, rec.added_at or now_iso())
        count = len(repo.collection_entries(cid))
        repo.update_collection(cid, {"track_count": count, "last_synced_at": now_iso()})
        return SingleResult(cid, track_id, created, listed)
