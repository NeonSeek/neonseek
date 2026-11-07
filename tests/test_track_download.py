"""download this track only and retry with download (D-125, D-131), on the pinned sockseek
with a mock library. nothing reaches soulseek."""

from __future__ import annotations

from download_env import needs_sockseek, track
from neonseek import actions
from neonseek.cli import main
from neonseek.db import repo as db
from neonseek.pipeline import playlists

pytestmark = needs_sockseek


def _three(env):
    env.file("A/Album/01 - One.flac", 60)
    env.file("A/Album/02 - Two.flac", 50)
    env.file("A/Album/03 - Three.flac", 40)
    cid = env.collection("Mix", [track("A", "One", "Album", 60), track("A", "Two", "Album", 50),
                                 track("A", "Three", "Album", 40)])  # fmt: skip
    return cid, env.ids(cid)


def _run_tracks(env, track_ids):
    env.events = []
    with actions.DownloadJob(env.ctx, [], track_ids=track_ids) as job:
        plan = job.plan
        return plan, job.run(env.events.append)


def test_only_the_chosen_track_is_downloaded(env):
    cid, (one, two, three) = _three(env)
    plan, outcome = _run_tracks(env, [two])
    assert plan.chosen == [(two, "A - Two")] and plan.collections == [(cid, "Mix")]
    assert plan.first_pass == 1
    assert env.state(two)["status"] == "done"
    assert env.state(one)["status"] == env.state(three)["status"] == "pending"
    assert env.titles(outcome.run_id, 1) == ["Two"]  # the same search limit and passes
    playlist = playlists.playlist_path(env.ctx.repo, cid, env.library)
    assert outcome.playlists == [playlist] and "Two" in playlist.read_text(encoding="utf-8")


def test_not_found_retries_excluded_comes_back_done_is_skipped(env):
    _, (one, two, three) = _three(env)
    repo = env.ctx.repo
    # not found in flac and moved on to the wav pass, as the report shows it
    repo.update_download(one, {"status": db.NOT_FOUND, "stage": db.STAGE_FALLBACK,
                               "last_error": "NoSearchResults"})  # fmt: skip
    repo.update_download(two, {"status": db.NOT_FOUND})
    repo.exclude([two])
    _run_tracks(env, [three])
    assert env.state(three)["status"] == "done"
    plan, outcome = _run_tracks(env, [one, two, three])
    assert plan.done == 1 and plan.first_pass == 2 and plan.track_ids == [one, two]
    # like a retry from the report: searched again as flac, also the excluded one
    assert env.titles(outcome.run_id, 1) == ["One", "Two"]
    assert env.state(one)["status"] == env.state(two)["status"] == "done"
    assert env.state(two)["excluded_from"] is None


def test_declining_changes_nothing(env):
    _, (one, two, _) = _three(env)
    env.ctx.repo.update_download(one, {"status": db.NOT_FOUND})
    env.ctx.repo.exclude([two])
    with actions.DownloadJob(env.ctx, [], track_ids=[one, two]) as job:
        assert job.plan.first_pass == 2
    assert env.state(one)["status"] == "not_found" and env.state(two)["status"] == "excluded"


def test_a_track_of_a_removed_collection_still_downloads(env):
    cid, (one, _, _) = _three(env)
    actions.remove_collections(env.ctx, [cid])
    plan, outcome = _run_tracks(env, [one])
    assert plan.collections == [] and outcome.playlists == []
    assert env.state(one)["status"] == "done"


def test_excluded_tracks_stay_out_of_a_collection_download(env):
    cid, (one, two, three) = _three(env)
    env.ctx.repo.exclude([two])
    outcome = env.run([cid])
    assert env.titles(outcome.run_id, 1) == ["One", "Three"]
    assert env.state(two)["status"] == "excluded"


def _cli(capsys, env, *argv):
    env.ctx.close()
    code = main(["--data-dir", str(env.ctx.data_dir), *argv])
    out, err = capsys.readouterr()
    return code, out, err


def test_cli_download_track_and_retry_download(env, capsys):
    _, (one, two, three) = _three(env)
    code, out, err = _cli(capsys, env, "download", "--track", str(two))
    assert code == 1 and "Tracks in this download" not in out and "Track in this download" in out
    assert "add --yes" in err and env.state(two)["status"] == "pending"
    code, out, _ = _cli(capsys, env, "download", "--track", str(two), "--yes")
    assert code == 0 and env.state(two)["status"] == "done"
    assert env.state(one)["status"] == "pending"
    code, _, err = _cli(capsys, env, "download", "--track", str(one), "--collection", "1")
    assert code == 1 and "not both" in err

    env.ctx.repo.update_download(three, {"status": db.NOT_FOUND, "last_error": "NoSearchResults"})
    code, out, _ = _cli(capsys, env, "report")
    assert "--download" in out
    code, out, _ = _cli(capsys, env, "retry", str(three), "--download", "--yes")
    assert code == 0 and env.state(three)["status"] == "done" and "FLAC pass: 1 track" in out
