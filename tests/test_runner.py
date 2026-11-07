"""runner on a fake engine: the search limit across passes and step failures."""

from __future__ import annotations

from pathlib import Path

import pytest

from audio_files import write_audio
from neonseek import config
from neonseek.covers import CoverCache
from neonseek.engine import base
from neonseek.pipeline import runner
from neonseek.pipeline.postprocess.base import StepError
from neonseek.pipeline.postprocess.place import Place
from neonseek.pipeline.postprocess.verify import Verify
from neonseek.sources import ingest
from neonseek.sources.base import CollectionRecord, TrackRecord


class FakeEngine:
    def __init__(self, script):
        self.script = script

    def run(self, items, on_event):
        return self.script(items, on_event)

    def cancel(self):
        pass


def not_found_all(searches: int):
    def script(items, on_event):
        for _ in range(searches):
            on_event(base.SearchStarted())
        for item in items:
            on_event(base.ItemNotFound(item.track_id, "NoSearchResults"))
        return base.RunResult(base.COMPLETED, 1)

    return script


def make_runner(repo, tmp_path, tracks: int, factory, events, steps=None, window=1.0):
    record = CollectionRecord(
        kind="csv", origin="file", name="c", source_ref="c", snapshot_id="c",
        tracks=[TrackRecord(title=f"Song {n}", artists=("A",), duration_ms=60000)
                for n in range(tracks)],
    )  # fmt: skip
    cid = ingest.ingest_collection(repo, record).collection_id
    return runner.Runner(
        repo=repo,
        settings=config.Settings(),
        library_dir=tmp_path / "lib",
        covers=CoverCache(tmp_path / "covers", None),
        logs_dir=tmp_path / "logs",
        engine_factory=factory,
        collection_ids=[cid],
        track_ids=repo.collection_track_ids(cid),
        on_event=events.append,
        steps=steps,
        search_window_s=window,
    )


def test_small_pass_2_gets_only_what_is_left_of_the_window(repo, tmp_path):
    calls, events = [], []

    def factory(run_id, number, formats, per_time):
        calls.append((number, per_time))
        return FakeEngine(not_found_all(3))

    outcome = make_runner(repo, tmp_path, 3, factory, events, window=30.0).run()
    # 3 searches used in pass 1, so pass 2 may use 31 more, never a fresh 34
    assert calls == [(1, None), (2, 31)]
    assert outcome.ended == runner.COMPLETED
    assert not [e for e in events if isinstance(e, runner.SearchLimitWait)]


def test_big_pass_2_waits_for_the_window(repo, tmp_path):
    calls, events = [], []

    def factory(run_id, number, formats, per_time):
        calls.append((number, per_time))
        return FakeEngine(not_found_all(3))

    make_runner(repo, tmp_path, 40, factory, events, window=1.0).run()
    assert calls == [(1, None), (2, None)]
    waits = [e for e in events if isinstance(e, runner.SearchLimitWait)]
    assert len(waits) == 1 and 0 < waits[0].seconds <= 1.0
    assert repo.searches_since(0) and len(repo.searches_since(0)) == 6


def test_no_searches_left_means_waiting_not_a_zero_limit(repo, tmp_path):
    calls, events = [], []

    def factory(run_id, number, formats, per_time):
        calls.append((number, per_time))
        # pass 1 uses the whole window, pass 2 needs a single search
        return FakeEngine(not_found_all(34 if number == 1 else 1))

    make_runner(repo, tmp_path, 1, factory, events, window=1.0).run()
    assert calls == [(1, None), (2, None)]
    waits = [e for e in events if isinstance(e, runner.SearchLimitWait)]
    assert len(waits) == 1 and 0 < waits[0].seconds <= 1.0


class Boom:
    name = "tag"
    fatal = False

    def process(self, track_file, ctx):
        raise StepError("tag write failed")


def done_script(staging: Path):
    def script(items, on_event):
        for n, item in enumerate(items, start=2):
            path = write_audio(staging / f"{n}.flac", 60)
            on_event(base.ItemDone(item.track_id, path, "user", "remote\\file.flac"))
        return base.RunResult(base.COMPLETED, 0)

    return script


def test_a_failing_tag_step_does_not_undo_the_download(repo, tmp_path):
    events = []
    staging = tmp_path / "lib" / runner.STAGING_DIR
    steps = [Verify(), Boom(), Place()]
    run = make_runner(repo, tmp_path, 1, lambda *a: FakeEngine(done_script(staging)), events,
                      steps=steps)  # fmt: skip
    outcome = run.run()
    (tid,) = run.track_ids
    row = repo.track_states([tid])[tid]
    assert row["status"] == "done" and row["source_user"] == "user"
    assert [e.step for e in events if isinstance(e, runner.StepWarning)] == ["tag"]
    log = (tmp_path / "logs" / f"run-{outcome.run_id}.log").read_text(encoding="utf-8")
    assert "tag write failed" in log


class Crash:
    name = "verify"
    fatal = True

    def process(self, track_file, ctx):
        raise RuntimeError("bug")


def test_a_fatal_step_makes_the_track_failed_and_cleans_staging(repo, tmp_path):
    events = []
    staging = tmp_path / "lib" / runner.STAGING_DIR
    run = make_runner(repo, tmp_path, 1, lambda *a: FakeEngine(done_script(staging)), events,
                      steps=[Crash(), Place()])  # fmt: skip
    run.run()
    (tid,) = run.track_ids
    row = repo.track_states([tid])[tid]
    assert (row["status"], row["attempts"]) == ("failed", 1)
    assert "bug" in row["last_error"]
    assert not staging.exists()


@pytest.mark.parametrize("ended", [base.START_TIMEOUT, base.LOGIN_FAILED])
def test_engine_error_stops_the_run(repo, tmp_path, ended):
    calls, events = [], []

    def factory(run_id, number, formats, per_time):
        calls.append(number)
        return FakeEngine(
            lambda items, on_event: base.RunResult(
                ended, None, "say what to do", [i.track_id for i in items]
            )
        )

    outcome = make_runner(repo, tmp_path, 2, factory, events).run()
    assert calls == [1]  # no fallback pass after an error
    assert (outcome.ended, outcome.message) == (runner.ERROR, "say what to do")
    assert outcome.counts == {"pending": 2}
