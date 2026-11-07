"""download engine interface. the runner only talks to this, so the engine can be swapped."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from neonseek.errors import NeonSeekError


@dataclass(frozen=True)
class EngineItem:
    """one track to look for. artist is the first artist, length is in seconds."""

    track_id: int
    artist: str
    title: str
    album: str | None = None
    length_s: int | None = None


# events, every item event carries our track id


@dataclass(frozen=True)
class SearchStarted:
    """one search went to soulseek. counted against the search limit across processes."""


@dataclass(frozen=True)
class ItemSearching:
    track_id: int


@dataclass(frozen=True)
class ItemDownloading:
    track_id: int
    percent: int | None
    user: str | None = None
    remote_path: str | None = None


@dataclass(frozen=True)
class ItemDone:
    track_id: int
    local_path: Path
    user: str | None = None
    remote_path: str | None = None


@dataclass(frozen=True)
class ItemNotFound:
    track_id: int
    reason: str


@dataclass(frozen=True)
class ItemFailed:
    track_id: int
    reason: str


@dataclass(frozen=True)
class RateLimitWait:
    """soulseek search limit reached, the engine waits. ends with the next ItemSearching."""

    track_id: int | None


@dataclass(frozen=True)
class LogLine:
    stream: str  # stdout | stderr
    text: str


# how a run ended
COMPLETED = "completed"
CANCELLED = "cancelled"
START_TIMEOUT = "start_timeout"
IDLE_TIMEOUT = "idle_timeout"
LOGIN_FAILED = "login_failed"
CRASHED = "crashed"


@dataclass
class RunResult:
    ended: str
    exit_code: int | None = None
    # user facing text for every ending other than completed and cancelled
    message: str | None = None
    # items without a final result, they go back to pending
    unresolved: list[int] = field(default_factory=list)
    # longest time without any output line, and the line before it (password cut)
    longest_silence_s: float = 0.0
    silence_after: str = ""
    searches: int = 0

    @property
    def ok(self) -> bool:
        return self.ended == COMPLETED


@dataclass(frozen=True)
class RunFinished:
    result: RunResult


EngineEvent = (
    SearchStarted
    | ItemSearching
    | ItemDownloading
    | ItemDone
    | ItemNotFound
    | ItemFailed
    | RateLimitWait
    | LogLine
    | RunFinished
)


class EngineError(NeonSeekError):
    """the engine could not start at all (missing binary, bad setup)."""


class DownloadEngine(Protocol):
    def run(
        self, items: list[EngineItem], on_event: Callable[[EngineEvent], None]
    ) -> RunResult: ...

    def cancel(self) -> None: ...
