"""the app log in %APPDATA%\\NeonSeek\\logs\\neonseek.log. secrets never reach it.

the gui writes what its log panel shows, plus errors. download runs keep their own run logs.
"""

from __future__ import annotations

import contextlib
import os
import threading
from datetime import datetime
from pathlib import Path

from neonseek import config
from neonseek import secrets as secret_store
from neonseek.redact import Redactor

FILE_NAME = "neonseek.log"
MAX_BYTES = 1024 * 1024  # then the file moves to neonseek.1.log


def known_secrets() -> list[str]:
    """the soulseek password and the spotify refresh token, if saved. never raises."""
    found = []
    for name in (secret_store.SOULSEEK_PASSWORD, secret_store.SPOTIFY_REFRESH_TOKEN):
        with contextlib.suppress(Exception):
            value = secret_store.get_secret(name)
            if value:
                found.append(value)
    return found


class AppLog:
    def __init__(self, data_dir: Path) -> None:
        self.path = config.logs_dir(data_dir) / FILE_NAME
        self._lock = threading.Lock()
        self._redact = Redactor(known_secrets())

    def refresh_secrets(self) -> None:
        """after a login changed, so a new password is cut too."""
        self._redact = Redactor(known_secrets())

    def write(self, text: str, kind: str = "info") -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"{stamp} [{kind}] {line}" for line in self._redact(text).splitlines() or [""]]
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                    os.replace(self.path, self.path.with_name("neonseek.1.log"))
                with self.path.open("a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
            except OSError:
                pass  # a full disk or a locked file must not stop the app


def tail(path: Path, lines: int = 60) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]
