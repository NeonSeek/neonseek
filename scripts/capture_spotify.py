"""phase 1: capture real spotify web api responses for docs/spotify-notes.md and fixtures.

uses the login saved by `neonseek spotify login` (refresh token in credential manager) and
only calls read endpoints. raw output holds personal data: write it outside the repo and
anonymize with scripts/anonymize_spotify.py before anything goes into tests/fixtures.

usage:
  uv run python scripts/capture_spotify.py --data-dir <dir> --out <raw dir> \
      [--foreign <playlist url or id> ...]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from neonseek import actions
from neonseek.spotify.client import API_URL
from neonseek.spotify.urls import parse_spotify_ref


class Recorder:
    def __init__(self, ctx: actions.AppContext, out: Path) -> None:
        self.ctx = ctx
        self.out = out
        self.index: list[dict[str, Any]] = []
        self.auth = ctx.spotify().auth

    def raw(self, name: str, path: str, params: dict[str, Any] | None = None) -> Any:
        # plain request without retries, the status code is part of what we record
        url = path if path.startswith("http") else f"{API_URL}{path}"
        headers = {"Authorization": f"Bearer {self.auth.access_token()}"}
        response = self.ctx.http.get(url, params=params, headers=headers)
        try:
            body = response.json()
        except ValueError:
            body = response.text
        record = {
            "name": name,
            "request": {"path": path, "params": params or {}},
            "status": response.status_code,
            "headers": {k: v for k, v in response.headers.items() if k.lower() in ("retry-after",)},
            "body": body,
        }
        (self.out / f"{name}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.index.append(
            {"name": name, "path": path, "params": params, "status": record["status"]}
        )
        print(f"{response.status_code}  {name}")
        return body if response.status_code == 200 else None

    def all_pages(self, name: str, path: str, params: dict[str, Any]) -> list[Any]:
        pages = []
        body = self.raw(f"{name}.p0", path, params)
        while body is not None:
            pages.append(body)
            if not body.get("next") or len(pages) >= 40:
                break
            body = self.raw(f"{name}.p{len(pages)}", body["next"])
        return pages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--foreign", nargs="*", default=[])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    ctx = actions.AppContext(args.data_dir)
    rec = Recorder(ctx, args.out)

    me = rec.raw("me", "/me")
    my_id = me["id"]

    playlists_pages = rec.all_pages("me_playlists", "/me/playlists", {"limit": 50})
    playlists = [p for page in playlists_pages for p in page.get("items", []) if p]
    rec.raw("me_playlists.limit2", "/me/playlists", {"limit": 2})

    # limit probing, one request each
    rec.raw("limit.me_playlists.51", "/me/playlists", {"limit": 51})
    rec.raw("limit.me_tracks.50", "/me/tracks", {"limit": 50})
    rec.raw("limit.me_tracks.51", "/me/tracks", {"limit": 51})

    own = [p for p in playlists if (p.get("owner") or {}).get("id") == my_id]
    collab = [p for p in playlists if p.get("collaborative")]
    foreign = [
        p
        for p in playlists
        if (p.get("owner") or {}).get("id") != my_id and not p.get("collaborative")
    ]
    print(f"playlists: {len(playlists)} own={len(own)} collab={len(collab)} foreign={len(foreign)}")

    picked: list[tuple[str, dict]] = []
    if own:
        # the biggest own playlist shows paging best
        picked.append(("own", max(own, key=lambda p: _total(p))))
    for i, p in enumerate(collab[:2]):
        picked.append((f"collab{i}", p))
    for i, p in enumerate(foreign[:3]):
        picked.append((f"followed{i}", p))

    for label, p in picked:
        pid = p["id"]
        rec.raw(f"playlist.{label}", f"/playlists/{pid}")
        rec.raw(f"playlist_items.{label}.limit2", f"/playlists/{pid}/items", {"limit": 2})
        if label == "own":
            rec.raw(f"limit.playlist_items.{label}.50", f"/playlists/{pid}/items", {"limit": 50})
            rec.raw(f"limit.playlist_items.{label}.100", f"/playlists/{pid}/items", {"limit": 100})
            rec.raw(f"limit.playlist_items.{label}.101", f"/playlists/{pid}/items", {"limit": 101})
            rec.raw(f"old.playlist_tracks.{label}", f"/playlists/{pid}/tracks", {"limit": 2})
            rec.all_pages(f"playlist_items.{label}.all", f"/playlists/{pid}/items", {"limit": 100})

    for i, text in enumerate(args.foreign):
        pid = parse_spotify_ref(text).id
        rec.raw(f"url_playlist.foreign{i}", f"/playlists/{pid}")
        rec.raw(f"url_playlist_items.foreign{i}", f"/playlists/{pid}/items", {"limit": 2})

    rec.raw("missing_playlist", "/playlists/0000000000000000000000")

    liked = rec.raw("me_tracks.limit2", "/me/tracks", {"limit": 2})
    if liked and liked.get("next"):
        rec.raw("me_tracks.limit2.p1", liked["next"])

    # album and track from the first liked track, else from the own playlist
    track = None
    if liked and liked.get("items"):
        first = liked["items"][0]
        track = first.get("track") or first.get("item")
    if track:
        album_id = track["album"]["id"]
        rec.raw("track", f"/tracks/{track['id']}")
        rec.raw("album", f"/albums/{album_id}")
        rec.raw("album_tracks.limit2", f"/albums/{album_id}/tracks", {"limit": 2})
        rec.raw("limit.album_tracks.50", f"/albums/{album_id}/tracks", {"limit": 50})
        rec.raw("limit.album_tracks.51", f"/albums/{album_id}/tracks", {"limit": 51})
        rec.raw("old.tracks_batch", "/tracks", {"ids": track["id"]})
    rec.raw("missing_track", "/tracks/0000000000000000000000")
    rec.raw("missing_album", "/albums/0000000000000000000000")

    (args.out / "_index.json").write_text(json.dumps(rec.index, indent=2), encoding="utf-8")
    ctx.close()


def _total(playlist: dict) -> int:
    for key in ("items", "tracks"):
        value = playlist.get(key)
        if isinstance(value, dict) and isinstance(value.get("total"), int):
            return value["total"]
    return 0


if __name__ == "__main__":
    main()
