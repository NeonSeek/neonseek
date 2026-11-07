from __future__ import annotations

import base64
import hashlib
import re
import socket
import threading
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from neonseek import secrets as secret_store
from neonseek.spotify import auth
from neonseek.spotify.auth import (
    LoginFlow,
    NoClientIdError,
    NotLoggedInError,
    SpotifyAuth,
    SpotifyAuthError,
)
from spotify_fake import FakeSpotify, MemoryTokenStore


@pytest.fixture
def fake_spotify() -> FakeSpotify:
    # only the token endpoint is used here, it is the same for both app variants
    return FakeSpotify()


def _http(fake: FakeSpotify) -> httpx.Client:
    return httpx.Client(transport=fake.transport())


def test_verifier_and_challenge():
    verifier = auth.make_verifier()
    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9\-._~]+", verifier)
    challenge = auth.challenge_for(verifier)
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert challenge == expected.decode().rstrip("=")
    assert "=" not in challenge and "+" not in challenge and "/" not in challenge


def test_authorize_url():
    url = auth.authorize_url("cid", "chal", "st")
    parts = urlsplit(url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == auth.AUTHORIZE_URL
    assert query == {
        "client_id": "cid",
        "response_type": "code",
        "redirect_uri": "http://127.0.0.1:43821/callback",
        "code_challenge_method": "S256",
        "code_challenge": "chal",
        "scope": "playlist-read-private playlist-read-collaborative user-library-read",
        "state": "st",
    }


def test_no_client_id_asks_for_settings():
    with pytest.raises(NoClientIdError, match="Client ID in Settings"):
        LoginFlow("  ", httpx.Client())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _browser(port: int, query: str) -> threading.Thread:
    # what the browser does after the user clicks agree
    def hit():
        httpx.get(f"http://127.0.0.1:{port}/favicon.ico")
        httpx.get(f"http://127.0.0.1:{port}/callback?{query}")

    thread = threading.Thread(target=hit)
    thread.start()
    return thread


def test_login_flow_exchanges_code(fake_spotify):
    port = _free_port()
    flow = LoginFlow("cid", _http(fake_spotify), port=port)
    flow.start()
    thread = _browser(port, f"code=the-code&state={flow.state}")
    tokens = flow.wait(timeout=10)
    thread.join()
    assert tokens.access_token == "access-1"
    assert tokens.refresh_token == "refresh-1"
    sent = fake_spotify.token_requests[0]
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "the-code"
    assert sent["code_verifier"] == flow.verifier
    assert sent["redirect_uri"] == auth.REDIRECT_URI
    assert "client_secret" not in sent


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("error=access_denied&state={state}", "declined"),
        ("code=x&state=wrong", "did not match"),
        ("state={state}", "failed"),
    ],
)
def test_login_flow_errors(fake_spotify, query, message):
    port = _free_port()
    flow = LoginFlow("cid", _http(fake_spotify), port=port)
    flow.start()
    thread = _browser(port, query.format(state=flow.state))
    with pytest.raises(SpotifyAuthError, match=message):
        flow.wait(timeout=10)
    thread.join()
    assert fake_spotify.token_requests == []


def test_login_flow_timeout_mentions_redirect_uri(fake_spotify):
    flow = LoginFlow("cid", _http(fake_spotify), port=_free_port())
    flow.start()
    with pytest.raises(SpotifyAuthError, match="127.0.0.1:43821/callback"):
        flow.wait(timeout=0.2)


def test_login_flow_cancel(fake_spotify):
    flow = LoginFlow("cid", _http(fake_spotify), port=_free_port())
    flow.start()
    flow.cancel()
    with pytest.raises(SpotifyAuthError, match="cancelled"):
        flow.wait(timeout=10)


def test_busy_port_is_explained(fake_spotify):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        flow = LoginFlow("cid", _http(fake_spotify), port=busy.getsockname()[1])
        with pytest.raises(SpotifyAuthError, match="busy"):
            flow.start()


def test_refresh_keeps_newest_refresh_token(fake_spotify):
    store = MemoryTokenStore("refresh-0")
    spotify_auth = SpotifyAuth("cid", _http(fake_spotify), store)
    assert spotify_auth.access_token() == "access-1"
    # cached until it expires
    assert spotify_auth.access_token() == "access-1"
    assert store.token == "refresh-1"
    spotify_auth.invalidate()
    assert spotify_auth.access_token() == "access-2"
    assert store.token == "refresh-2"
    assert fake_spotify.token_requests[0] == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh-0",
        "client_id": "cid",
    }


def test_expired_access_token_is_refreshed(fake_spotify):
    now = [1050.0]
    spotify_auth = SpotifyAuth(
        "cid", _http(fake_spotify), MemoryTokenStore("r"), clock=lambda: now[0]
    )
    spotify_auth.accept(auth.Tokens("old", expires_at=1100.0))
    assert spotify_auth.access_token() == "access-1"  # within the safety margin


def test_revoked_login_is_forgotten(fake_spotify):
    fake_spotify.revoked = True
    store = MemoryTokenStore("refresh-0")
    spotify_auth = SpotifyAuth("cid", _http(fake_spotify), store)
    with pytest.raises(NotLoggedInError, match="Log in to Spotify again"):
        spotify_auth.access_token()
    assert store.token is None


def test_no_refresh_token_asks_for_login(fake_spotify):
    with pytest.raises(NotLoggedInError):
        SpotifyAuth("cid", _http(fake_spotify), MemoryTokenStore()).access_token()


def test_keyring_store_uses_credential_manager_backend(_isolated):
    store = auth.KeyringTokenStore()
    store.save("secret-token")
    assert _isolated.values[(secret_store.SERVICE, secret_store.SPOTIFY_REFRESH_TOKEN)] == (
        "secret-token"
    )
    assert store.load() == "secret-token"
    store.clear()
    store.clear()  # clearing twice is fine
    assert store.load() is None
