"""one download at a time per user: one sockseek process, one soulseek login (PLAN.md section 7).

the lock is held on an open file, so windows releases it when the process dies. the pid in the
file is only a note for people, nothing reads it back: a file left by a closed or killed
neonseek holds no lock, also when its pid belongs to another process by now (D-262).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from neonseek.errors import NeonSeekError
from neonseek.i18n import tr


class AlreadyRunningError(NeonSeekError):
    pass


class RunLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # noqa: SIM115 - held open until release
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise AlreadyRunningError(
                tr(
                    "Another NeonSeek download or library move is running. Wait until it ends or "
                    "cancel it, then start again."
                )
            ) from None
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        self._file = handle

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self._file.close()
        self._file = None

    def __enter__(self) -> RunLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
