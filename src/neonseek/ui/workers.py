"""qthread wrappers on core. long work never runs in the ui thread (PLAN.md section 9).

each worker opens its own app context, because a sqlite connection stays in its thread.
"""

from __future__ import annotations

import sys
import threading
import traceback
from collections.abc import Callable, Iterable

from PySide6.QtCore import QThread, Signal

from neonseek import actions
from neonseek.errors import NeonSeekError
from neonseek.i18n import tr


def unexpected_message(exc: BaseException) -> str:
    return tr(
        "Unexpected error: {error}. Your data is safe. Try again, and if it keeps happening, "
        "copy the diagnostics from About."
    ).format(error=f"{type(exc).__name__}: {exc}")


class Worker(QThread):
    """runs fn(ctx, worker) once. fn may emit progress or event, and set cancel_hook."""

    progress = Signal(str)
    event = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)
    error = Signal(object)  # the NeonSeekError itself, for callers that need its type

    def __init__(self, ctx: actions.AppContext, fn: Callable, parent=None) -> None:
        super().__init__(parent)
        self._base = ctx
        self._fn = fn
        self.cancel_hook: Callable[[], None] | None = None
        self.trace = ""

    def cancel(self) -> None:
        if self.cancel_hook is not None:
            self.cancel_hook()

    def run(self) -> None:
        ctx = self._base.fork()
        try:
            result = self._fn(ctx, self)
        except NeonSeekError as exc:
            self.error.emit(exc)
            self.failed.emit(exc.message)
        except Exception as exc:
            self.trace = traceback.format_exc()
            sys.stderr.write(self.trace)
            self.failed.emit(unexpected_message(exc))
        else:
            self.succeeded.emit(result)
        finally:
            ctx.close()


class DownloadWorker(QThread):
    """one download: summary first, then the run after the user said start (PLAN.md 8).

    the run lock is held from the summary to the end, like in the cli."""

    planned = Signal(object)  # DownloadPlan, waits for decide()
    nothing = Signal(object, list)  # DownloadPlan and the playlists written
    declined = Signal()
    event = Signal(object)
    ran = Signal(object)  # RunOutcome
    failed = Signal(str)

    def __init__(
        self,
        ctx: actions.AppContext,
        collection_ids: Iterable[int],
        parent=None,
        track_ids: Iterable[int] | None = None,
    ):
        super().__init__(parent)
        self._base = ctx
        self.collection_ids = list(collection_ids)
        # only these tracks, whatever their collections (D-125)
        self.track_ids = None if track_ids is None else list(track_ids)
        self._decision = threading.Event()
        self._go = False
        self._job: actions.DownloadJob | None = None
        self._cancelled = False
        self.trace = ""

    def decide(self, go: bool) -> None:
        self._go = go
        self._decision.set()

    def cancel(self) -> None:
        self._cancelled = True
        if self._job is not None:
            self._job.cancel()
        self.decide(False)

    def run(self) -> None:
        ctx = self._base.fork()
        try:
            with actions.DownloadJob(ctx, self.collection_ids, track_ids=self.track_ids) as job:
                if job.plan.nothing_to_do:
                    paths = actions.write_playlists(ctx, [cid for cid, _ in job.plan.collections])
                    self.nothing.emit(job.plan, paths)
                    return
                self.planned.emit(job.plan)
                self._decision.wait()
                if not self._go or self._cancelled:
                    self.declined.emit()
                    return
                self._job = job
                outcome = job.run(self.event.emit)
                self._job = None
                self.ran.emit(outcome)
        except NeonSeekError as exc:
            self.failed.emit(exc.message)
        except Exception as exc:
            self.trace = traceback.format_exc()
            sys.stderr.write(self.trace)
            self.failed.emit(unexpected_message(exc))
        finally:
            ctx.close()
