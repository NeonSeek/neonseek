"""the lists neonseek names itself show in the language of the window everywhere, while the
database keeps the english name (D-180)."""

from __future__ import annotations

from neonseek import actions, cli, i18n, outcomes, secrets
from neonseek.db import repo as db
from neonseek.sources import ingest
from neonseek.sources.base import KIND_SINGLES, TrackRecord, display_name

POLISH = i18n.translate("Single tracks", i18n.POLISH)


def _single(ctx) -> tuple[int, int]:
    result = ingest.add_single(
        ctx.repo, TrackRecord(title="One", artists=("A",), duration_ms=60_000)
    )
    return result.collection_id, result.track_id


def test_the_name_is_translated_and_the_database_keeps_english(ctx):
    cid, _ = _single(ctx)
    assert POLISH != "Single tracks"
    i18n.set_language(i18n.POLISH)
    assert ctx.repo.get_collection(cid)["name"] == "Single tracks"
    assert display_name(KIND_SINGLES, "Single tracks") == POLISH
    # a name the user gave stays as it is
    assert display_name(KIND_SINGLES, "My singles") == "My singles"
    assert [c.name for c in actions.list_collections(ctx)] == [POLISH]


def test_every_place_that_reads_the_name_from_the_database(ctx):
    cid, tid = _single(ctx)
    ctx.repo.conn.execute("UPDATE downloads SET status = ? WHERE track_id = ?", (db.NOT_FOUND, tid))
    ctx.repo.conn.commit()
    actions.change_setting(ctx, "library_dir", str(ctx.data_dir / "library"))
    secrets.set_soulseek_login(secrets.SoulseekLogin("someone", "a-password"))
    i18n.set_language(i18n.POLISH)
    # the report and its rows
    (row,) = actions.report(ctx)
    assert row.collections == [(cid, POLISH)]
    # properties of a track
    assert actions.track_info(ctx, tid).collections == [(cid, POLISH)]
    # the question before removing it
    assert actions.remove_plan(ctx, [cid]).collections == [(cid, POLISH)]
    # the summary before a download and the log lines made from it
    with actions.DownloadJob(ctx, [cid]) as job:
        assert job.plan.collections == [(cid, POLISH)]
        assert POLISH in outcomes.texts(outcomes.plan_lines(job.plan, gui=True))


def test_the_cli_shows_the_translated_name(ctx, capsys):
    cid, _ = _single(ctx)
    cli.main(["--data-dir", str(ctx.data_dir), "settings", "language", "pl"])
    capsys.readouterr()
    cli.main(["--data-dir", str(ctx.data_dir), "collections"])
    assert POLISH in capsys.readouterr().out
    cli.main(["--data-dir", str(ctx.data_dir), "collection", "info", str(cid)])
    assert POLISH in capsys.readouterr().out
    cli.main(["--data-dir", str(ctx.data_dir), "collection", "tags", str(cid), "--genre", "Techno"])
    assert POLISH in capsys.readouterr().out
