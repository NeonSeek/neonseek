"""common interface of the postprocessing steps (PLAN.md section 8).

a step takes the downloaded file and returns it, maybe moved or with more facts. the runner
only knows this interface, so later steps (rename, convert) join the same list.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from neonseek.config import Settings
from neonseek.covers import CoverCache
from neonseek.db.repo import Repo
from neonseek.errors import NeonSeekError

# downloads.format
FLAC = "flac"
WAV = "wav"
AIFF = "aiff"
EXTENSIONS = {FLAC: ".flac", WAV: ".wav", AIFF: ".aiff"}


class StepError(NeonSeekError):
    """the step could not do its job. the message ends up in downloads.last_error."""


@dataclass
class TrackFile:
    track_id: int
    path: Path
    # filled by verify
    format: str | None = None
    duration_ms: int | None = None
    sample_rate: int | None = None
    bit_depth: int | None = None
    length_verified: bool | None = None


@dataclass
class StepContext:
    repo: Repo
    settings: Settings
    library_dir: Path
    covers: CoverCache
    # step name, track id, message: problems of steps that do not stop the track
    warn: Callable[[str, int, str], None]
    # remembers the target path before a move, so an interrupted move is ours next time
    claim_path: Callable[[int, Path], None]


class Step(Protocol):
    name: str
    # a fatal step that fails makes the track failed, any other step only logs
    fatal: bool

    def process(self, track_file: TrackFile, ctx: StepContext) -> TrackFile: ...
