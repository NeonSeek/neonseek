"""spotify login: authorization code with pkce and a short lived local callback server."""

from __future__ import annotations

import base64
import hashlib
import html
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from neonseek import secrets as secret_store
from neonseek.errors import NeonSeekError
from neonseek.i18n import tr

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
REDIRECT_HOST = "127.0.0.1"
REDIRECT_PORT = 43821
REDIRECT_PATH = "/callback"
REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}{REDIRECT_PATH}"
SCOPES = ("playlist-read-private", "playlist-read-collaborative", "user-library-read")
LOGIN_TIMEOUT_S = 300
# refresh a bit before spotify says the token expires
EXPIRY_MARGIN_S = 60


class SpotifyAuthError(NeonSeekError):
    pass


class NotLoggedInError(SpotifyAuthError):
    pass


class NoClientIdError(SpotifyAuthError):
    pass


def require_client_id(client_id: str) -> str:
    if not client_id.strip():
        raise NoClientIdError(
            tr(
                "Spotify is not set up yet. Add your Spotify Client ID in Settings, or keep "
                "using CSV files without Spotify."
            )
        )
    return client_id.strip()


def not_logged_in() -> NotLoggedInError:
    return NotLoggedInError(tr("Log in to Spotify first. Spotify lists need a login."))


def make_verifier() -> str:
    # 86 characters from the unreserved set, the spec allows 43 to 128
    return secrets.token_urlsafe(64)


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(client_id: str, challenge: str, state: str) -> str:
    query = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "scope": " ".join(SCOPES),
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


@dataclass
class Tokens:
    access_token: str
    expires_at: float
    refresh_token: str | None = None


def _token_request(http: httpx.Client, data: dict[str, str]) -> Tokens:
    try:
        response = http.post(TOKEN_URL, data=data)
    except httpx.TransportError as exc:
        raise SpotifyAuthError(
            tr("Could not reach Spotify. Check your internet connection and try again.")
        ) from exc
    if response.status_code != 200:
        try:
            error = response.json().get("error", "")
        except ValueError:
            error = ""
        if error == "invalid_grant":
            raise NotLoggedInError(
                tr("Your Spotify login has expired or was revoked. Log in to Spotify again.")
            )
        if error == "invalid_client":
            raise SpotifyAuthError(
                tr(
                    "Spotify does not accept this Client ID. Copy the Client ID from your app "
                    "in the Spotify Developer Dashboard into Settings again."
                )
            )
        raise SpotifyAuthError(
            tr("Spotify login failed (HTTP {status}, {error}). Try logging in again.").format(
                status=response.status_code, error=error or "no details"
            )
        )
    body = response.json()
    return Tokens(
        access_token=body["access_token"],
        expires_at=time.time() + int(body.get("expires_in", 3600)),
        refresh_token=body.get("refresh_token"),
    )


def exchange_code(http: httpx.Client, client_id: str, code: str, verifier: str) -> Tokens:
    return _token_request(
        http,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )


def refresh_tokens(http: httpx.Client, client_id: str, refresh_token: str) -> Tokens:
    return _token_request(
        http,
        {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id},
    )


@dataclass
class CallbackResult:
    code: str | None
    state: str | None
    error: str | None


_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>NeonSeek</title></head>
<body style="font-family:monospace;background:#1A1230;color:#EDE9F7;padding:3em">
<h1>NeonSeek</h1><p>{text}</p></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    server: _CallbackHTTPServer

    def do_GET(self) -> None:  # noqa: N802 (http.server api)
        parts = urlsplit(self.path)
        if parts.path != REDIRECT_PATH:
            self.send_error(404)
            return
        query = parse_qs(parts.query)
        result = CallbackResult(
            code=query.get("code", [None])[0],
            state=query.get("state", [None])[0],
            error=query.get("error", [None])[0],
        )
        if result.code:
            text = tr("Spotify login finished. You can close this tab and go back to NeonSeek.")
        else:
            text = tr("Spotify login was not completed. Go back to NeonSeek and try again.")
        body = _PAGE.format(text=html.escape(text)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.server.deliver(result)

    def log_message(self, format: str, *args: object) -> None:
        # the request line holds the authorization code, never log it
        pass


class _CallbackHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int) -> None:
        super().__init__((REDIRECT_HOST, port), _Handler)
        self.result: CallbackResult | None = None
        self.done = threading.Event()

    def deliver(self, result: CallbackResult) -> None:
        if not self.done.is_set():
            self.result = result
            self.done.set()


class LoginFlow:
    """one login attempt. start() opens the local server, wait() returns the tokens."""

    def __init__(self, client_id: str, http: httpx.Client, port: int = REDIRECT_PORT) -> None:
        self.client_id = require_client_id(client_id)
        self.http = http
        self.port = port
        self.verifier = make_verifier()
        self.state = secrets.token_urlsafe(16)
        self.url = authorize_url(self.client_id, challenge_for(self.verifier), self.state)
        self._server: _CallbackHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        try:
            self._server = _CallbackHTTPServer(self.port)
        except OSError as exc:
            raise SpotifyAuthError(
                tr(
                    "Port {port} on this computer is busy, so the Spotify login cannot finish. "
                    "Close other NeonSeek windows or logins in progress and try again."
                ).format(port=self.port)
            ) from exc
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    def cancel(self) -> None:
        if self._server is not None:
            self._server.deliver(CallbackResult(None, None, "cancelled"))

    def _stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def wait(self, timeout: float = LOGIN_TIMEOUT_S) -> Tokens:
        if self._server is None:
            raise RuntimeError("start() first")
        try:
            if not self._server.done.wait(timeout):
                raise SpotifyAuthError(
                    tr(
                        "Spotify login timed out. Try again. If the browser showed an error, "
                        "check that the Redirect URI in your Spotify app is exactly {uri}"
                    ).format(uri=REDIRECT_URI)
                )
            result = self._server.result
        finally:
            self._stop()
        assert result is not None
        if result.error == "cancelled":
            raise SpotifyAuthError(tr("Spotify login was cancelled."))
        if result.error == "access_denied":
            raise SpotifyAuthError(
                tr("Spotify login was declined. Log in again and choose Agree to use Spotify.")
            )
        if result.error or not result.code:
            raise SpotifyAuthError(
                tr("Spotify login failed ({error}). Try again.").format(
                    error=result.error or "no code"
                )
            )
        if result.state != self.state:
            raise SpotifyAuthError(
                tr("Spotify login answer did not match this login. Start the login again.")
            )
        return exchange_code(self.http, self.client_id, result.code, self.verifier)


class TokenStore(Protocol):
    def load(self) -> str | None: ...
    def save(self, refresh_token: str) -> None: ...
    def clear(self) -> None: ...


class KeyringTokenStore:
    def load(self) -> str | None:
        return secret_store.get_secret(secret_store.SPOTIFY_REFRESH_TOKEN)

    def save(self, refresh_token: str) -> None:
        secret_store.set_secret(secret_store.SPOTIFY_REFRESH_TOKEN, refresh_token)

    def clear(self) -> None:
        secret_store.delete_secret(secret_store.SPOTIFY_REFRESH_TOKEN)


class SpotifyAuth:
    """hands out access tokens. the access token stays in memory, the refresh token in keyring."""

    def __init__(
        self,
        client_id: str,
        http: httpx.Client,
        store: TokenStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.client_id = require_client_id(client_id)
        self.http = http
        self.store = store or KeyringTokenStore()
        self.clock = clock
        self._tokens: Tokens | None = None

    def logged_in(self) -> bool:
        return self.store.load() is not None

    def accept(self, tokens: Tokens) -> None:
        # pkce refresh tokens rotate, always keep the newest one
        if tokens.refresh_token:
            self.store.save(tokens.refresh_token)
        self._tokens = tokens

    def access_token(self) -> str:
        if self._tokens is not None and self.clock() < self._tokens.expires_at - EXPIRY_MARGIN_S:
            return self._tokens.access_token
        refresh_token = self.store.load()
        if not refresh_token:
            raise not_logged_in()
        try:
            tokens = refresh_tokens(self.http, self.client_id, refresh_token)
        except NotLoggedInError:
            self.store.clear()
            raise
        self.accept(tokens)
        return tokens.access_token

    def invalidate(self) -> None:
        self._tokens = None

    def logout(self) -> None:
        self._tokens = None
        self.store.clear()
