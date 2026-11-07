"""sockseek adapter: one subprocess per pass, events read from --progress-json.

the call, the row mapping and the timeouts follow PLAN.md section 7. checked on sockseek 3.0.5,
see docs/sockseek-notes.md.
"""

from __future__ import annotations

import contextlib
import csv
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from neonseek.engine import base
from neonseek.engine.base import EngineError, EngineItem, RunResult
from neonseek.i18n import tr
from neonseek.redact import Redactor
from neonseek.secrets import SoulseekLogin

# dev and test override of the binary, never set in a normal install
SOCKSEEK_ENV = "NEONSEEK_SOCKSEEK"
CREATE_NO_WINDOW = 0x08000000

FLAC_FORMATS = ("flac",)
# --format compares the file extension only, so aiff needs both spellings
FALLBACK_FORMATS = ("wav", "aiff", "aif")
# in pass 2 aiff ranks before wav: rekordbox shows its cover and all tags (D-082)
FALLBACK_PREFERRED = ("aiff", "aif")
LENGTH_TOLERANCE_S = 3

# longer than sockseek's --connect-timeout (20 s), see PLAN.md section 7
START_TIMEOUT_S = 60.0
LOGIN_CHECK_TIMEOUT_S = 30.0
# killing sockseek and reading its last lines after the limit, at most this long
LOGIN_CLEANUP_S = 3.0

RUN_CSV_HEADER = ("Artist", "Title", "Album", "Length")
# {row} counts the header, so the first data row is 2
FIRST_ROW = 2
STAGING_SUBDIR = ".sockseek-staging"

NOT_FOUND_REASONS = {"NoSearchResults", "NoMatchingResults"}
RATE_LIMIT_RE = re.compile(r"SongJob: rate limited: (?P<what>.+?)\s*$")
LOGIN_FAILED_RE = re.compile(r"Soulseek login failed: (?P<why>.+?)\s*$")
REJECTED_RE = re.compile(r"rejected login attempt: (?P<code>[A-Z]+)")
KICKED_TEXT = "kicked this client"
# lines that mean the login did not go through, used by the login check
LOGIN_TROUBLE = (
    "login failed",
    "failed to ensure soulseek connection",
    "connection lost",
    "reconnect",
    KICKED_TEXT,
)


def find_binary() -> Path:
    """sockseek.exe: next to the installed app, or in vendor/ of a source checkout."""
    override = os.environ.get(SOCKSEEK_ENV)
    candidates = [Path(override)] if override else []
    if getattr(sys, "frozen", False):
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates += [
            bundle / "vendor" / "sockseek" / "sockseek.exe",
            Path(sys.executable).parent / "vendor" / "sockseek" / "sockseek.exe",
        ]
    else:
        root = Path(__file__).resolve().parents[3]
        candidates.append(root / "vendor" / "sockseek" / "sockseek.exe")
    for path in candidates:
        if path.is_file():
            return path
    raise EngineError(
        tr(
            "Sockseek, the program NeonSeek uses to download from Soulseek, is missing. "
            "Install NeonSeek again."
        )
    )


VERSION_TIMEOUT_S = 15.0


def binary_version(command: list[str] | None = None) -> str:
    """what sockseek --version prints. no login, no network."""
    command = command or [str(find_binary())]
    try:
        proc = start_process([*command, "--version"])
    except OSError as exc:
        raise EngineError(
            tr("Could not start Sockseek ({error}). Install NeonSeek again.").format(
                error=type(exc).__name__
            )
        ) from exc
    try:
        out, _ = proc.communicate(timeout=VERSION_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise EngineError(
            tr("Sockseek did not answer. Restart Windows, then install NeonSeek again.")
        ) from None
    text = out.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0 or not text:
        problem = tr("Sockseek did not start properly (exit code {code}). Install NeonSeek again.")
        raise EngineError(problem.format(code=proc.returncode))
    return text.splitlines()[0].strip()


def source_note() -> str:
    """the license and source of the bundled sockseek (agpl), written by fetch_sockseek.ps1."""
    try:
        path = find_binary().parent / "SOURCE.txt"
        return path.read_text(encoding="utf-8-sig").strip()
    except (EngineError, OSError):
        return "Sockseek (AGPL-3.0): https://github.com/fiso64/sockseek"


def _creation_flags() -> int:
    # no console window pops up for the subprocess
    return CREATE_NO_WINDOW if sys.platform == "win32" else 0


def start_process(command: list[str]) -> subprocess.Popen:
    """the only place a sockseek process starts. tests guard it so nothing reaches soulseek."""
    return subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        creationflags=_creation_flags(),
    )


def _one_line(text: str | None) -> str:
    # {row} counts csv records, so a field must never hold a line break
    return " ".join((text or "").split())


def write_run_csv(path: Path, items: Iterable[EngineItem]) -> None:
    """our work file for sockseek, not a user csv. no uri/id/url column, no empty title."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(RUN_CSV_HEADER)
        for item in items:
            title = _one_line(item.title)
            if not title:
                # a row with only an album makes sockseek download the whole album
                raise ValueError(f"track {item.track_id} has no title")
            length = str(item.length_s) if item.length_s else ""
            writer.writerow([_one_line(item.artist), title, _one_line(item.album), length])


def read_run_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def login_failure_message(why: str) -> str:
    """user facing text for a 'Soulseek login failed: ...' line."""
    code = REJECTED_RE.search(why)
    code = code.group("code") if code else ""
    if code == "INVALIDPASS":
        return tr(
            "Soulseek did not accept the saved login: the password is wrong, or the username "
            "belongs to someone else. Log in to Soulseek again in Settings."
        )
    if code == "INVALIDUSERNAME":
        return tr(
            "Soulseek does not accept this username. Log in to Soulseek again in Settings "
            "with another name."
        )
    if code == "SVRFULL":
        return tr("The Soulseek server is full right now. Try again later.")
    if code == "SVRPRIVATE":
        return tr("The Soulseek server does not accept new accounts right now. Try again later.")
    if code == "INVALIDVERSION":
        return tr(
            "The Soulseek server does not accept this version of Sockseek. Install the newest "
            "NeonSeek."
        )
    if KICKED_TEXT in why:
        return kicked_message()
    return tr(
        "Could not log in to Soulseek ({reason}). Check your internet connection and try again."
    ).format(reason=why)


def kicked_message() -> str:
    return tr(
        "Your Soulseek account logged in somewhere else, for example in another Soulseek "
        "program. Close it and start again."
    )


def start_timeout_message(seconds: float) -> str:
    return tr(
        "Could not connect to Soulseek within {seconds} seconds. Check your internet "
        "connection and that no security program blocks NeonSeek. If it keeps happening, the "
        "Soulseek account may be banned: the server does not answer banned accounts at all. "
        "Then log in with another account in Settings."
    ).format(seconds=int(seconds))


def idle_timeout_message(minutes: float) -> str:
    return tr(
        "Sockseek gave no sign of life for {minutes} minutes, so the download was stopped. "
        "Finished tracks are kept. Start the download again. If this keeps happening, raise "
        "the idle limit in Settings."
    ).format(minutes=round(minutes))


@dataclass
class SockseekSettings:
    login: SoulseekLogin
    output_dir: Path
    run_csv: Path
    log_file: Path
    formats: tuple[str, ...]
    start_timeout_s: float = START_TIMEOUT_S
    idle_timeout_s: float = 600.0
    # tests put a fake process here, the default is the bundled sockseek.exe
    command: list[str] | None = None
    # tests add --mock-files-dir and friends here
    extra_args: tuple[str, ...] = ()
    # --pref-format only ranks, it never filters anything out
    pref_formats: tuple[str, ...] = ()


@dataclass
class _Download:
    index: int
    size: int | None
    user: str | None
    remote_path: str | None
    job_id: str | None = None


@dataclass
class _Tracker:
    """maps sockseek events back to run.csv positions (PLAN.md section 7)."""

    items: list[EngineItem]
    keys: list[tuple[str, str] | None] = field(default_factory=list)
    resolved: set[int] = field(default_factory=set)
    searched: set[int] = field(default_factory=set)
    downloads: dict[int, _Download] = field(default_factory=dict)
    jobs: dict[str, int] = field(default_factory=dict)
    # failures that could belong to several identical items, settled at the end
    deferred: dict[tuple[str, str], list] = field(default_factory=dict)
    searches: int = 0

    def __post_init__(self) -> None:
        # until track_list arrives, guess the keys from our own items
        self.keys = [(_one_line(i.artist), _one_line(i.title)) for i in self.items]

    def track_list(self, data: dict) -> None:
        for entry in data.get("tracks") or []:
            index = entry.get("index")
            if isinstance(index, int) and 0 <= index < len(self.items):
                self.keys[index] = (entry.get("artist") or "", entry.get("title") or "")

    @staticmethod
    def key(data: dict) -> tuple[str, str]:
        return (data.get("artist") or "", data.get("title") or "")

    def open_with(self, key: tuple[str, str]) -> list[int]:
        return [i for i, k in enumerate(self.keys) if k == key and i not in self.resolved]

    def pick(self, key: tuple[str, str], prefer: set[int] | None = None) -> int | None:
        open_items = self.open_with(key)
        if prefer is not None:
            preferred = [i for i in open_items if i in prefer]
            if preferred:
                return preferred[0]
        return open_items[0] if open_items else None

    def index_from_path(self, path: str) -> int | None:
        stem = Path(path.replace("\\", "/")).stem
        if stem.isdigit():
            index = int(stem) - FIRST_ROW
            if 0 <= index < len(self.items):
                return index
        return None

    def by_text(self, text: str) -> int | None:
        # rate limit lines say "<artist> - <title>", or only the title without an artist
        for index, (artist, title) in enumerate(self.keys):
            if index in self.resolved:
                continue
            if text == (f"{artist} - {title}" if artist else title):
                return index
        return None


class _Silence:
    """longest gap between two output lines on both streams, from the start to the end."""

    def __init__(self, redact: Redactor) -> None:
        self._redact = redact
        self._lock = threading.Lock()
        self._last = time.monotonic()
        self._last_text = ""
        self.longest = 0.0
        self.after = ""

    def start(self) -> None:
        self._last = time.monotonic()
        self._last_text = "(start)"

    def line(self, text: str | None) -> None:
        # text None is the end of a stream
        with self._lock:
            now = time.monotonic()
            gap = now - self._last
            if gap > self.longest:
                self.longest = gap
                self.after = self._redact(self._last_text)[:200]
            self._last = now
            self._last_text = "(end)" if text is None else text


class SockseekEngine:
    def __init__(self, settings: SockseekSettings) -> None:
        self.settings = settings
        self._cancel = threading.Event()
        self._proc: subprocess.Popen | None = None

    def args(self) -> list[str]:
        s = self.settings
        return [
            str(s.run_csv),
            # always with "=": a password starting with "-" is read as a flag otherwise
            f"--user={s.login.username}",
            f"--pass={s.login.password}",
            "--no-config",
            "--output-dir", str(s.output_dir),
            "--format", ",".join(s.formats),
            "--length-tol", str(LENGTH_TOLERANCE_S),
            "--strict-title",
            "--strict-artist",
            "--remove-ft",
            "--no-write-index",
            "--no-skip-existing",
            "--name-format", "{row}",
            "--progress-json",
            "-v",
            "--log-file", str(s.log_file),
            "--no-listen",
            *(["--pref-format", ",".join(s.pref_formats)] if s.pref_formats else []),
            *s.extra_args,
        ]  # fmt: skip

    def cancel(self) -> None:
        self._cancel.set()

    def _kill(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            # it may end on its own meanwhile
            with contextlib.suppress(OSError):
                proc.kill()

    def run(
        self, items: list[EngineItem], on_event: Callable[[base.EngineEvent], None]
    ) -> RunResult:
        s = self.settings
        s.output_dir.mkdir(parents=True, exist_ok=True)
        s.log_file.parent.mkdir(parents=True, exist_ok=True)
        write_run_csv(s.run_csv, items)
        command = s.command or [str(find_binary())]
        redact = Redactor([s.login.password])
        tracker = _Tracker(items)
        lines: queue.Queue[tuple[str, str | None]] = queue.Queue()
        last_activity = [time.monotonic()]
        silence = _Silence(redact)

        try:
            # the command line holds the password, it is never logged
            self._proc = start_process([*command, *self.args()])
        except OSError as exc:
            raise EngineError(
                tr("Could not start Sockseek ({error}). Install NeonSeek again.").format(
                    error=type(exc).__name__
                )
            ) from exc
        proc = self._proc

        silence.start()

        def pump(stream, name: str) -> None:
            for raw in iter(stream.readline, b""):
                last_activity[0] = time.monotonic()
                text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                silence.line(text)
                lines.put((name, text))
            silence.line(None)
            lines.put((name, None))

        for stream, name in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
            threading.Thread(target=pump, args=(stream, name), daemon=True).start()

        started = time.monotonic()
        got_json = False
        open_streams = 2
        ended: str | None = None
        message: str | None = None
        login_failure: str | None = None
        kicked = False
        try:
            while open_streams:
                try:
                    name, text = lines.get(timeout=0.2)
                except queue.Empty:
                    name, text = "", None
                else:
                    if text is None:
                        open_streams -= 1
                    elif name == "stdout" and text.startswith("{"):
                        event = _parse_json(text)
                        if event is None:
                            on_event(base.LogLine(name, redact(text)))
                        else:
                            got_json = True
                            self._dispatch(event, tracker, on_event)
                    else:
                        clean = redact(text)
                        on_event(base.LogLine(name, clean))
                        if name == "stderr":
                            failed = LOGIN_FAILED_RE.search(clean)
                            if failed:
                                login_failure = failed.group("why")
                            kicked = kicked or KICKED_TEXT in clean
                            limited = RATE_LIMIT_RE.search(clean)
                            if limited:
                                index = tracker.by_text(limited.group("what"))
                                track_id = items[index].track_id if index is not None else None
                                on_event(base.RateLimitWait(track_id))
                if ended is not None:
                    continue
                now = time.monotonic()
                if self._cancel.is_set():
                    ended = base.CANCELLED
                elif not got_json and now - started > s.start_timeout_s:
                    ended, message = base.START_TIMEOUT, start_timeout_message(s.start_timeout_s)
                elif now - last_activity[0] > s.idle_timeout_s:
                    ended = base.IDLE_TIMEOUT
                    message = idle_timeout_message(s.idle_timeout_s / 60)
                else:
                    continue
                self._kill()
        except KeyboardInterrupt:
            ended = base.CANCELLED
            self._kill()
        finally:
            # whatever happened in our code, sockseek must not keep running
            self._kill()
        exit_code = proc.wait()
        self._proc = None

        # identical artist and title: what is left after the {row} successes gets the failures
        for key, failures in tracker.deferred.items():
            for failure in failures:
                index = tracker.pick(key)
                if index is not None:
                    tracker.resolved.add(index)
                    on_event(_with_track(failure, items[index].track_id))

        if ended is None:
            if login_failure is not None or kicked:
                ended = base.LOGIN_FAILED
                message = kicked_message() if kicked else login_failure_message(login_failure)
            elif exit_code in (0, 1) and (got_json or not items):
                # 1 also means a normal run with tracks not found
                ended = base.COMPLETED
            else:
                ended = base.CRASHED
                message = tr(
                    "Sockseek stopped unexpectedly (exit code {code}). Start the download "
                    "again. If it keeps happening, copy the diagnostics from About."
                ).format(code=exit_code)

        _redact_file(s.log_file, redact)
        shutil.rmtree(s.output_dir / STAGING_SUBDIR, ignore_errors=True)
        unresolved = [items[i].track_id for i in range(len(items)) if i not in tracker.resolved]
        result = RunResult(
            ended,
            exit_code,
            message,
            unresolved,
            longest_silence_s=round(silence.longest, 1),
            silence_after=silence.after,
            searches=tracker.searches,
        )
        on_event(base.RunFinished(result))
        return result

    def _dispatch(
        self, event: dict, tracker: _Tracker, on_event: Callable[[base.EngineEvent], None]
    ) -> None:
        kind = event.get("type")
        data = event.get("data") or {}
        items = tracker.items
        if kind == "track_list":
            tracker.track_list(data)
        elif kind == "search_start":
            tracker.searches += 1
            on_event(base.SearchStarted())
            index = tracker.pick(
                tracker.key(data), prefer=set(range(len(items))) - tracker.searched
            )
            if index is not None:
                tracker.searched.add(index)
                on_event(base.ItemSearching(items[index].track_id))
        elif kind == "download_start":
            index = tracker.pick(tracker.key(data), prefer=tracker.searched)
            if index is not None:
                tracker.downloads[index] = _Download(
                    index, data.get("size"), data.get("username"), data.get("filename")
                )
                on_event(
                    base.ItemDownloading(
                        items[index].track_id, 0, data.get("username"), data.get("filename")
                    )
                )
        elif kind == "download_progress":
            job = data.get("jobId")
            index = tracker.jobs.get(job) if job else None
            if index is None:
                # progress only carries a job id, the size is the only link to a track
                for candidate in tracker.downloads.values():
                    if candidate.job_id is None and candidate.size == data.get("totalBytes"):
                        candidate.job_id = job
                        tracker.jobs[job] = index = candidate.index
                        break
            if index is not None and index not in tracker.resolved:
                download = tracker.downloads.get(index)
                on_event(
                    base.ItemDownloading(
                        items[index].track_id,
                        data.get("percent"),
                        download.user if download else None,
                        download.remote_path if download else None,
                    )
                )
        elif kind == "track_state":
            self._track_state(data, tracker, on_event)

    def _track_state(
        self, data: dict, tracker: _Tracker, on_event: Callable[[base.EngineEvent], None]
    ) -> None:
        items = tracker.items
        outcome = data.get("terminalOutcome")
        key = tracker.key(data)
        if outcome == "Cancelled":
            return  # stays unresolved and goes back to pending
        if outcome == "Succeeded" and data.get("downloadPath"):
            index = tracker.index_from_path(data["downloadPath"])
            if index is None or index in tracker.resolved:
                index = tracker.pick(key)
            if index is None:
                return
            tracker.resolved.add(index)
            on_event(
                base.ItemDone(
                    items[index].track_id,
                    Path(data["downloadPath"]),
                    data.get("username"),
                    data.get("filename"),
                )
            )
            return
        reason = data.get("failureReason") or outcome or "Other"
        if outcome == "Skipped":
            reason = f"Skipped ({data.get('skipReason') or 'unknown'})"
        if reason in NOT_FOUND_REASONS:
            failure: base.EngineEvent = base.ItemNotFound(-1, reason)
        else:
            failure = base.ItemFailed(-1, reason)
        open_items = tracker.open_with(key)
        if len(open_items) == 1:
            tracker.resolved.add(open_items[0])
            on_event(_with_track(failure, items[open_items[0]].track_id))
        elif open_items:
            # identical artist and title: successes are mapped by {row} first, the rest at the end
            tracker.deferred.setdefault(key, []).append(failure)


def _with_track(event, track_id: int):
    return type(event)(track_id, event.reason)


def _parse_json(text: str) -> dict | None:
    try:
        event = json.loads(text)
    except ValueError:
        return None
    return event if isinstance(event, dict) and "type" in event else None


def _redact_file(path: Path, redact: Redactor) -> None:
    # sockseek writes its own log, the password is cut from it after the run
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if redact.hits(text):
        path.write_text(redact(text), encoding="utf-8")


# login check: an empty run.csv logs in and ends without a single search (D-051)

LOGIN_OK = "ok"
LOGIN_REJECTED = "rejected"  # INVALIDPASS: wrong password, or a taken name for a new account
LOGIN_BAD_NAME = "invalid_username"
LOGIN_SERVER = "server"  # SVRFULL, SVRPRIVATE, INVALIDVERSION
LOGIN_UNREACHABLE = "unreachable"  # network error, sockseek would retry in the background
LOGIN_NO_ANSWER = "no_answer"  # nothing within the limit, also what a ban looks like
LOGIN_KICKED = "kicked"


@dataclass(frozen=True)
class LoginCheck:
    outcome: str
    code: str = ""  # server rejection code, if any
    detail: str = ""  # sockseek's text, with the password cut

    @property
    def ok(self) -> bool:
        return self.outcome == LOGIN_OK


def check_login(
    login: SoulseekLogin,
    work_dir: Path,
    *,
    command: list[str] | None = None,
    timeout_s: float | None = None,
    extra_args: Iterable[str] = (),
) -> LoginCheck:
    """logs in once with an empty list. killed at the first failure line, so a bad password is
    sent exactly once and never retried in the background. ends after timeout_s (default
    LOGIN_CHECK_TIMEOUT_S) plus at most LOGIN_CLEANUP_S, whatever sockseek does."""
    if timeout_s is None:
        timeout_s = LOGIN_CHECK_TIMEOUT_S
    work_dir.mkdir(parents=True, exist_ok=True)
    run_csv = work_dir / "login-check.csv"
    write_run_csv(run_csv, [])
    out = work_dir / "out"
    args = [
        str(run_csv),
        f"--user={login.username}",
        f"--pass={login.password}",
        "--no-config",
        "--output-dir", str(out),
        "--no-listen",
        "--progress-json",
        *extra_args,
    ]  # fmt: skip
    redact = Redactor([login.password])
    command = command or [str(find_binary())]
    try:
        proc = start_process([*command, *args])
    except OSError as exc:
        raise EngineError(
            tr("Could not start Sockseek ({error}). Install NeonSeek again.").format(
                error=type(exc).__name__
            )
        ) from exc
    trouble: list[str] = []
    lock = threading.Lock()

    def pump(stream) -> None:
        for raw in iter(stream.readline, b""):
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if any(marker in text.lower() for marker in LOGIN_TROUBLE):
                with lock:
                    if not trouble:
                        trouble.append(redact(text))
                        # before sockseek's 1 s reconnect delay sends the login again
                        proc.kill()

    threads = [
        threading.Thread(target=pump, args=(stream,), daemon=True)
        for stream in (proc.stdout, proc.stderr)
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        # every step after the limit is bounded too, so the check always ends (D-250)
        if proc.poll() is None:
            proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=LOGIN_CLEANUP_S)
        cleanup_end = time.monotonic() + LOGIN_CLEANUP_S
        for thread in threads:
            thread.join(timeout=max(0.0, cleanup_end - time.monotonic()))
        shutil.rmtree(work_dir, ignore_errors=True)

    if trouble:
        line = trouble[0]
        code = REJECTED_RE.search(line)
        code = code.group("code") if code else ""
        if code == "INVALIDPASS":
            return LoginCheck(LOGIN_REJECTED, code, line)
        if code == "INVALIDUSERNAME":
            return LoginCheck(LOGIN_BAD_NAME, code, line)
        if code:
            return LoginCheck(LOGIN_SERVER, code, line)
        if KICKED_TEXT in line:
            return LoginCheck(LOGIN_KICKED, "", line)
        return LoginCheck(LOGIN_UNREACHABLE, "", line)
    if timed_out:
        return LoginCheck(LOGIN_NO_ANSWER)
    if proc.returncode == 0:
        return LoginCheck(LOGIN_OK)
    return LoginCheck(LOGIN_UNREACHABLE, "", f"exit code {proc.returncode}")
