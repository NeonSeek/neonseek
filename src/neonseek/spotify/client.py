"""thin spotify web api client: user token, paging by next, 401 refresh, 429 retry-after."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from neonseek.errors import NeonSeekError
from neonseek.i18n import tr
from neonseek.spotify.auth import SpotifyAuth

API_URL = "https://api.spotify.com/v1"
HTTP_TIMEOUT_S = 20.0
# waiting longer than this for a rate limit is not worth blocking the app
MAX_RETRY_AFTER_S = 120
SERVER_ERROR_RETRIES = 3
RATE_LIMIT_RETRIES = 5


class SpotifyApiError(NeonSeekError):
    def __init__(self, message: str, status: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def new_http_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT_S, transport=transport)


def _error_message(body: Any) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or "")
        if isinstance(error, str):
            return error
    return ""


class SpotifyClient:
    def __init__(
        self,
        auth: SpotifyAuth,
        http: httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
        on_wait: Callable[[float], None] | None = None,
    ) -> None:
        self.auth = auth
        self.http = http
        self.sleep = sleep
        # tells the ui that we wait for a spotify rate limit
        self.on_wait = on_wait

    def get(self, path_or_url: str, params: dict[str, Any] | None = None) -> Any:
        url = path_or_url if path_or_url.startswith("http") else f"{API_URL}{path_or_url}"
        refreshed = False
        server_errors = 0
        rate_limits = 0
        while True:
            headers = {"Authorization": f"Bearer {self.auth.access_token()}"}
            try:
                response = self.http.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                raise SpotifyApiError(
                    tr("Could not reach Spotify. Check your internet connection and try again.")
                ) from exc

            status = response.status_code
            if status == 200:
                return response.json()
            if status == 401 and not refreshed:
                refreshed = True
                self.auth.invalidate()
                continue
            if status == 429 and rate_limits < RATE_LIMIT_RETRIES:
                rate_limits += 1
                wait = _retry_after(response)
                if wait > MAX_RETRY_AFTER_S:
                    raise SpotifyApiError(
                        tr(
                            "Spotify asks to wait {minutes} minutes before the next request. "
                            "Try again later."
                        ).format(minutes=max(1, round(wait / 60))),
                        status,
                        _json(response),
                    )
                if self.on_wait:
                    self.on_wait(wait)
                self.sleep(wait)
                continue
            if status >= 500 and server_errors < SERVER_ERROR_RETRIES:
                server_errors += 1
                self.sleep(2**server_errors)
                continue
            body = _json(response)
            raise SpotifyApiError(_status_message(status, body), status, body)

    def pages(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """yields every page object, following next until it is null."""
        page = self.get(path, params)
        while True:
            yield page
            next_url = page.get("next")
            if not next_url:
                return
            page = self.get(next_url)

    def items(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        for page in self.pages(path, params):
            yield from page.get("items") or []


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _retry_after(response: httpx.Response) -> float:
    try:
        return max(1.0, float(response.headers.get("Retry-After", "5")))
    except ValueError:
        return 5.0


def _status_message(status: int, body: Any) -> str:
    detail = _error_message(body) or "no details"
    if status == 401:
        return tr("Spotify did not accept the login. Log in to Spotify again.")
    if status == 403:
        return tr(
            "Spotify refused access ({detail}). Check that your Spotify account is added to "
            "your app in the Spotify Developer Dashboard and that it has Premium."
        ).format(detail=detail)
    if status == 404:
        return tr("Spotify could not find this item ({detail}).").format(detail=detail)
    if status == 429:
        return tr("Spotify is limiting requests right now. Try again in a few minutes.")
    return tr("Spotify returned an error (HTTP {status}, {detail}). Try again later.").format(
        status=status, detail=detail
    )
