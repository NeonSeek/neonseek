"""download.lock after a neonseek that was closed or killed, e.g. by the installer of an update
(D-262). the lock is the one windows holds on the open file, the pid inside is only a note."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time

import pytest

from download_env import needs_sockseek, track
from neonseek import actions
from neonseek.pipeline import runner
from neonseek.run_lock import AlreadyRunningError, RunLock

HOLD = """
import os, sys, time
from pathlib import Path
from neonseek.run_lock import RunLock

lock = RunLock(Path(sys.argv[1]))
lock.acquire()
print(os.getpid(), flush=True)
time.sleep(120)
"""
WAIT = "import os, time; print(os.getpid(), flush=True); time.sleep(120)"


def _python(code: str, *args: str) -> tuple[subprocess.Popen, int]:
    """a python process and its own pid: the python of a venv on windows is a launcher that
    starts the real one as its child."""
    child = subprocess.Popen([sys.executable, "-c", code, *args], stdout=subprocess.PIPE,
                             text=True)  # fmt: skip
    return child, int(child.stdout.readline())


def _kill(child: subprocess.Popen, pid: int) -> None:
    # terminateprocess, like a program the installer has to close
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGTERM)
    child.wait(30)
    child.stdout.close()


def _killed_holder(path) -> int:
    """a process that holds the lock and is killed. the file stays behind with its pid."""
    child, pid = _python(HOLD, str(path))
    try:
        with pytest.raises(AlreadyRunningError):
            RunLock(path).acquire()
    finally:
        _kill(child, pid)
    assert path.read_text(encoding="ascii") == str(pid)
    return pid


def _soon(check, seconds: float = 10.0):
    # windows frees the lock of a dead process right away, a slow machine may need a moment
    end = time.monotonic() + seconds
    while True:
        try:
            return check()
        except AlreadyRunningError:
            if time.monotonic() > end:
                raise
            time.sleep(0.1)


def _interrupted(ctx, cid) -> list[int]:
    ids = ctx.repo.collection_track_ids(cid)
    with ctx.repo.conn:
        ctx.repo.conn.execute("UPDATE downloads SET status = 'downloading'")
    return ids


def test_start_after_a_killed_neonseek(env):
    path = env.ctx.data_dir / "download.lock"
    _killed_holder(path)
    cid = env.collection("Mix", [track("Artist", "Song", "Album", 60)])
    [track_id] = _interrupted(env.ctx, cid)
    # the start recovers what the killed run left in progress, a busy lock would skip it
    assert _soon(lambda: _recovered(env.ctx)) == 1
    assert env.state(track_id)["status"] == "pending"


def _recovered(ctx) -> int:
    # recover_interrupted skips a busy lock: first make sure windows has freed it
    lock = RunLock(ctx.data_dir / "download.lock")
    lock.acquire()
    lock.release()
    return actions.recover_interrupted(ctx)


def test_a_pid_taken_by_another_process_is_no_lock(env):
    # the old pid now belongs to a running process that has nothing to do with neonseek
    other, other_pid = _python(WAIT)
    try:
        path = env.ctx.data_dir / "download.lock"
        _killed_holder(path)
        path.write_text(str(other_pid), encoding="ascii")
        cid = env.collection("Mix", [track("Artist", "Song", "Album", 60)])
        [track_id] = _interrupted(env.ctx, cid)
        assert _soon(lambda: _recovered(env.ctx)) == 1
        assert env.state(track_id)["status"] == "pending"
        # the next download takes the lock, writes its own pid and recovers too
        path.write_text(str(other_pid), encoding="ascii")
        _interrupted(env.ctx, cid)
        with actions.DownloadJob(env.ctx, [cid], None):
            assert env.state(track_id)["status"] == "pending"
            with pytest.raises(AlreadyRunningError):
                RunLock(path).acquire()
        # windows does not let anyone read the locked byte, so the pid is read afterwards
        assert path.read_text(encoding="ascii") == str(os.getpid())
    finally:
        _kill(other, other_pid)


@needs_sockseek
def test_a_download_runs_after_a_killed_neonseek(env):
    path = env.ctx.data_dir / "download.lock"
    _killed_holder(path)
    env.file("Artist/Album/01 - Song.flac", 60)
    cid = env.collection("Mix", [track("Artist", "Song", "Album", 60)])
    _interrupted(env.ctx, cid)
    outcome = _soon(lambda: env.run([cid]))
    assert outcome.ended == runner.COMPLETED
    [track_id] = env.ids(cid)
    assert env.state(track_id)["status"] == "done"
