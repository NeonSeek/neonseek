"""cover thumbnails, loaded lazily off the ui thread (PLAN.md section 9).

the core cover cache downloads and stores the file as it came. here it is only scaled.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap

from neonseek.covers import CoverCache

THREADS = 4


class _Signals(QObject):
    done = Signal(str, int, QImage)  # url, size in pixels, image (null when it failed)


def crop_scale(image: QImage, px: int) -> QImage:
    """square thumbnail: scaled to cover the square, then cut in the middle."""
    scaled = image.scaled(
        px, px, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )  # fmt: skip
    x = max(0, (scaled.width() - px) // 2)
    y = max(0, (scaled.height() - px) // 2)
    return scaled.copy(x, y, px, px)


class _Task(QRunnable):
    def __init__(self, loader: CoverLoader, url: str, px: int) -> None:
        super().__init__()
        self.loader = loader
        self.url = url
        self.px = px

    def run(self) -> None:
        image = QImage()
        try:
            path = self.loader.fetch(self.url)
            loaded = QImage(str(path))
            if not loaded.isNull():
                image = crop_scale(loaded, self.px)
        except Exception as exc:  # a broken cover never breaks the window
            self.loader.log(f"cover {self.url}: {exc}")
        self.loader.signals.done.emit(self.url, self.px, image)


class CoverLoader(QObject):
    """thumbnails by url and size. ready(url) tells views to paint again."""

    ready = Signal(str)

    def __init__(
        self,
        covers_dir: Path,
        http_factory: Callable[[], object],
        log: Callable[[str], None] = lambda _: None,
    ) -> None:
        super().__init__()
        self.covers_dir = covers_dir
        self.http_factory = http_factory
        self.log = log
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(THREADS)
        self.signals = _Signals()
        self.signals.done.connect(self._done)
        self._pixmaps: dict[tuple[str, int], QPixmap] = {}
        self._pending: set[tuple[str, int]] = set()
        # failed in this session only, the next start tries again
        self._failed: set[tuple[str, int]] = set()
        self._local = threading.local()
        self._clients: list = []
        self._url_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # worker side

    def _http(self):
        client = getattr(self._local, "client", None)
        if client is None:
            client = self.http_factory()
            self._local.client = client
            with self._locks_guard:
                self._clients.append(client)
        return client

    def _url_lock(self, url: str) -> threading.Lock:
        with self._locks_guard:
            return self._url_locks.setdefault(url, threading.Lock())

    def fetch(self, url: str) -> Path:
        # one download per url, the second size waits and finds the file in the cache
        with self._url_lock(url):
            return CoverCache(self.covers_dir, self._http).get(url)

    # ui side

    def pixmap(self, url: str | None, size: int, dpr: float = 1.0) -> QPixmap | None:
        """the thumbnail if it is ready. otherwise starts loading it and returns none."""
        if not url:
            return None
        px = max(1, round(size * dpr))
        key = (url, px)
        cached = self._pixmaps.get(key)
        if cached is not None:
            return cached
        if key not in self._pending and key not in self._failed:
            self._pending.add(key)
            self.pool.start(_Task(self, url, px))
        return None

    def _done(self, url: str, px: int, image: QImage) -> None:
        key = (url, px)
        self._pending.discard(key)
        if image.isNull():
            self._failed.add(key)
            return
        pixmap = QPixmap.fromImage(image)
        self._pixmaps[key] = pixmap
        self.ready.emit(url)

    def shutdown(self) -> None:
        self.pool.clear()
        self.pool.waitForDone(3000)
        for client in self._clients:
            with contextlib.suppress(Exception):
                client.close()
        self._clients.clear()
