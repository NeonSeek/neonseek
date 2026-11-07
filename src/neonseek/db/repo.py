"""sqlite access. all sql lives here, the rest of core works with rows and plain values."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from neonseek.errors import DatabaseError
from neonseek.i18n import tr

TRACK_COLUMNS = (
    "spotify_id",
    "isrc",
    "title",
    "artists_json",
    "album",
    "album_artists_json",
    "release_date",
    "duration_ms",
    "track_number",
    "disc_number",
    "cover_url",
    "explicit",
    "match_key",
)

COLLECTION_COLUMNS = (
    "kind",
    "origin",
    "external_id",
    "source_ref",
    "name",
    "owner_id",
    "is_readable",
    "snapshot_id",
    "import_map_json",
    "image_url",
    "track_count",
    "selected",
    "genre",
    "comment",
    "last_synced_at",
)

DOWNLOAD_COLUMNS = (
    "status",
    "stage",
    "file_path",
    "format",
    "sample_rate",
    "bit_depth",
    "duration_ms",
    "length_verified",
    "source_user",
    "source_path",
    "attempts",
    "last_error",
    "excluded_from",
)

# downloads.status
PENDING = "pending"
SEARCHING = "searching"
DOWNLOADING = "downloading"
DONE = "done"
NOT_FOUND = "not_found"
FAILED = "failed"
# the user does not want it downloaded (D-126). excluded_from keeps the status before
EXCLUDED = "excluded"
# statuses a track can be excluded from: no file yet, nothing running
EXCLUDABLE = (PENDING, NOT_FOUND, FAILED)

# columns that describe the file of a downloaded track, cleared when the file goes away
FILE_COLUMNS = ("file_path", "format", "sample_rate", "bit_depth", "duration_ms",
                "length_verified", "source_user", "source_path")  # fmt: skip

# downloads.stage
STAGE_FLAC = "flac"
STAGE_FALLBACK = "fallback"


def _migrate_v1(conn: sqlite3.Connection) -> None:
    schema = resources.files("neonseek.db").joinpath("schema.sql").read_text(encoding="utf-8")
    # executescript commits on its own, so the version bump goes inside the same script
    conn.executescript(f"BEGIN;\n{schema}\nPRAGMA user_version = 1;\nCOMMIT;")


def _migrate_v2(conn: sqlite3.Connection) -> None:
    # tags set by the user per collection, and search times shared by every sockseek process
    conn.executescript(
        """
        BEGIN;
        ALTER TABLE collections ADD COLUMN genre TEXT;
        ALTER TABLE collections ADD COLUMN comment TEXT;
        CREATE TABLE searches (at REAL NOT NULL);
        CREATE INDEX searches_at ON searches (at);
        PRAGMA user_version = 2;
        COMMIT;
        """
    )


def _migrate_v3(conn: sqlite3.Connection) -> None:
    # excluded tracks remember their status, the library move keeps a journal (D-126, D-128)
    conn.executescript(
        """
        BEGIN;
        ALTER TABLE downloads ADD COLUMN excluded_from TEXT;
        CREATE TABLE file_moves (
          track_id INTEGER PRIMARY KEY REFERENCES tracks (id),
          from_path TEXT NOT NULL,
          to_path TEXT NOT NULL
        );
        PRAGMA user_version = 3;
        COMMIT;
        """
    )


def _migrate_v4(conn: sqlite3.Connection) -> None:
    # ids of a track on apple music, youtube, soundcloud or tidal. a sync finds the same track again
    # by them, also after the user corrected its artist or title (D-228)
    conn.executescript(
        """
        BEGIN;
        CREATE TABLE track_sources (
          source_id TEXT PRIMARY KEY,
          track_id INTEGER NOT NULL REFERENCES tracks (id)
        );
        CREATE INDEX track_sources_track ON track_sources (track_id);
        PRAGMA user_version = 4;
        COMMIT;
        """
    )


# index i migrates from version i to i + 1, only ever append
MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _migrate_v1,
    _migrate_v2,
    _migrate_v3,
    _migrate_v4,
]
SCHEMA_VERSION = len(MIGRATIONS)


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def backup_path(path: Path, version: int) -> Path:
    """the copy of the database as it was in this schema version, next to it."""
    return path.with_name(f"{path.stem}-schema-{version}-backup{path.suffix}")


def backup(conn: sqlite3.Connection, path: Path, version: int) -> Path | None:
    """one copy per schema version, taken before that version is migrated. a copy that is
    already there is the older one and stays. a failed copy stops the migration."""
    target = backup_path(path, version)
    if target.exists():
        return None
    partial = target.with_name(target.name + ".tmp")
    try:
        partial.unlink(missing_ok=True)
        copy = sqlite3.connect(partial)
        try:
            # the backup api copies a consistent database, also what is still in the wal file
            conn.backup(copy)
        finally:
            copy.close()
        os.replace(partial, target)
    except (OSError, sqlite3.Error) as exc:
        with contextlib.suppress(OSError):
            partial.unlink()
        raise DatabaseError(
            tr(
                "Could not save a copy of the database before updating it ({error}). Nothing "
                "was changed. Free some space on the drive of {folder} and start NeonSeek again."
            ).format(error=exc, folder=path.parent)
        ) from exc
    return target


def migrate(conn: sqlite3.Connection, path: Path | None = None) -> None:
    """every step after the first gets a copy of the database first, so an update that goes
    wrong never takes the user's collections with it."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise DatabaseError(
            tr(
                "The database was created by a newer version of NeonSeek. "
                "Install the newest version to open it."
            )
        )
    for step in range(version, SCHEMA_VERSION):
        # a new database has nothing to keep, only one that was opened with data gets copies
        if version > 0 and path is not None:
            backup(conn, path, step)
        MIGRATIONS[step](conn)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        migrate(conn, path)
    except DatabaseError:
        # a newer database or a copy that could not be saved: the file stays as it was
        conn.close()
        raise
    except sqlite3.DatabaseError as exc:
        raise DatabaseError(
            tr(
                "Could not open the database {path} ({error}). "
                "Close other NeonSeek windows and try again."
            ).format(path=path, error=exc)
        ) from exc
    return conn


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


@dataclass(frozen=True)
class CollectionSummary:
    id: int
    kind: str
    origin: str
    name: str
    is_readable: bool
    selected: bool
    track_count: int | None
    linked: int
    done: int
    last_synced_at: str | None
    source_ref: str | None
    genre: str | None = None
    comment: str | None = None
    image_url: str | None = None
    excluded: int = 0  # tracks the user excluded from downloads


class Repo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        with self.conn:
            yield

    # collections

    def get_collection(self, collection_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM collections WHERE id = ?", (collection_id,)
        ).fetchone()

    def find_collection(self, kind: str, external_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM collections WHERE kind = ? AND external_id = ?", (kind, external_id)
        ).fetchone()

    def find_collection_by_kind(self, kind: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM collections WHERE kind = ? ORDER BY id LIMIT 1", (kind,)
        ).fetchone()

    def find_collection_by_path(self, kind: str, path: str) -> sqlite3.Row | None:
        # windows paths are case insensitive, so compare outside sql
        rows = self.conn.execute(
            "SELECT * FROM collections WHERE kind = ? AND source_ref IS NOT NULL ORDER BY id",
            (kind,),
        ).fetchall()
        return next((row for row in rows if same_path(row["source_ref"], path)), None)

    def collections_of_kind(self, kind: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM collections WHERE kind = ? ORDER BY id", (kind,)
        ).fetchall()

    def insert_collection(self, values: dict[str, Any]) -> int:
        _check_columns(values, COLLECTION_COLUMNS)
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        cur = self.conn.execute(
            f"INSERT INTO collections ({cols}) VALUES ({marks})", tuple(values.values())
        )
        return int(cur.lastrowid)

    def update_collection(self, collection_id: int, values: dict[str, Any]) -> None:
        if not values:
            return
        _check_columns(values, COLLECTION_COLUMNS)
        sets = ", ".join(f"{col} = ?" for col in values)
        self.conn.execute(
            f"UPDATE collections SET {sets} WHERE id = ?", (*values.values(), collection_id)
        )

    def delete_collection(self, collection_id: int) -> None:
        # links go by cascade, tracks and downloads stay (PLAN.md section 5)
        self.conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))

    def entry_counts(self) -> dict[int, int]:
        """places in each list, a track that is there twice counted twice."""
        rows = self.conn.execute(
            "SELECT collection_id, COUNT(*) FROM collection_tracks GROUP BY collection_id"
        )
        return {collection_id: count for collection_id, count in rows}

    def list_collections(self) -> list[CollectionSummary]:
        rows = self.conn.execute(
            """
            SELECT c.*,
                   (SELECT COUNT(DISTINCT ct.track_id) FROM collection_tracks ct
                     WHERE ct.collection_id = c.id) AS linked,
                   (SELECT COUNT(DISTINCT ct.track_id) FROM collection_tracks ct
                     JOIN downloads d ON d.track_id = ct.track_id
                     WHERE ct.collection_id = c.id AND d.status = 'done') AS done,
                   (SELECT COUNT(DISTINCT ct.track_id) FROM collection_tracks ct
                     JOIN downloads d ON d.track_id = ct.track_id
                     WHERE ct.collection_id = c.id AND d.status = 'excluded') AS excluded
            FROM collections c
            ORDER BY c.kind, c.name COLLATE NOCASE, c.id
            """
        ).fetchall()
        return [
            CollectionSummary(
                id=row["id"],
                kind=row["kind"],
                origin=row["origin"],
                name=row["name"] or "",
                is_readable=bool(row["is_readable"]),
                selected=bool(row["selected"]),
                track_count=row["track_count"],
                linked=row["linked"],
                done=row["done"],
                excluded=row["excluded"],
                last_synced_at=row["last_synced_at"],
                source_ref=row["source_ref"],
                genre=row["genre"],
                comment=row["comment"],
                image_url=row["image_url"],
            )
            for row in rows
        ]

    # collection content

    def collection_entries(self, collection_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM collection_tracks WHERE collection_id = ? ORDER BY position",
            (collection_id,),
        ).fetchall()

    def replace_collection_entries(
        self, collection_id: int, entries: Iterable[tuple[int, str | None]]
    ) -> None:
        self.conn.execute("DELETE FROM collection_tracks WHERE collection_id = ?", (collection_id,))
        self.conn.executemany(
            "INSERT INTO collection_tracks (collection_id, track_id, position, added_at) "
            "VALUES (?, ?, ?, ?)",
            [
                (collection_id, track_id, position, added_at)
                for position, (track_id, added_at) in enumerate(entries)
            ],
        )

    def append_collection_entry(
        self, collection_id: int, track_id: int, added_at: str | None
    ) -> None:
        self.conn.execute(
            "INSERT INTO collection_tracks (collection_id, track_id, position, added_at) "
            "VALUES (?, ?, (SELECT COALESCE(MAX(position) + 1, 0) FROM collection_tracks "
            "WHERE collection_id = ?), ?)",
            (collection_id, track_id, collection_id, added_at),
        )

    # tracks

    def get_track(self, track_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    def find_track_by_spotify_id(self, spotify_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM tracks WHERE spotify_id = ?", (spotify_id,)
        ).fetchone()

    def find_tracks_by_isrc(self, isrc: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM tracks WHERE isrc = ? ORDER BY id", (isrc,)
        ).fetchall()

    def find_tracks_by_match_key(self, match_key: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM tracks WHERE match_key = ? ORDER BY id", (match_key,)
        ).fetchall()

    def find_track_by_source_id(self, source_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT t.* FROM tracks t JOIN track_sources s ON s.track_id = t.id "
            "WHERE s.source_id = ?",
            (source_id,),
        ).fetchone()

    def add_track_source(self, source_id: str, track_id: int) -> None:
        # the first track an id was seen with keeps it
        self.conn.execute(
            "INSERT OR IGNORE INTO track_sources (source_id, track_id) VALUES (?, ?)",
            (source_id, track_id),
        )

    def insert_track(self, values: dict[str, Any]) -> int:
        _check_columns(values, TRACK_COLUMNS)
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        cur = self.conn.execute(
            f"INSERT INTO tracks ({cols}) VALUES ({marks})", tuple(values.values())
        )
        track_id = int(cur.lastrowid)
        # every track gets its download row right away, phase 2 picks pending ones
        self.conn.execute(
            "INSERT INTO downloads (track_id, status, stage, attempts, updated_at) "
            "VALUES (?, 'pending', 'flac', 0, ?)",
            (track_id, now_iso()),
        )
        return track_id

    def retry_with_new_names(self, track_id: int) -> None:
        """a track not found or failed gets a fresh start after the user corrected it."""
        self.conn.execute(
            "UPDATE downloads SET status = ?, stage = ?, attempts = 0, last_error = NULL, "
            "updated_at = ? WHERE track_id = ? AND status IN (?, ?)",
            (PENDING, STAGE_FLAC, now_iso(), track_id, NOT_FOUND, FAILED),
        )

    def update_track(self, track_id: int, values: dict[str, Any]) -> None:
        if not values:
            return
        _check_columns(values, TRACK_COLUMNS)
        sets = ", ".join(f"{col} = ?" for col in values)
        self.conn.execute(f"UPDATE tracks SET {sets} WHERE id = ?", (*values.values(), track_id))

    # downloads

    def get_download(self, track_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM downloads WHERE track_id = ?", (track_id,)
        ).fetchone()

    def update_download(self, track_id: int, values: dict[str, Any]) -> None:
        _check_columns(values, DOWNLOAD_COLUMNS)
        values = {**values, "updated_at": now_iso()}
        sets = ", ".join(f"{col} = ?" for col in values)
        with self.conn:
            self.conn.execute(
                f"UPDATE downloads SET {sets} WHERE track_id = ?", (*values.values(), track_id)
            )

    def add_failure(self, track_id: int, reason: str) -> None:
        """failed in the current stage, one more attempt used."""
        with self.conn:
            self.conn.execute(
                "UPDATE downloads SET status = 'failed', attempts = attempts + 1, last_error = ?, "
                "updated_at = ? WHERE track_id = ?",
                (reason, now_iso(), track_id),
            )

    def reset_in_progress(self, track_ids: Iterable[int] | None = None) -> int:
        """searching and downloading go back to pending, stage and attempts stay."""
        sql = (
            "UPDATE downloads SET status = 'pending', updated_at = ? "
            "WHERE status IN ('searching', 'downloading')"
        )
        with self.conn:
            if track_ids is None:
                return self.conn.execute(sql, (now_iso(),)).rowcount
            return sum(
                self.conn.execute(
                    f"{sql} AND track_id IN ({_marks(chunk)})", (now_iso(), *chunk)
                ).rowcount
                for chunk in _chunks(list(track_ids))
            )

    def revert_fallback(self) -> int:
        """on_missing_flac = not_found: tracks not downloaded in the fallback stage go back.
        excluded tracks keep their state until they are included again."""
        with self.conn:
            return self.conn.execute(
                "UPDATE downloads SET stage = 'flac', status = 'not_found', attempts = 0, "
                "updated_at = ? WHERE stage = 'fallback' AND status NOT IN ('done', 'excluded')",
                (now_iso(),),
            ).rowcount

    def move_to_fallback(self, track_ids: Iterable[int]) -> None:
        with self.conn:
            for chunk in _chunks(list(track_ids)):
                self.conn.execute(
                    "UPDATE downloads SET stage = 'fallback', status = 'pending', attempts = 0, "
                    f"updated_at = ? WHERE track_id IN ({_marks(chunk)})",
                    (now_iso(), *chunk),
                )

    def retry(self, track_ids: Iterable[int]) -> list[int]:
        """not_found and failed start again from flac. returns the ids that changed."""
        changed = []
        with self.conn:
            for track_id in dict.fromkeys(track_ids):
                cur = self.conn.execute(
                    "UPDATE downloads SET stage = 'flac', status = 'pending', attempts = 0, "
                    "last_error = NULL, updated_at = ? "
                    "WHERE track_id = ? AND status IN ('not_found', 'failed')",
                    (now_iso(), track_id),
                )
                if cur.rowcount:
                    changed.append(track_id)
        return changed

    def exclude(self, track_ids: Iterable[int]) -> list[int]:
        """tracks without a file stop being downloaded, their status is kept for later."""
        return self._update_each(
            track_ids,
            "UPDATE downloads SET excluded_from = status, status = 'excluded', updated_at = ? "
            "WHERE track_id = ? AND status IN ('pending', 'not_found', 'failed')",
        )

    def include(self, track_ids: Iterable[int]) -> list[int]:
        """excluded tracks go back to the status they had, nothing is searched again."""
        return self._update_each(
            track_ids,
            "UPDATE downloads SET status = COALESCE(excluded_from, 'pending'), "
            "excluded_from = NULL, updated_at = ? WHERE track_id = ? AND status = 'excluded'",
        )

    def prepare_for_download(self, track_ids: Iterable[int]) -> None:
        """chosen tracks before their own download: excluded ones come back, not found and
        failed ones start again from flac like a retry from the report (D-125)."""
        ids = list(dict.fromkeys(track_ids))
        self.include(ids)
        self.retry(ids)

    def file_gone(self, track_id: int, exclude: bool) -> None:
        """the file of a track went to the recycle bin. the track starts again from flac, as
        excluded when the user deleted it on purpose, else as not downloaded."""
        cleared = ", ".join(f"{col} = NULL" for col in FILE_COLUMNS)
        status = EXCLUDED if exclude else PENDING
        excluded_from = PENDING if exclude else None
        with self.conn:
            self.conn.execute(
                f"UPDATE downloads SET {cleared}, status = ?, excluded_from = ?, stage = 'flac', "
                "attempts = 0, last_error = NULL, updated_at = ? WHERE track_id = ?",
                (status, excluded_from, now_iso(), track_id),
            )

    def _update_each(self, track_ids: Iterable[int], sql: str) -> list[int]:
        changed = []
        with self.conn:
            for track_id in dict.fromkeys(track_ids):
                if self.conn.execute(sql, (now_iso(), track_id)).rowcount:
                    changed.append(track_id)
        return changed

    def collections_of_tracks(self, track_ids: Iterable[int]) -> list[int]:
        """every collection that holds one of these tracks, in the order they were added."""
        found: set[int] = set()
        for chunk in _chunks(list(dict.fromkeys(track_ids))):
            rows = self.conn.execute(
                "SELECT DISTINCT collection_id FROM collection_tracks "
                f"WHERE track_id IN ({_marks(chunk)})",
                chunk,
            ).fetchall()
            found.update(row["collection_id"] for row in rows)
        return sorted(found)

    # library move journal (PLAN.md section 8)

    def start_move(self, track_id: int, from_path: str, to_path: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO file_moves (track_id, from_path, to_path) VALUES (?, ?, ?)",
                (track_id, from_path, to_path),
            )

    def finish_move(self, track_id: int, file_path: str) -> None:
        """the file is where file_path says, the journal row goes in the same transaction."""
        with self.conn:
            self.conn.execute(
                "UPDATE downloads SET file_path = ?, updated_at = ? WHERE track_id = ?",
                (file_path, now_iso(), track_id),
            )
            self.conn.execute("DELETE FROM file_moves WHERE track_id = ?", (track_id,))

    def pending_moves(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM file_moves ORDER BY track_id").fetchall()

    def all_track_ids(self) -> list[int]:
        return [row["id"] for row in self.conn.execute("SELECT id FROM tracks ORDER BY id")]

    def status_counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) AS n FROM downloads GROUP BY status")
        return {row["status"]: row["n"] for row in rows}

    def collection_track_ids(self, collection_id: int) -> list[int]:
        """tracks in source order, a track listed twice comes once."""
        rows = self.conn.execute(
            "SELECT track_id FROM collection_tracks WHERE collection_id = ? ORDER BY position",
            (collection_id,),
        ).fetchall()
        return list(dict.fromkeys(row["track_id"] for row in rows))

    def track_states(self, track_ids: Iterable[int]) -> dict[int, sqlite3.Row]:
        """track and download columns together. the file length is file_duration_ms."""
        found: dict[int, sqlite3.Row] = {}
        for chunk in _chunks(list(dict.fromkeys(track_ids))):
            rows = self.conn.execute(
                f"""
                SELECT t.*, d.status, d.stage, d.attempts, d.file_path, d.format,
                       d.sample_rate, d.bit_depth, d.duration_ms AS file_duration_ms,
                       d.length_verified, d.last_error, d.source_user, d.source_path,
                       d.excluded_from, d.updated_at
                FROM tracks t JOIN downloads d ON d.track_id = t.id
                WHERE t.id IN ({_marks(chunk)})
                """,
                chunk,
            ).fetchall()
            found.update((row["id"], row) for row in rows)
        return found

    def collections_of_track(self, track_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM collections WHERE id IN "
            "(SELECT collection_id FROM collection_tracks WHERE track_id = ?) ORDER BY id",
            (track_id,),
        ).fetchall()

    def done_entries(self, collection_id: int) -> list[sqlite3.Row]:
        """downloaded tracks of a collection in source order, every position."""
        return self.conn.execute(
            """
            SELECT ct.position, t.*, d.file_path, d.duration_ms AS file_duration_ms
            FROM collection_tracks ct
            JOIN tracks t ON t.id = ct.track_id
            JOIN downloads d ON d.track_id = ct.track_id
            WHERE ct.collection_id = ? AND d.status = 'done' AND d.file_path IS NOT NULL
            ORDER BY ct.position
            """,
            (collection_id,),
        ).fetchall()

    def file_paths(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT track_id, file_path FROM downloads WHERE file_path IS NOT NULL"
        ).fetchall()

    def downloaded_files(self) -> list[sqlite3.Row]:
        """every track with a file, the ones the library move and the delete actions know."""
        return self.conn.execute(
            "SELECT track_id, file_path FROM downloads "
            "WHERE status = 'done' AND file_path IS NOT NULL ORDER BY track_id"
        ).fetchall()

    def problem_tracks(self) -> list[sqlite3.Row]:
        """not_found and failed tracks for the report."""
        return self.conn.execute(
            """
            SELECT t.*, d.status, d.stage, d.attempts, d.last_error, d.updated_at
            FROM tracks t JOIN downloads d ON d.track_id = t.id
            WHERE d.status IN ('not_found', 'failed')
            ORDER BY t.id
            """
        ).fetchall()

    # searches made by sockseek, shared by every run so a new process cannot exceed the limit

    def add_search(self, at: float) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO searches (at) VALUES (?)", (at,))

    def searches_since(self, since: float) -> list[float]:
        with self.conn:
            # an hour is far more than any search window
            self.conn.execute("DELETE FROM searches WHERE at < ?", (since - 3600,))
        rows = self.conn.execute(
            "SELECT at FROM searches WHERE at >= ? ORDER BY at", (since,)
        ).fetchall()
        return [row["at"] for row in rows]

    # runs

    def start_run(self) -> int:
        with self.conn:
            cur = self.conn.execute("INSERT INTO runs (started_at) VALUES (?)", (now_iso(),))
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, stats: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET finished_at = ?, stats_json = ? WHERE id = ?",
                (now_iso(), json.dumps(stats, ensure_ascii=False), run_id),
            )

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()

    def last_finished_run(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def count(self, table: str) -> int:
        if table not in {"collections", "tracks", "collection_tracks", "downloads", "runs"}:
            raise ValueError(table)
        return int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _marks(values: list) -> str:
    return ", ".join("?" for _ in values)


def _chunks(values: list, size: int = 500) -> Iterator[list]:
    # sqlite limits the number of parameters in one statement
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _check_columns(values: dict[str, Any], allowed: tuple[str, ...]) -> None:
    unknown = set(values) - set(allowed)
    if unknown:
        raise ValueError(f"unknown columns: {sorted(unknown)}")
