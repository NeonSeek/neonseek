from __future__ import annotations

import httpx
import pytest

from neonseek.spotify.auth import SpotifyAuth
from neonseek.spotify.client import SpotifyApiError, SpotifyClient
from spotify_fake import MemoryTokenStore


class Script:
    """api answers in order, plus a token endpoint that always works."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "accounts.spotify.com":
            n = sum(1 for c in self.calls if c == "token") + 1
            self.calls.append("token")
            return httpx.Response(
                200, json={"access_token": f"a{n}", "expires_in": 3600, "refresh_token": f"r{n}"}
            )
        self.calls.append(str(request.url))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _client(script: Script, sleeps: list[float], waits: list[float] | None = None):
    http = httpx.Client(transport=httpx.MockTransport(script.handler))
    auth = SpotifyAuth("cid", http, MemoryTokenStore("r0"))
    return SpotifyClient(auth, http, sleep=sleeps.append, on_wait=(waits or []).append)


def test_rate_limit_waits_retry_after():
    script = Script(
        [
            httpx.Response(429, headers={"Retry-After": "7"}, json={"error": {"status": 429}}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    sleeps, waits = [], []
    client = _client(script, sleeps, waits)
    assert client.get("/me") == {"ok": True}
    assert sleeps == [7.0]


def test_long_rate_limit_gives_up_with_advice():
    script = Script(
        [
            httpx.Response(
                429,
                headers={"Retry-After": "3600"},
                json={"error": {"status": 429, "reason": "QUOTA_EXCEEDED"}},
            )
        ]
    )
    with pytest.raises(SpotifyApiError, match="wait 60 minutes") as info:
        _client(script, []).get("/me")
    assert info.value.status == 429


def test_401_refreshes_once():
    script = Script([httpx.Response(401), httpx.Response(200, json={"id": "x"})])
    assert _client(script, []).get("/me") == {"id": "x"}
    assert script.calls.count("token") == 2


def test_server_errors_are_retried_then_reported():
    script = Script([httpx.Response(502)] * 4)
    sleeps = []
    with pytest.raises(SpotifyApiError, match="HTTP 502") as info:
        _client(script, sleeps).get("/me")
    assert sleeps == [2, 4, 8]
    assert info.value.status == 502


def test_network_error_says_what_to_do():
    script = Script([httpx.ConnectError("down")])
    with pytest.raises(SpotifyApiError, match="internet connection"):
        _client(script, []).get("/me")


def test_404_keeps_status_and_body():
    body = {"error": {"status": 404, "message": "Resource not found"}}
    script = Script([httpx.Response(404, json=body)])
    with pytest.raises(SpotifyApiError) as info:
        _client(script, []).get("/playlists/x")
    assert info.value.status == 404
    assert info.value.body == body


def test_pages_follow_next():
    script = Script(
        [
            httpx.Response(200, json={"items": [1, 2], "next": "https://api.spotify.com/v1/x?o=2"}),
            httpx.Response(200, json={"items": [3], "next": None}),
        ]
    )
    assert list(_client(script, []).items("/x", {"limit": 2})) == [1, 2, 3]
    assert script.calls[-1] == "https://api.spotify.com/v1/x?o=2"
