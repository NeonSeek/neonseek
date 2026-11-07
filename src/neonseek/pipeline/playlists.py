"""one .m3u8 per collection: utf-8, relative paths, source order, downloaded tracks only."""

from __future__ import annotations

import json
import os
from pathlib import Path

from neonseek.db.repo import Repo
from neonseek.pipeline.postprocess.place import safe_name

PLAYLISTS_DIR = "playlists"


def playlist_path(repo: Repo, collection_id: int, library_dir: Path) -> Path:
    row = repo.get_collection(collection_id)
    name = safe_name(row["name"] or f"collection {collection_id}", 120)
    # two collections with the same name get their id, so neither overwrites the other
    twins = [
        c.id
        for c in repo.list_collections()
        if c.id != collection_id and safe_name(c.name or "", 120).casefold() == name.casefold()
    ]
    if twins:
        name = f"{name} [{collection_id}]"
    return library_dir / PLAYLISTS_DIR / f"{name}.m3u8"


def _entry_path(file_path: str, folder: Path) -> str:
    try:
        return os.path.relpath(file_path, folder)
    except ValueError:
        # another drive, a relative path is impossible
        return file_path


def write_playlist(repo: Repo, collection_id: int, library_dir: Path) -> Path:
    path = playlist_path(repo, collection_id, library_dir)
    lines = ["#EXTM3U"]
    for row in repo.done_entries(collection_id):
        artists = json.loads(row["artists_json"]) if row["artists_json"] else []
        label = f"{', '.join(artists)} - {row['title']}" if artists else row["title"]
        length = row["file_duration_ms"] or row["duration_ms"]
        seconds = round(length / 1000) if length else -1
        lines.append(f"#EXTINF:{seconds},{' '.join(label.split())}")
        lines.append(_entry_path(row["file_path"], path.parent))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    # newline="" keeps the crlf as written, windows would double the cr otherwise
    tmp.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")
    os.replace(tmp, path)
    return path
