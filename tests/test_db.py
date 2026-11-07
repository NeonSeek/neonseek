from __future__ import annotations

import sqlite3

import pytest

from neonseek.db import repo as db
from neonseek.errors import DatabaseError


def test_new_database_gets_current_version(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"collections", "tracks", "collection_tracks", "downloads", "runs"} <= tables
    conn.close()


def test_reopening_keeps_data(tmp_path):
    path = tmp_path / "x.db"
    conn = db.connect(path)
    with conn:
        conn.execute("INSERT INTO runs (started_at) VALUES ('x')")
    conn.close()
    conn = db.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    conn.close()


def test_newer_database_is_refused(tmp_path):
    path = tmp_path / "x.db"
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    raw.close()
    with pytest.raises(DatabaseError, match="newer version"):
        db.connect(path)


def test_only_one_singles_collection(repo):
    with repo.transaction():
        repo.insert_collection({"kind": "singles", "origin": "builtin", "name": "a"})
    with pytest.raises(sqlite3.IntegrityError), repo.transaction():
        repo.insert_collection({"kind": "singles", "origin": "builtin", "name": "b"})


def test_unknown_columns_are_rejected(repo):
    with pytest.raises(ValueError):
        repo.insert_track({"title": "x", "bpm": 120})


def test_same_path_ignores_case_and_separators():
    assert db.same_path(r"C:\Music\List.csv", "c:/music/list.csv")
    assert not db.same_path(r"C:\Music\List.csv", r"C:\Music\Other.csv")


# a copy of the database before every migration (owner request, D-155)


def _old_database(path, version: int) -> None:
    raw = sqlite3.connect(path)
    for step in range(version):
        db.MIGRATIONS[step](raw)
    raw.execute("INSERT INTO collections (kind, origin, name) VALUES ('csv', 'file', 'mine')")
    raw.commit()
    raw.close()


def _copy(path):
    raw = sqlite3.connect(path)
    try:
        version = raw.execute("PRAGMA user_version").fetchone()[0]
        names = [r[0] for r in raw.execute("SELECT name FROM collections")]
    finally:
        raw.close()
    return version, names


def test_every_migration_step_gets_a_copy_first(tmp_path):
    path = tmp_path / "neonseek.db"
    _old_database(path, 1)
    db.connect(path).close()
    one, two, three = (db.backup_path(path, v) for v in (1, 2, 3))
    assert one.parent == tmp_path and one.name == "neonseek-schema-1-backup.db"
    # each copy is the database as it was in that version, with the user's data
    assert _copy(one) == (1, ["mine"]) and _copy(two) == (2, ["mine"])
    assert _copy(three) == (3, ["mine"])
    assert not db.backup_path(path, 4).exists()  # the current version needs no copy
    assert not list(tmp_path.glob("*.tmp"))


def test_a_copy_that_exists_stays_and_a_new_database_gets_none(tmp_path):
    path = tmp_path / "neonseek.db"
    _old_database(path, 2)
    older = db.backup_path(path, 2)
    _old_database(older, 2)
    raw = sqlite3.connect(older)
    raw.execute("UPDATE collections SET name = 'from before'")
    raw.commit()
    raw.close()
    db.connect(path).close()
    assert _copy(older) == (2, ["from before"])  # one copy per version, never replaced
    fresh = tmp_path / "new" / "neonseek.db"
    db.connect(fresh).close()
    assert [p.name for p in fresh.parent.iterdir() if "backup" in p.name] == []


def test_no_migration_without_its_copy(tmp_path, monkeypatch):
    path = tmp_path / "neonseek.db"
    _old_database(path, 2)
    # the copy cannot be saved, like on a full drive
    monkeypatch.setattr(db.os, "replace", _fail_replace)
    with pytest.raises(DatabaseError, match="copy of the database"):
        db.connect(path)
    assert _copy(path)[0] == 2  # the database stays as it was


def _fail_replace(source, target):
    raise PermissionError(13, "access denied")
