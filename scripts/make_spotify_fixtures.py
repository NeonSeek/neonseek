"""phase 1: capture a small set of real spotify responses and write them anonymized to
tests/fixtures/spotify. raw responses stay in memory, only the anonymized copy is written.

anonymized: user ids, display names, account id, profile images, ids, names, descriptions
and images of playlists. tracks, albums and artists come out real: run
scripts/fake_spotify_catalog.py right after, it swaps them for a made up catalog (D-212).
the run fails if any of the replaced values is still somewhere in the output.

usage:
  uv run python scripts/make_spotify_fixtures.py --data-dir <dir> --album <album id> \
      --spotify-playlist <playlist id> [--out tests/fixtures/spotify]
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from neonseek import actions
from neonseek.spotify import models
from neonseek.spotify.client import API_URL

ROOT = Path(__file__).resolve().parents[1]
TEST_USER_ID = "neonseek-test-user"
TEST_USER_NAME = "Test User"
OPEN = "https://open.spotify.com"


class Capture:
    def __init__(self, ctx: actions.AppContext) -> None:
        self.ctx = ctx
        self.auth = ctx.spotify().auth
        self.records: dict[str, dict[str, Any]] = {}

    def get(self, name: str, path: str, params: dict[str, Any] | None = None) -> Any:
        url = path if path.startswith("http") else f"{API_URL}{path}"
        headers = {"Authorization": f"Bearer {self.auth.access_token()}"}
        response = self.ctx.http.get(url, params=params, headers=headers)
        parts = urlsplit(str(response.request.url))
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.records[name] = {
            "request": {"path": parts.path.removeprefix("/v1"), "params": query},
            "status": response.status_code,
            "body": response.json(),
        }
        print(f"{response.status_code}  {name}")
        return response.json() if response.status_code == 200 else None

    def pages(self, name: str, path: str, params: dict[str, Any]) -> None:
        body = self.get(f"{name}.p0", path, params)
        index = 1
        while body and body.get("next"):
            body = self.get(f"{name}.p{index}", body["next"])
            index += 1


class Anonymizer:
    def __init__(self, me: dict[str, Any]) -> None:
        self.me_id = me["id"]
        self.users: dict[str, tuple[str, str]] = {self.me_id: (TEST_USER_ID, TEST_USER_NAME)}
        self.playlists: dict[str, tuple[str, str]] = {}
        # every original value we replace, checked against the output at the end
        self.secrets: set[str] = {self.me_id}
        for key in ("display_name", "account_id"):
            if me.get(key):
                self.secrets.add(me[key])
        if me.get("display_name"):
            self.secrets.update(w for w in me["display_name"].split() if len(w) >= 4)
        self.secrets.update(i["url"] for i in me.get("images") or [])

    def user(self, user_id: str, name: str | None) -> tuple[str, str]:
        if user_id not in self.users:
            n = len(self.users)
            self.users[user_id] = (f"other-user-{n}", f"Other User {n}")
        self.secrets.add(user_id)
        if name:
            self.secrets.add(name)
        return self.users[user_id]

    def playlist(self, playlist_id: str, name: str | None, owned: bool) -> tuple[str, str]:
        if playlist_id not in self.playlists:
            n = len(self.playlists) + 1
            label = "Own" if owned else "Followed"
            self.playlists[playlist_id] = (f"0NeonFixturePlaylist{n:02d}", f"{label} Playlist {n}")
        self.secrets.add(playlist_id)
        if name and len(name) >= 3:
            self.secrets.add(name)
        return self.playlists[playlist_id]

    def walk(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self.walk(v) for v in value]
        if not isinstance(value, dict):
            return value
        obj = {k: self.walk(v) for k, v in value.items()}
        if obj.get("type") == "user" and obj.get("id"):
            new_id, new_name = self.user(value["id"], value.get("display_name"))
            obj["id"] = new_id
            obj["href"] = f"{API_URL}/users/{new_id}"
            obj["uri"] = f"spotify:user:{new_id}"
            obj["external_urls"] = {"spotify": f"{OPEN}/user/{new_id}"}
            if "display_name" in obj:
                obj["display_name"] = new_name
            if "images" in obj:
                obj["images"] = []
            if "followers" in obj:
                obj["followers"] = {"href": None, "total": 0}
            if "account_id" in obj:
                obj["account_id"] = "fixture-account"
        if obj.get("type") == "playlist" and obj.get("id"):
            owned = (value.get("owner") or {}).get("id") == self.me_id
            new_id, new_name = self.playlist(value["id"], value.get("name"), owned)
            obj["id"] = new_id
            obj["name"] = new_name
            obj["description"] = ""
            obj["href"] = f"{API_URL}/playlists/{new_id}"
            obj["uri"] = f"spotify:playlist:{new_id}"
            obj["external_urls"] = {"spotify": f"{OPEN}/playlist/{new_id}"}
            obj["images"] = [
                {"height": 640, "url": "https://mosaic.scdn.co/640/fixture", "width": 640}
            ]
            if obj.get("followers"):
                obj["followers"] = {"href": None, "total": 0}
            if value.get("description"):
                self.secrets.add(value["description"])
        return obj

    def text_pass(self, text: str) -> str:
        # ids inside next/href urls and request paths
        for old, (new, _) in {**self.users, **self.playlists}.items():
            text = text.replace(old, new)
        return text


def _total(playlist: dict[str, Any]) -> int:
    return models.content_total(playlist) or 0


def shrink(value: Any) -> Any:
    # available_markets lists 185 country codes per track and album, we never read it
    if isinstance(value, list):
        return [shrink(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {k: shrink(v) for k, v in value.items()}
    if isinstance(out.get("available_markets"), list):
        out["available_markets"] = out["available_markets"][:2]
    return out


def trim_page(record: dict[str, Any], keep: int, *, last: bool) -> None:
    body = record["body"]
    body["items"] = body["items"][:keep]
    if last:
        body["next"] = None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--album", required=True, help="album with 5 to 14 tracks")
    parser.add_argument("--spotify-playlist", required=True, help="playlist made by spotify")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    ctx = actions.AppContext(args.data_dir)
    cap = Capture(ctx)
    me = cap.get("me", "/me")
    listing = cap.get("me_playlists", "/me/playlists", {"limit": 50})
    playlists = [p for p in listing["items"] if p]
    own = [p for p in playlists if p["owner"]["id"] == me["id"] and 3 <= _total(p) <= 6]
    own_small = min(own, key=lambda p: (_total(p) != 5, _total(p)))
    foreign = [p for p in playlists if p["owner"]["id"] != me["id"]]
    followed = min(foreign, key=_total)

    cap.get("playlist.own", f"/playlists/{own_small['id']}")
    cap.pages("playlist_items.own", f"/playlists/{own_small['id']}/items", {"limit": 2})
    cap.get("playlist.followed", f"/playlists/{followed['id']}")
    cap.get("playlist_items.followed.probe", f"/playlists/{followed['id']}/items", {"limit": 1})
    cap.get("playlist.spotify_made", f"/playlists/{args.spotify_playlist}")

    liked0 = cap.get("me_tracks.p0", "/me/tracks", {"limit": 2})
    cap.get("me_tracks.p1", liked0["next"])
    first = liked0["items"][0].get("item") or liked0["items"][0]["track"]
    single_album = first["album"]["id"]
    cap.get(f"track.{first['id']}", f"/tracks/{first['id']}")
    cap.get("album.single", f"/albums/{single_album}")
    cap.pages("album_tracks.single", f"/albums/{single_album}/tracks", {"limit": 50})

    cap.get("album.multi", f"/albums/{args.album}")
    cap.pages("album_tracks.multi", f"/albums/{args.album}/tracks", {"limit": 5})
    for name in [n for n in cap.records if n.startswith("album_tracks.multi")]:
        for item in cap.records[name]["body"]["items"]:
            cap.get(f"track.{item['id']}", f"/tracks/{item['id']}")
    ctx.close()

    records = cap.records
    # trimmed lists, documented in the fixture readme
    listing_rec = records["me_playlists"]
    listing_rec["body"]["items"] = [own_small, followed]
    listing_rec["body"]["total"] = 2
    listing_rec["body"]["next"] = None
    # an older developer app gets the content of someone else's playlist, a new one does not
    followed_body = records["playlist.followed"]["body"]
    for key in ("items", "tracks"):
        if isinstance(followed_body.get(key), dict) and "items" in followed_body[key]:
            followed_body[key]["items"] = followed_body[key]["items"][:2]
    trim_page(records["me_tracks.p1"], 2, last=True)
    for name in ("me_tracks.p0", "me_tracks.p1"):
        records[name]["body"]["total"] = 4

    anon = Anonymizer(me)
    out_texts: dict[str, str] = {}
    for name, record in records.items():
        clean = copy.deepcopy(record)
        clean["body"] = shrink(anon.walk(clean["body"]))
        out_texts[name] = anon.text_pass(json.dumps(clean, ensure_ascii=False, indent=2) + "\n")

    leaks = []
    for name, text in out_texts.items():
        for secret in anon.secrets:
            if secret and secret in text:
                leaks.append((name, len(secret)))
        if re.search(r"[?&]si=", text):
            leaks.append((name, "si"))
    if leaks:
        raise SystemExit(f"anonymization leak, nothing written: {leaks}")

    args.out.mkdir(parents=True, exist_ok=True)
    for old in args.out.glob("*.json"):
        old.unlink()
    for name, text in out_texts.items():
        (args.out / f"{name}.json").write_text(text, encoding="utf-8", newline="\n")
    ids = {
        "own_playlist": anon.playlists[own_small["id"]][0],
        "followed_playlist": anon.playlists[followed["id"]][0],
        "spotify_playlist": args.spotify_playlist,
        "liked_first_track": first["id"],
        "single_album": single_album,
        "multi_album": args.album,
    }
    (args.out / "_ids.json").write_text(
        json.dumps(ids, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {len(out_texts)} files, checked {len(anon.secrets)} replaced values")


if __name__ == "__main__":
    main()
