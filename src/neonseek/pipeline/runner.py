"""one download run: pass 1 (flac), pass 2 (wav, aiff), postprocessing and playlists.

follows PLAN.md sections 7 and 8. the database is the only state, so any interruption leaves
something the next run can continue from.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from neonseek.config import LOSSLESS, Settings
from neonseek.covers import CoverCache
from neonseek.db import repo as db
from neonseek.engine import base as engine
from neonseek.engine.base import DownloadEngine, EngineItem, RunResult
from neonseek.engine.sockseek import FALLBACK_FORMATS, FLAC_FORMATS
from neonseek.i18n import tr
from neonseek.pipeline import playlists, policy, search_budget
from neonseek.pipeline.postprocess.base import Step, StepContext, StepError, TrackFile
from neonseek.pipeline.postprocess.cover import EmbedCover
from neonseek.pipeline.postprocess.place import Place
from neonseek.pipeline.postprocess.tag import WriteTags
from neonseek.pipeline.postprocess.verify import Verify

STAGING_DIR = ".staging"

# how a run ended
COMPLETED = "completed"
CANCELLED = "cancelled"
ERROR = "error"


def default_steps() -> list[Step]:
    return [Verify(), EmbedCover(), WriteTags(), Place()]


# events of the runner, next to the engine events it passes on


@dataclass(frozen=True)
class PassStarted:
    number: int
    formats: tuple[str, ...]
    track_ids: tuple[int, ...]


@dataclass(frozen=True)
class PassFinished:
    number: int
    result: RunResult


@dataclass(frozen=True)
class SearchLimitWait:
    """waiting before a pass, because earlier searches used the soulseek search limit."""

    seconds: float


@dataclass(frozen=True)
class TrackFinished:
    track_id: int
    label: str
    status: str  # done | not_found | failed
    detail: str | None = None
    track_file: TrackFile | None = None


@dataclass(frozen=True)
class StepWarning:
    track_id: int
    label: str
    step: str
    message: str


# run id, pass number, formats, lowered --searches-per-time or none
EngineFactory = Callable[[int, int, tuple[str, ...], int | None], DownloadEngine]


@dataclass
class RunOutcome:
    run_id: int
    ended: str
    message: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    passes: list[dict] = field(default_factory=list)
    playlists: list[Path] = field(default_factory=list)


def track_label(row) -> str:
    artists = json.loads(row["artists_json"]) if row["artists_json"] else []
    return f"{artists[0]} - {row['title']}" if artists else row["title"]


# apple music names albums "Name - EP" and "Name - Single", folders on soulseek do not (D-230)
_RELEASE_SUFFIX = re.compile(r"\s+-\s+(?:EP|Single)\s*$", re.IGNORECASE)


def search_album(album: str | None) -> str | None:
    """the album as sockseek should look for it. the stored album stays as the source gave it."""
    if not album:
        return album
    return _RELEASE_SUFFIX.sub("", album).strip() or album


def engine_item(row) -> EngineItem:
    artists = json.loads(row["artists_json"]) if row["artists_json"] else []
    length = round(row["duration_ms"] / 1000) if row["duration_ms"] else None
    return EngineItem(
        row["id"], artists[0] if artists else "", row["title"], search_album(row["album"]), length
    )


def clear_staging(library_dir: Path) -> None:
    # staging is ours and never part of the library (D-002)
    shutil.rmtree(library_dir / STAGING_DIR, ignore_errors=True)


class Runner:
    def __init__(
        self,
        *,
        repo: db.Repo,
        settings: Settings,
        library_dir: Path,
        covers: CoverCache,
        logs_dir: Path,
        engine_factory: EngineFactory,
        collection_ids: list[int],
        track_ids: list[int],
        on_event: Callable[[object], None],
        steps: list[Step] | None = None,
        search_window_s: float = search_budget.RENEW_S,
    ) -> None:
        self.repo = repo
        self.settings = settings
        self.library_dir = library_dir
        self.logs_dir = logs_dir
        self.engine_factory = engine_factory
        self.collection_ids = collection_ids
        self.track_ids = track_ids
        self.on_event = on_event
        self.steps = steps if steps is not None else default_steps()
        self.search_window_s = search_window_s
        self._cancel = threading.Event()
        self._engine: DownloadEngine | None = None
        self._labels: dict[int, str] = {}
        self._downloading: set[int] = set()
        self._log = None
        self.step_ctx = StepContext(
            repo=repo,
            settings=settings,
            library_dir=library_dir,
            covers=covers,
            warn=self._warn,
            claim_path=lambda track_id, path: repo.update_download(
                track_id, {"file_path": str(path)}
            ),
        )

    @property
    def staging(self) -> Path:
        return self.library_dir / STAGING_DIR

    def cancel(self) -> None:
        self._cancel.set()
        if self._engine is not None:
            self._engine.cancel()

    # logging and events

    def _write_log(self, text: str) -> None:
        if self._log is not None:
            stamp = datetime.now().strftime("%H:%M:%S")
            self._log.write(f"{stamp} {text}\n")
            self._log.flush()

    def _emit(self, event: object) -> None:
        self.on_event(event)

    def _warn(self, step: str, track_id: int, message: str) -> None:
        label = self._labels.get(track_id, str(track_id))
        self._write_log(f"[{step}] {label}: {message}")
        self._emit(StepWarning(track_id, label, step, message))

    # the run

    def run(self) -> RunOutcome:
        run_id = self.repo.start_run()
        outcome = RunOutcome(run_id, COMPLETED)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.logs_dir / f"run-{run_id}.log"
        with log_path.open("a", encoding="utf-8") as self._log:
            try:
                self._run(run_id, outcome)
            except KeyboardInterrupt:
                outcome.ended = CANCELLED
            finally:
                self.repo.reset_in_progress(self.track_ids)
                with contextlib.suppress(Exception):
                    outcome.playlists = [
                        playlists.write_playlist(self.repo, cid, self.library_dir)
                        for cid in self.collection_ids
                    ]
                clear_staging(self.library_dir)
                outcome.counts = self.counts()
                self._write_log(f"run ended: {outcome.ended} {outcome.message or ''}".rstrip())
                self.repo.finish_run(
                    run_id,
                    {
                        "collections": self.collection_ids,
                        "tracks": len(self.track_ids),
                        "on_missing_flac": self.settings.on_missing_flac,
                        "ended": outcome.ended,
                        "message": outcome.message,
                        "passes": outcome.passes,
                        "counts": outcome.counts,
                    },
                )
        self._log = None
        return outcome

    def _run(self, run_id: int, outcome: RunOutcome) -> None:
        clear_staging(self.library_dir)
        states = self.repo.track_states(self.track_ids)
        self._labels = {tid: track_label(row) for tid, row in states.items()}
        s = self.settings

        first = [
            tid
            for tid in self.track_ids
            if tid in states
            and policy.wants_pass1(states[tid]["status"], states[tid]["stage"],
                                   states[tid]["attempts"], s)
        ]  # fmt: skip
        if first and not self._pass(run_id, 1, FLAC_FORMATS, first, states, outcome):
            return
        if s.on_missing_flac != LOSSLESS or self._cancel.is_set():
            if self._cancel.is_set():
                outcome.ended = CANCELLED
            return

        # move what waits for the fallback pass, then take everything open in that stage
        states = self.repo.track_states(self.track_ids)
        waiting = [
            tid
            for tid in self.track_ids
            if policy.waiting_for_fallback(states[tid]["status"], states[tid]["stage"],
                                           states[tid]["attempts"], s)
        ]  # fmt: skip
        self.repo.move_to_fallback(waiting)
        states = self.repo.track_states(self.track_ids)
        second = [
            tid
            for tid in self.track_ids
            if policy.in_fallback_pass(states[tid]["status"], states[tid]["stage"],
                                       states[tid]["attempts"], s)
        ]  # fmt: skip
        if second:
            self._pass(run_id, 2, FALLBACK_FORMATS, second, states, outcome)

    def _wait_for_search_limit(self, tracks: int) -> tuple[float, int | None]:
        plan = search_budget.plan_start(
            self.repo.searches_since(time.time() - self.search_window_s),
            time.time(),
            tracks,
            window_s=self.search_window_s,
        )
        if plan.wait_s > 0:
            self._write_log(f"waiting {plan.wait_s:.0f} s for the soulseek search limit")
            self._emit(SearchLimitWait(plan.wait_s))
            end = time.monotonic() + plan.wait_s
            while not self._cancel.is_set() and time.monotonic() < end:
                time.sleep(min(0.5, max(0.0, end - time.monotonic())))
        return plan.wait_s, plan.searches_per_time

    def _pass(
        self,
        run_id: int,
        number: int,
        formats: tuple[str, ...],
        track_ids: list[int],
        states: dict,
        outcome: RunOutcome,
    ) -> bool:
        """runs one sockseek pass. false when the run has to stop."""
        waited, per_time = self._wait_for_search_limit(len(track_ids))
        if self._cancel.is_set():
            outcome.ended = CANCELLED
            return False
        items = [engine_item(states[tid]) for tid in track_ids]
        self._engine = self.engine_factory(run_id, number, formats, per_time)
        self._write_log(f"pass {number} ({','.join(formats)}): {len(items)} tracks")
        self._emit(PassStarted(number, formats, tuple(track_ids)))
        self._downloading = set()
        try:
            result = self._engine.run(items, self._on_engine_event)
        finally:
            self._engine = None
            self.repo.reset_in_progress(track_ids)
        outcome.passes.append(
            {
                "pass": number,
                "formats": list(formats),
                "tracks": len(track_ids),
                "ended": result.ended,
                "exit_code": result.exit_code,
                "searches": result.searches,
                "longest_silence_s": result.longest_silence_s,
                "silence_after": result.silence_after,
                "waited_for_search_limit_s": round(waited, 1),
                "searches_per_time": per_time,
            }
        )
        self._write_log(
            f"pass {number} ended: {result.ended}, exit code {result.exit_code}, "
            f"longest silence {result.longest_silence_s} s"
        )
        self._emit(PassFinished(number, result))
        if result.ended == engine.CANCELLED or self._cancel.is_set():
            outcome.ended = CANCELLED
            return False
        if not result.ok:
            outcome.ended = ERROR
            outcome.message = result.message
            return False
        return True

    # engine events

    def _on_engine_event(self, event) -> None:
        try:
            self._handle(event)
        except Exception as exc:  # a bug here must not kill a running sockseek
            track_id = getattr(event, "track_id", None)
            self._write_log(f"[runner] {type(event).__name__}: {exc!r}")
            if isinstance(event, engine.ItemDone):
                with contextlib.suppress(Exception):
                    self.repo.add_failure(event.track_id, tr("Unexpected error: {e}").format(e=exc))
            if track_id is not None:
                self._emit(StepWarning(track_id, self._labels.get(track_id, ""), "runner",
                                       str(exc)))  # fmt: skip

    def _handle(self, event) -> None:
        repo = self.repo
        if isinstance(event, engine.SearchStarted):
            repo.add_search(time.time())
        elif isinstance(event, engine.ItemSearching):
            repo.update_download(event.track_id, {"status": db.SEARCHING})
        elif isinstance(event, engine.ItemDownloading):
            if event.track_id not in self._downloading:
                self._downloading.add(event.track_id)
                repo.update_download(
                    event.track_id,
                    {
                        "status": db.DOWNLOADING,
                        "source_user": event.user,
                        "source_path": event.remote_path,
                    },
                )
        elif isinstance(event, engine.ItemDone):
            self._finish_download(event)
            return
        elif isinstance(event, engine.ItemNotFound):
            repo.update_download(
                event.track_id, {"status": db.NOT_FOUND, "last_error": event.reason}
            )
            self._emit(TrackFinished(event.track_id, self._label(event), db.NOT_FOUND,
                                     event.reason))  # fmt: skip
            return
        elif isinstance(event, engine.ItemFailed):
            repo.add_failure(event.track_id, event.reason)
            self._emit(TrackFinished(event.track_id, self._label(event), db.FAILED,
                                     event.reason))  # fmt: skip
            return
        elif isinstance(event, engine.LogLine):
            self._write_log(f"[{event.stream}] {event.text}")
        self._emit(event)

    def _label(self, event) -> str:
        return self._labels.get(event.track_id, str(event.track_id))

    def _finish_download(self, event: engine.ItemDone) -> None:
        track_id = event.track_id
        label = self._label(event)
        self.repo.update_download(
            track_id,
            {
                "status": db.DOWNLOADING,
                "source_user": event.user,
                "source_path": event.remote_path,
            },
        )
        track_file = TrackFile(track_id, Path(event.local_path))
        for step in self.steps:
            try:
                track_file = step.process(track_file, self.step_ctx)
            except Exception as exc:
                message = exc.message if isinstance(exc, StepError) else repr(exc)
                if not step.fatal:
                    # cover and tag problems never undo a download
                    self._warn(step.name, track_id, message)
                    continue
                self.repo.add_failure(track_id, message)
                with contextlib.suppress(OSError):
                    if self.staging in track_file.path.parents:
                        track_file.path.unlink()
                self._write_log(f"[{step.name}] {label}: {message}")
                self._emit(TrackFinished(track_id, label, db.FAILED, message))
                return
        self.repo.update_download(
            track_id,
            {
                "status": db.DONE,
                "file_path": str(track_file.path),
                "format": track_file.format,
                "sample_rate": track_file.sample_rate,
                "bit_depth": track_file.bit_depth,
                "duration_ms": track_file.duration_ms,
                "length_verified": int(bool(track_file.length_verified)),
                "last_error": None,
            },
        )
        self._write_log(f"[done] {label}: {track_file.path}")
        self._emit(TrackFinished(track_id, label, db.DONE, None, track_file))

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.repo.track_states(self.track_ids).values():
            key = row["status"]
            if policy.waiting_for_fallback(row["status"], row["stage"], row["attempts"],
                                           self.settings):  # fmt: skip
                key = "waiting_fallback"
            counts[key] = counts.get(key, 0) + 1
        return counts
