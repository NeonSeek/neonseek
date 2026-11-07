"""apple music, youtube and soundcloud for tests, from made up answers in fixtures/web.

FakeApple answers the lookup api and music.apple.com pages the way the real ones did when
checked (docs/playlist-sources-notes.md): 50 songs per album lookup by default, at most 200,
several ids at once. FakeExtractor stands in for yt-dlp. FakeSoundCloudNet answers yt-dlp's
own requests, so the real yt-dlp code runs on saved answers (the contract test).
"""

from __future__ import annotations

import copy
import io
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from neonseek.sources.ytdlp import BatchUnavailable

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "web"
# the playlist of apple_playlist.html
PLAYLIST_ID = "pl.0123456789abcdef0123456789abcdef"
# the playlist, album and track of tidal_embed_*.html: made up data in the markup of real
# embed pages (docs/tidal-notes.md)
TIDAL_PLAYLIST_ID = "0f1e2d3c-4b5a-4987-8876-5a4b3c2d1e0f"
TIDAL_ALBUM_ID = "900000100"
TIDAL_TRACK_ID = "900000101"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeApple:
    def __init__(self) -> None:
        catalog = load("apple_catalog.json")
        self.albums: dict[str, dict] = {str(a["collectionId"]): a for a in catalog["albums"]}
        self.songs: dict[str, dict] = {str(s["trackId"]): s for s in catalog["songs"]}
        # path of a page -> (status, html)
        self.pages: dict[str, tuple[int, str]] = {}
        self.requests: list[str] = []
        self.error: Exception | None = None
        self.status: int | None = None
        # the same http client reads tidal embed pages
        self.tidal = FakeTidal()

    def add_page(self, path: str, html: str, status: int = 200) -> None:
        self.pages[path] = (status, html)

    def _lookup(self, params: dict[str, list[str]]) -> dict:
        ids = (params.get("id") or [""])[0].split(",")
        entity = (params.get("entity") or [None])[0]
        limit = min(int((params.get("limit") or ["50"])[0]), 200)
        results: list[dict] = []
        for item_id in ids:
            if item_id in self.albums:
                results.append(self.albums[item_id])
                if entity == "song":
                    songs = sorted(
                        (s for s in self.songs.values() if str(s["collectionId"]) == item_id),
                        key=lambda s: (s.get("discNumber") or 0, s.get("trackNumber") or 0),
                    )
                    results += songs[:limit]
            elif item_id in self.songs:
                results.append(self.songs[item_id])
        return {"resultCount": len(results), "results": results}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "embed.tidal.com":
            return self.tidal.handler(request)
        self.requests.append(str(request.url))
        if self.error is not None:
            raise self.error
        if self.status is not None:
            return httpx.Response(self.status, text="")
        url = urlsplit(str(request.url))
        if url.hostname == "itunes.apple.com" and url.path == "/lookup":
            body = json.dumps(self._lookup(parse_qs(url.query)))
            return httpx.Response(200, text=body, headers={"content-type": "text/javascript"})
        if url.hostname == "music.apple.com":
            status, html = self.pages.get(url.path, (404, "<html>not found</html>"))
            return httpx.Response(status, text=html, headers={"content-type": "text/html"})
        raise AssertionError(f"the fake apple music does not know {request.url}")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


class FakeTidal:
    """embed.tidal.com with saved pages. an unknown id answers like tidal: 404 and its error
    page."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[int, str]] = {
            f"/playlists/{TIDAL_PLAYLIST_ID}": (200, text("tidal_embed_playlist.html")),
            f"/albums/{TIDAL_ALBUM_ID}": (200, text("tidal_embed_album.html")),
            f"/tracks/{TIDAL_TRACK_ID}": (200, text("tidal_embed_track.html")),
        }
        self.requests: list[str] = []
        self.error: Exception | None = None
        self.status: int | None = None

    def add_page(self, path: str, html: str, status: int = 200) -> None:
        self.pages[path] = (status, html)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        if self.error is not None:
            raise self.error
        if self.status is not None:
            return httpx.Response(self.status, text="")
        status, html = self.pages.get(request.url.path, (404, text("tidal_embed_error.html")))
        return httpx.Response(status, text=html, headers={"content-type": "text/html"})


_TIDAL_ITEM = re.compile(r"<list-item\b.*?</list-item>", re.S)
_TIDAL_LIST = re.compile(r'(<media-item-list role="list">)(.*?)(</media-item-list>)', re.S)


def tidal_page(count: int, first_id: int = 900100001) -> str:
    """a playlist embed page with this many made up tracks, in the markup of the saved one."""
    page = text("tidal_embed_playlist.html")
    item = _TIDAL_ITEM.search(page).group(0)
    items = []
    for n in range(count):
        one = re.sub(r'product-id="\d+"', f'product-id="{first_id + n}"', item, count=1)
        one = re.sub(r'(<span slot="index">)\d+', rf"\g<1>{n + 1}", one)
        one = re.sub(r'(<span slot="title">)[^<]*', rf"\g<1>Fixture Song {n + 1}", one)
        items.append(one)
    return _TIDAL_LIST.sub(lambda m: m.group(1) + "".join(items) + m.group(3), page, count=1)


def apple_page(playlist_id: str, songs: list[dict], title: str = "Big Fixture",
               next_page: bool = False) -> str:  # fmt: skip
    """a music.apple.com page with these songs, built like apple_playlist.html."""
    items = [
        {
            "id": f"track-lockup - {playlist_id} - {s['trackId']}",
            "title": s["trackName"],
            "artistName": s["artistName"],
            "duration": s.get("trackTimeMillis"),
            "tertiaryLinks": [{"title": s.get("collectionName")}],
            "contentDescriptor": {
                "kind": "song",
                "identifiers": {"storeAdamID": str(s["trackId"])},
            },
        }
        for s in songs
    ]
    page: dict[str, Any] = {
        "sections": [
            {"itemKind": "containerDetailHeaderLockup",
             "items": [{"title": title, "trackCount": len(songs)}]},
            {"itemKind": "trackLockup", "items": items},
        ],
        "seoData": {"pageTitle": f"{title} - Playlist - Apple Music"},
    }  # fmt: skip
    if next_page:
        page["nextIntent"] = {
            "$kind": "PlaylistPaginationIntent",
            "url": f"/v1/catalog/pl/playlists/{playlist_id}/tracks?l=en-US&offset=300",
        }
    data = json.dumps({"data": [{"intent": {}, "data": page}], "userTokenHash": ""})
    return (
        f'<html><script type="application/json" id="serialized-server-data">{data}</script></html>'
    )


def fake_song(track_id: int, album_id: int, number: int, name: str = "Fixture Song") -> dict:
    return {
        "wrapperType": "track", "kind": "song", "trackId": track_id, "collectionId": album_id,
        "artistName": "Neon Fixture Artist", "collectionName": "Huge Fixture Box",
        "trackName": f"{name} {number}", "trackNumber": number, "discNumber": 1,
        "trackTimeMillis": 200000 + number, "releaseDate": "2020-01-01T12:00:00Z",
        "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/x/cover.jpg/100x100bb.jpg",
    }  # fmt: skip


class FakeExtractor:
    """yt-dlp for tests: urls answer with saved yt-dlp output, nothing goes online."""

    def __init__(self) -> None:
        self.answers: dict[str, Any] = {}
        self.tracks: dict[str, dict] = {}  # soundcloud id -> yt-dlp track info
        self.batch_broken = False
        self.calls: list[tuple[str, Any]] = []

    def answer(self, url: str, info: Any) -> None:
        self.answers[url] = info

    def extract(self, url: str) -> dict:
        self.calls.append(("extract", url))
        found = self.answers.get(url)
        if found is None:
            raise AssertionError(f"the fake yt-dlp does not know {url}")
        if isinstance(found, BaseException):
            raise found
        return copy.deepcopy(found)

    def soundcloud_tracks(self, ids: list[str]) -> dict[str, dict]:
        self.calls.append(("batch", list(ids)))
        if self.batch_broken:
            raise BatchUnavailable("yt-dlp changed")
        return {i: copy.deepcopy(self.tracks[i]) for i in ids if i in self.tracks}

    def resolve(self, url: str) -> dict:
        self.calls.append(("resolve", url))
        for info in self.tracks.values():
            if url in (
                info.get("webpage_url"),
                f"https://api-v2.soundcloud.com/tracks/{info['id']}",
            ):
                return copy.deepcopy(info)
        raise RuntimeError(f"HTTP Error 404: Not Found ({url})")


def youtube_extractor() -> FakeExtractor:
    fake = FakeExtractor()
    playlist = load("ytdlp_youtube_playlist.json")
    fake.answer(playlist["webpage_url"], playlist)
    video = load("ytdlp_youtube_video.json")
    fake.answer(video["webpage_url"], video)
    return fake


def soundcloud_extractor() -> FakeExtractor:
    fake = FakeExtractor()
    saved = load("ytdlp_soundcloud.json")
    fake.answer(saved["set"]["webpage_url"], saved["set"])
    fake.tracks = saved["tracks"]
    for info in saved["tracks"].values():
        fake.answer(info["webpage_url"], info)
    return fake


class FakeSoundCloudNet:
    """answers the requests yt-dlp's soundcloud extractor makes, from soundcloud_api.json.
    anything else fails the test: a new request after a yt-dlp update shows up here."""

    def __init__(self) -> None:
        self.api = load("soundcloud_api.json")
        self.requests: list[str] = []

    def urlopen(self, ydl, request):
        from yt_dlp.networking import Response
        from yt_dlp.networking.common import Request
        from yt_dlp.networking.exceptions import HTTPError

        if isinstance(request, str):
            request = Request(request)
        url = urlsplit(request.url)
        query = parse_qs(url.query)
        self.requests.append(f"{request.method} {url.scheme}://{url.netloc}{url.path}")

        def reply(body: Any, kind: str = "application/json", status: int = 200):
            data = body if isinstance(body, str) else json.dumps(body)
            response = Response(io.BytesIO(data.encode("utf-8")), request.url,
                                {"Content-Type": kind}, status=status)  # fmt: skip
            if status >= 400:
                raise HTTPError(response)
            return response

        client_id = self.api["client_id"]
        if url.netloc == "soundcloud.com" and url.path in ("", "/"):
            return reply('<html><script crossorigin src="https://a-v2.sndcdn.com/assets/'
                         'fixture-app.js"></script></html>', "text/html")  # fmt: skip
        if url.netloc == "a-v2.sndcdn.com":
            return reply(f'({{client_id:"{client_id}",env:"production"}})', "text/javascript")
        if url.netloc == "api-v2.soundcloud.com":
            if (query.get("client_id") or [""])[0] != client_id:
                return reply({"errors": []}, status=401)
            if url.path == "/resolve":
                wanted = (query.get("url") or [""])[0]
                if wanted == self.api["resolve_set"]["permalink_url"]:
                    return reply(self.api["resolve_set"])
                return reply({}, status=404)
            if url.path == "/tracks":
                ids = (query.get("ids") or [""])[0].split(",")
                return reply([t for t in self.api["tracks"] if str(t["id"]) in ids])
        if request.method == "HEAD" and url.netloc == "i1.sndcdn.com":
            # the set's own thumbnail: yt-dlp checks whether the original exists
            return reply("", "image/jpeg", status=404)
        raise AssertionError(f"yt-dlp made a request the saved answers do not have: {request.url}")


def soundcloud_fixture_ids() -> list[str]:
    return [str(t["id"]) for t in load("soundcloud_api.json")["resolve_set"]["tracks"]]


ARTWORK = re.compile(r"-t500x500\.jpg$")
