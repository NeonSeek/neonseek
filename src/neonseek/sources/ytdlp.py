"""youtube and soundcloud track lists through yt-dlp as a library (D-231). only the list of
entries is read, never audio or video: the audio comes from soulseek.

yt-dlp is pinned in pyproject.toml. its cache lives in the neonseek data folder, it reads no
config file and no cookies.
"""

from __future__ import annotations

import re
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from neonseek.errors import NeonSeekError
from neonseek.i18n import tr
from neonseek.sources.links import SOUNDCLOUD, YOUTUBE, service_name

# soundcloud api answers at most this many tracks per ids= request
SOUNDCLOUD_IDS_PER_REQUEST = 50
# a url result is followed this many times at most (short link, video in a playlist)
MAX_HOPS = 3
_ARTWORK_SIZE = re.compile(r"-(?:large|t\d+x\d+|crop|original)\.(?:jpg|png)$")


class WebSourceError(NeonSeekError):
    pass


class BatchUnavailable(Exception):
    """the batched soundcloud metadata no longer works with this yt-dlp, read one by one."""


class Extractor(Protocol):
    def extract(self, url: str) -> dict[str, Any]:
        """a playlist with flat entries, or one video or track."""

    def soundcloud_tracks(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        """metadata of soundcloud tracks by id in few requests, may raise BatchUnavailable."""

    def resolve(self, url: str) -> dict[str, Any]:
        """one entry read on its own, the slow way."""


class _Log:
    """yt-dlp talks through this, nothing reaches the console of the window."""

    def __init__(self, sink: Callable[[str], None] | None) -> None:
        self.sink = sink

    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        if self.sink is not None:
            self.sink(message)

    def error(self, message: str) -> None:
        if self.sink is not None:
            self.sink(message)


class YtDlp:
    def __init__(self, cache_dir: Path | None, log: Callable[[str], None] | None = None) -> None:
        self.cache_dir = cache_dir
        self.log = _Log(log)

    def _options(self) -> dict[str, Any]:
        return {
            "quiet": True,
            "no_warnings": False,
            "logger": self.log,
            "skip_download": True,
            # entries of a playlist stay unresolved: title, channel and length are enough
            "extract_flat": "in_playlist",
            # a video without playable formats still has a title
            "ignore_no_formats_error": True,
            "cachedir": str(self.cache_dir) if self.cache_dir else False,
            "socket_timeout": 30,
            "retries": 2,
            "extractor_retries": 2,
            "noprogress": True,
            "color": {"stdout": "never", "stderr": "never"},
        }

    def _ydl(self):
        import yt_dlp

        return yt_dlp.YoutubeDL(self._options())

    def extract(self, url: str) -> dict[str, Any]:
        with self._ydl() as ydl:
            info = ydl.extract_info(url, download=False, process=False)
            for _ in range(MAX_HOPS):
                if not isinstance(info, dict) or info.get("_type") not in (
                    "url",
                    "url_transparent",
                ):
                    break
                info = ydl.extract_info(info["url"], download=False, process=False)
            if isinstance(info, dict) and info.get("_type") == "playlist":
                # entries can be a generator that reads the next pages on the way
                info = {**info, "entries": [e for e in info.get("entries") or [] if e]}
            return info

    def soundcloud_tracks(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        # yt-dlp's own soundcloud client (it finds the client_id) and its track mapping.
        # these are inner parts of yt-dlp: tests/test_web_sources.py runs them on saved
        # answers, so a yt-dlp update that changes them fails there first (D-232)
        found: dict[str, dict[str, Any]] = {}
        with self._ydl() as ydl:
            try:
                ie = ydl.get_info_extractor("Soundcloud")
                ie.initialize()
                base = ie._API_V2_BASE
                headers = ie._HEADERS
            except (AttributeError, TypeError, KeyError) as exc:
                raise BatchUnavailable(str(exc)) from exc
            for start in range(0, len(ids), SOUNDCLOUD_IDS_PER_REQUEST):
                batch = ids[start : start + SOUNDCLOUD_IDS_PER_REQUEST]
                try:
                    tracks = ie._call_api(
                        base + "tracks", None, "Downloading track metadata",
                        query={"ids": ",".join(batch)}, headers=headers,
                    )  # fmt: skip
                    if not isinstance(tracks, list):
                        raise BatchUnavailable("tracks answer is not a list")
                    for raw in tracks:
                        if not isinstance(raw, dict) or raw.get("id") is None:
                            continue
                        # without the artwork: yt-dlp would check every cover with its own
                        # request, the cover address is built here instead
                        user = {**(raw.get("user") or {}), "avatar_url": None}
                        bare = {**raw, "artwork_url": None, "user": user}
                        info = ie._extract_info_dict(bare, extract_flat=True)
                        info["cover_url"] = soundcloud_cover(raw.get("artwork_url"))
                        publisher = raw.get("publisher_metadata") or {}
                        info["isrc"] = publisher.get("isrc")
                        info["album"] = publisher.get("album_title") or publisher.get(
                            "release_title"
                        )
                        found[str(raw["id"])] = info
                except (AttributeError, TypeError, KeyError) as exc:
                    raise BatchUnavailable(str(exc)) from exc
        return found

    def resolve(self, url: str) -> dict[str, Any]:
        return self.extract(url)


def soundcloud_cover(artwork_url: Any) -> str | None:
    """the 500 px jpeg of a soundcloud artwork ("...-large.jpg" is 100 px)."""
    if not isinstance(artwork_url, str) or not artwork_url:
        return None
    return _ARTWORK_SIZE.sub("-t500x500.jpg", artwork_url, count=1)


def _root(exc: BaseException) -> BaseException:
    """the first cause of a yt-dlp error: DownloadError wraps ExtractorError wraps HTTPError."""
    for _ in range(6):
        inner = None
        info = getattr(exc, "exc_info", None)
        if info and len(info) > 1 and info[1] is not None:
            inner = info[1]
        elif getattr(exc, "cause", None) is not None:
            inner = exc.cause
        elif exc.__cause__ is not None:
            inner = exc.__cause__
        if inner is None or inner is exc or not isinstance(inner, BaseException):
            break
        exc = inner
    return exc


def _status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status", None) or getattr(exc, "status", None)
    return status if isinstance(status, int) else None


def problem(service: str, exc: BaseException, what: str) -> WebSourceError:
    """a yt-dlp failure in words a user can act on. what: playlist, set, video, track."""
    name = service_name(service)
    root = _root(exc)
    status = _status(root)
    text = str(exc).lower()
    network = isinstance(root, (socket.timeout, ConnectionError, OSError)) and status is None
    if not network:
        try:
            from yt_dlp.networking.exceptions import TransportError

            network = isinstance(root, TransportError) and status is None
        except ImportError:
            pass
    if network or "timed out" in text or "getaddrinfo" in text or "connection" in text:
        return WebSourceError(
            tr("No connection to {service}. Check the internet connection and try again.").format(
                service=name
            )
        )
    if status in (404, 410) or "does not exist" in text or "not found" in text:
        return WebSourceError(
            tr(
                "{service} could not find this {what}. Check that the link is complete and that "
                "the {what} is public."
            ).format(service=name, what=what)
        )
    if status == 429 or "too many requests" in text:
        return WebSourceError(
            tr(
                "{service} is limiting requests from this connection right now. Wait an hour "
                "and try again."
            ).format(service=name)
        )
    if "private" in text or status in (401, 403) or "sign in" in text or "members" in text:
        return WebSourceError(
            tr(
                "{service} does not share this {what}: it is private, blocked in your country or "
                "needs a login. Make it public or unlisted and try again."
            ).format(service=name, what=what)
        )
    return WebSourceError(
        tr(
            "{service} answered in a form NeonSeek cannot read ({error}). The site may have "
            "changed. Try again later or check for a newer version of NeonSeek."
        ).format(service=name, error=_short(str(root) or str(exc)))
    )


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.replace("ERROR:", "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def what_label(service: str, playlist: bool) -> str:
    if service == YOUTUBE:
        return tr("playlist") if playlist else tr("video")
    if service == SOUNDCLOUD:
        return tr("set") if playlist else tr("track")
    return tr("playlist") if playlist else tr("track")
