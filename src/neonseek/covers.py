"""cover images from the source, cached in %APPDATA%\\NeonSeek\\covers as they came."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import httpx

from neonseek.errors import NeonSeekError
from neonseek.i18n import tr

TIMEOUT_S = 20.0
MAX_BYTES = 20 * 1024 * 1024
MIME = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


class CoverError(NeonSeekError):
    pass


def sniff_type(data: bytes) -> str | None:
    """file extension from the first bytes, none for anything that is not an image we know."""
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


class CoverCache:
    def __init__(self, directory: Path, http) -> None:
        self.directory = directory
        # an httpx.Client or a callable that returns one, opened only when needed
        self._http = http

    @staticmethod
    def key(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]

    def cached(self, url: str) -> Path | None:
        key = self.key(url)
        for ext in MIME:
            path = self.directory / f"{key}{ext}"
            if path.is_file():
                return path
        return None

    def get(self, url: str) -> Path:
        """the cached file, downloaded first when it is not there yet."""
        path = self.cached(url)
        if path is not None:
            return path
        http = self._http() if callable(self._http) else self._http
        try:
            response = http.get(url, timeout=TIMEOUT_S, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise CoverError(
                tr("Could not download the cover ({error}).").format(error=type(exc).__name__)
            ) from exc
        if response.status_code != 200:
            raise CoverError(
                tr("Could not download the cover (HTTP {code}).").format(code=response.status_code)
            )
        data = response.content
        if len(data) > MAX_BYTES:
            raise CoverError(tr("The cover image is too large."))
        # the bytes decide the format, a wrong content-type header does not matter
        ext = sniff_type(data)
        if ext is None:
            raise CoverError(tr("The cover is not a JPEG, PNG or WebP image."))
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{self.key(url)}{ext}"
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return path
