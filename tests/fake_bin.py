"""a recycle bin for tests: files go into a folder of the test, never into the windows bin."""

from __future__ import annotations

import shutil
from pathlib import Path

from neonseek import recycle
from neonseek.library import is_inside, norm


class FakeRecycleBin:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.items: list[Path] = []  # original paths, in the order they came
        self._no_bin: list[tuple[Path, str]] = []
        self._locked: set[str] = set()

    def without_bin(self, folder: Path, why: str = "removable drive, like a USB stick") -> None:
        """files below this folder behave like a usb stick: no recycle bin."""
        self._no_bin.append((folder, why))

    def lock(self, path: Path) -> None:
        """this file is in use by another program and cannot go."""
        self._locked.add(norm(path))

    def problem(self, path: Path) -> str | None:
        for folder, why in self._no_bin:
            if is_inside(path, folder):
                return recycle.no_bin_message(path, why)
        return None

    def recycle(self, path: Path) -> None:
        problem = self.problem(path)
        if problem:
            raise recycle.RecycleError(problem)
        if norm(path) in self._locked:
            raise recycle.RecycleError(f"{path} is used by another program")
        self.folder.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(self.folder / f"{len(self.items)}-{path.name}"))
        self.items.append(Path(path))

    def names(self) -> list[str]:
        return [p.name for p in self.items]
