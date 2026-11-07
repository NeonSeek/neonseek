"""core behind the window: shared lines, the download monitor, helpers and diagnostics."""

from __future__ import annotations

from pathlib import Path

import pytest

from neonseek import actions, applog, config, outcomes, views
from neonseek import secrets as secret_store
from neonseek.db import repo as db
from neonseek.engine import base as engine
from neonseek.errors import ConfigError, NeonSeekError
from neonseek.pipeline import runner
from neonseek.pipeline.postprocess.base import TrackFile
from neonseek.sources import csv_file, ingest
from neonseek.sources.base import CollectionRecord, TrackRecord
from neonseek.spotify.urls import find_links


def _csv(ctx, name="list", titles=("One", "Two"), **values) -> int:
    tracks = [TrackRecord(title=t, artists=("Artist",), duration_ms=60_000) for t in titles]
    record = CollectionRecord(kind="csv", origin="file", name=name, source_ref=f"C:/{name}.csv",
                              snapshot_id=name, tracks=tracks)  # fmt: skip
    cid = ingest.ingest_collection(ctx.repo, record).collection_id
    if values:
        with ctx.repo.transaction():
            ctx.repo.update_collection(cid, values)
    return cid


# the download monitor (one source for the cli printer and the gui log)


def test_monitor_follows_a_run():
    monitor = outcomes.RunMonitor()
    assert monitor.progress_text() == "Starting..."
    lines = monitor.feed(runner.PassStarted(1, ("flac",), (1, 2)))
    assert lines[0].text == "Pass 1 (FLAC): 2 tracks"
    assert monitor.feed(engine.RateLimitWait(1))[0].kind == outcomes.WARN
    assert monitor.waiting
    assert monitor.feed(engine.RateLimitWait(1)) == []  # said once
    assert monitor.feed(engine.ItemSearching(1)) == [] and not monitor.waiting
    assert monitor.changed == {1}
    monitor.feed(engine.ItemDownloading(1, 40))
    assert monitor.percent == {1: 40}
    done = TrackFile(1, Path("x.flac"), format="flac", sample_rate=44100, bit_depth=16)
    line = monitor.feed(runner.TrackFinished(1, "A - One", "done", None, done))[0]
    assert line.kind == outcomes.DONE and "FLAC 44.1/16" in line.text
    assert monitor.percent == {} and monitor.pass_done == 1
    line = monitor.feed(runner.TrackFinished(2, "A - Two", "not_found", "NoSearchResults"))[0]
    assert line.kind == outcomes.WARN and "nothing found on Soulseek" in line.text
    assert monitor.progress_text() == "Pass 1 (FLAC): 2 / 2"
    wait = monitor.feed(runner.SearchLimitWait(100))[0]
    assert "1 min 40 s" in wait.text and monitor.waiting
    assert monitor.feed(runner.StepWarning(1, "A - One", "cover", "bad"))[0].kind == outcomes.WARN
    assert monitor.feed(engine.LogLine("stderr", "x")) == []


def test_plan_lines_show_collections_tracks_and_time():
    plan = actions.DownloadPlan([(3, "Mix")], [1, 2, 3], 3, 1, 5, 9, [(4, "Empty")], 220)
    text = outcomes.texts(outcomes.plan_lines(plan))
    assert "[3] Mix" in text and "Empty has no tracks yet" in text
    assert "Tracks in these collections: 9, already downloaded: 5." in text
    assert "FLAC pass: 3 tracks to search" in text and "up to 3 min 40 s" in text
    assert "not included" in text and "WAV/AIFF pass already: 1" in text
    nothing = actions.DownloadPlan([(3, "Mix")], [], 0, 0, 9, 9, [], 0)
    assert "FLAC pass" not in outcomes.texts(outcomes.plan_lines(nothing))


def test_run_end_lines_mark_errors_for_stderr(ctx):
    outcome = runner.RunOutcome(7, runner.ERROR, "boom", {"done": 2})
    lines = outcomes.run_end_lines(ctx, outcome)
    assert lines[0].text == "Tracks of this run: 2 done"
    assert lines[-1].err and lines[-1].kind == outcomes.ERROR and "boom" in lines[-1].text
    cancelled = outcomes.run_end_lines(ctx, runner.RunOutcome(7, runner.CANCELLED))
    assert "Finished tracks are kept" in cancelled[-1].text


def test_retag_hint_differs_only_in_the_way_to_do_it():
    assert "neonseek retag --collection 5" in outcomes.retag_hint(5, gui=False).text
    assert "WRITE TAGS" in outcomes.retag_hint(5, gui=True).text


# window helpers


def test_spotify_state_without_asking_spotify(ctx):
    assert actions.spotify_state(ctx) == actions.SpotifyState(False, False)
    assert "Client ID" in actions.spotify_needed_message(actions.spotify_state(ctx))
    actions.set_spotify_client_id(ctx, "client")
    assert actions.spotify_state(ctx) == actions.SpotifyState(True, False)
    assert "Log in" in actions.spotify_needed_message(actions.spotify_state(ctx))
    ctx.token_store.save("refresh")
    assert actions.spotify_state(ctx).ready


def test_setup_is_needed_until_library_and_login(ctx):
    assert actions.needs_setup(ctx)
    actions.change_setting(ctx, "library_dir", str(ctx.data_dir / "lib"))
    assert actions.needs_setup(ctx)
    secret_store.set_soulseek_login(secret_store.SoulseekLogin("me", "pw"))
    assert not actions.needs_setup(ctx)


def test_collection_folder_prefers_the_playlist(ctx, tmp_path):
    cid = _csv(ctx)
    with pytest.raises(actions.NoLibraryError):
        actions.collection_folder(ctx, cid)
    library = tmp_path / "lib"
    actions.change_setting(ctx, "library_dir", str(library))
    assert actions.collection_folder(ctx, cid) == actions.Place(library, False)
    (library / "tracks").mkdir(parents=True)
    assert actions.collection_folder(ctx, cid).path == library / "tracks"
    playlist = actions.write_playlists(ctx, [cid])[0]
    assert actions.collection_folder(ctx, cid) == actions.Place(playlist, True)


def test_collection_source(ctx):
    csv_id = _csv(ctx)
    assert actions.collection_source(ctx, csv_id).path == "C:/list.csv"
    record = CollectionRecord(kind="spotify_playlist", origin="account", name="p",
                              external_id="abc")  # fmt: skip
    pid = ingest.ingest_collection(ctx.repo, record).collection_id
    assert actions.collection_source(ctx, pid).url == "https://open.spotify.com/playlist/abc"
    liked = ingest.ingest_collection(
        ctx.repo,
        CollectionRecord(kind="spotify_liked", origin="account", name="Liked", external_id="me"),
    ).collection_id
    assert actions.collection_source(ctx, liked).url.endswith("/collection/tracks")
    with pytest.raises(NeonSeekError):
        actions.collection_source(ctx, 999)


def test_theme_setting_is_checked_against_the_themes(ctx):
    assert ctx.settings().theme == "neon"
    assert actions.change_setting(ctx, "theme", "ice") == "ice"
    assert ctx.settings().theme == "ice"
    with pytest.raises(ConfigError, match="no theme gone.*neon, daylight"):
        actions.change_setting(ctx, "theme", "gone")
    with pytest.raises(ConfigError, match="not a theme name"):
        actions.change_setting(ctx, "theme", "Bad Name")
    folder = ctx.data_dir / "themes"
    folder.mkdir(parents=True)
    neon = (Path(__file__).parents[1] / "src/neonseek/ui/theme/themes/neon.toml").read_text(
        encoding="utf-8")  # fmt: skip
    (folder / "mine.toml").write_text(neon.replace('"Neon"', '"Mine"'), encoding="utf-8")
    assert actions.change_setting(ctx, "theme", "mine") == "mine"


def test_sockseek_info_runs_only_the_version(ctx, fake_sockseek):
    script = fake_sockseek([{"out": "3.0.5"}, {"exit": 0}])
    info = actions.sockseek_info(ctx)
    assert info.version == "3.0.5" and "AGPL" in info.source
    assert script.with_suffix(".args.json").read_text() == '["--version"]'
    fake_sockseek([{"exit": 3}])
    with pytest.raises(NeonSeekError, match="Install NeonSeek again"):
        actions.sockseek_info(ctx)


def test_third_party_lists_every_license():
    names = {name: license_name for name, _, license_name in actions.third_party()}
    assert names["Sockseek"] == "AGPL-3.0" and names["JetBrains Mono"] == "OFL-1.1"
    assert "PySide6-Essentials" in names and "mutagen" in names


def test_diagnostics_cut_secrets_names_and_the_user_folder(ctx, fake_sockseek, monkeypatch):
    fake_sockseek([{"out": "3.0.5"}, {"exit": 0}])
    secret_store.set_soulseek_login(secret_store.SoulseekLogin("djname", "Hunter2-secret"))
    secret_store.set_secret(secret_store.SPOTIFY_REFRESH_TOKEN, "refresh-token-xyz")
    home = str(Path.home())
    log = applog.AppLog(ctx.data_dir)
    log.write(f"logged in as djname with Hunter2-secret and refresh-token-xyz in {home}")
    run = config.logs_dir(ctx.data_dir) / "run-1.log"
    run.write_text("sockseek --pass=Hunter2-secret\n", encoding="utf-8")
    text = actions.diagnostics(ctx, {"Qt": "6.12"})
    assert "NeonSeek" in text and "Sockseek 3.0.5" in text and "Qt 6.12" in text
    assert "Soulseek login saved: yes" in text
    for secret in ("Hunter2-secret", "refresh-token-xyz", "djname", home):
        assert secret not in text
    assert "%USERPROFILE%" in text


def test_app_log_cuts_secrets_and_rotates(ctx, monkeypatch):
    secret_store.set_soulseek_login(secret_store.SoulseekLogin("me", "Sekret-123"))
    log = applog.AppLog(ctx.data_dir)
    log.write("password Sekret-123 here", "warn")
    text = log.path.read_text(encoding="utf-8")
    assert "Sekret-123" not in text and "[warn]" in text
    secret_store.set_soulseek_login(secret_store.SoulseekLogin("me", "New-Pass-456"))
    log.refresh_secrets()
    log.write("now New-Pass-456")
    assert "New-Pass-456" not in log.path.read_text(encoding="utf-8")
    monkeypatch.setattr(applog, "MAX_BYTES", 10)
    log.write("after the limit")
    assert log.path.with_name("neonseek.1.log").exists()
    assert applog.tail(log.path) == [applog.tail(log.path)[-1]]
    assert applog.tail(ctx.data_dir / "nothing.log") == []


def test_csv_defaults_keep_name_and_mapping(ctx, csv_dir):
    path = csv_dir / "custom_headers.csv"
    fresh = actions.csv_import_defaults(ctx, path)
    assert fresh == actions.CsvDefaults(path.stem, {}, None, None)
    header = csv_file.read_table(path).header
    outcome = actions.import_csv(ctx, path, name="Mine", overrides={"title": header[0]},
                                 length_unit="s")  # fmt: skip
    again = actions.csv_import_defaults(ctx, path)
    assert again.name == "Mine" and again.collection_id == outcome.collection_id
    assert again.overrides == {"title": header[0]} and again.length_unit == "s"


def test_partial_mapping_for_the_dialog(csv_dir):
    header = csv_file.read_table(csv_dir / "custom_headers.csv").header
    with pytest.raises(csv_file.CsvMappingError):
        csv_file.resolve_columns(header, {})
    partial = csv_file.resolve_columns(header, {}, require_title=False)
    assert "title" not in partial


def test_cancel_before_the_runner_exists_stops_the_run(env):
    from download_env import track

    env.file("A/Album/01 - One.flac", 60)
    cid = env.collection("C", [track("A", "One", "Album", 60)])
    with actions.DownloadJob(env.ctx, [cid]) as job:
        job.cancel()
        outcome = job.run(lambda event: None)
    assert outcome.ended == runner.CANCELLED
    assert env.state(env.ids(cid)[0])["status"] == db.PENDING


def test_library_rows_and_live_labels(ctx):
    one = _csv(ctx, "one", ("A",))
    _csv(ctx, "two", ("B",))
    rows = views.library_tracks(ctx)
    assert [r.title for r in rows] == ["A", "B"]
    (tid,) = ctx.repo.collection_track_ids(one)
    ctx.repo.update_download(tid, {"status": db.DOWNLOADING})
    fresh = views.tracks_for(ctx, [tid], {tid: 4})
    assert fresh[tid].number == 4 and views.live_label(fresh[tid], 42) == "downloading 42%"
    assert views.live_label(rows[1], 42) == "not downloaded"


def test_report_state_and_choice_labels(ctx):
    cid = _csv(ctx, "one", ("A",))
    (tid,) = ctx.repo.collection_track_ids(cid)
    ctx.repo.update_download(tid, {"status": db.NOT_FOUND})
    (row,) = actions.report(ctx)
    assert views.report_state(row, ctx.settings()) == (views.WAITING_FALLBACK,
                                                       "waiting for WAV/AIFF")  # fmt: skip
    assert views.choice_label("tags_from_source", "overwrite") == "Replace with the source data"
    assert views.choice_label("on_missing_flac", "lossless").startswith("Take WAV or AIFF")
    assert views.setting_hint("on_missing_flac")


def test_find_links_in_pasted_text():
    text = (
        "look https://open.spotify.com/intl-pl/album/0NeonFxAlb000000000002?si=x and\n"
        "spotify:track:11dFghVXANMlKmJXsNCbNl https://example.com https://spotify.link/a "
        "https://open.spotify.com/intl-pl/album/0NeonFxAlb000000000002?si=x"
    )
    assert find_links(text) == [
        "https://open.spotify.com/intl-pl/album/0NeonFxAlb000000000002?si=x",
        "spotify:track:11dFghVXANMlKmJXsNCbNl",
        "https://spotify.link/a",
    ]
    assert find_links("nothing here") == []


def test_fork_shares_fakes_but_not_the_connection(ctx):
    _csv(ctx)
    other = ctx.fork()
    try:
        assert other.data_dir == ctx.data_dir and other.token_store is ctx.token_store
        assert other.http_factory is ctx.http_factory
        assert other.repo.conn is not ctx.repo.conn
        assert len(other.repo.list_collections()) == 1
    finally:
        other.close()


def test_csv_preview_for_the_dialog(csv_dir):
    good = actions.csv_preview(csv_dir / "exportify_comma_ms.csv")
    assert good.parse is not None and good.error is None and len(good.parse.tracks) == 4
    assert good.columns["title"] == "Track Name" and good.auto["title"] == "Track Name"
    custom = csv_dir / "custom_headers.csv"
    partial = actions.csv_preview(custom)
    assert partial.parse is None and "track titles" in partial.error
    assert "title" not in partial.columns and partial.header
    mapped = actions.csv_preview(custom, {"title": partial.header[0]})
    assert mapped.parse is not None and mapped.columns["title"] == partial.header[0]
    with pytest.raises(NeonSeekError, match="does not exist"):
        actions.csv_preview(csv_dir / "gone.csv")
