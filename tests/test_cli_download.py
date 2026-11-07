"""download, report, retry, collection tags, retag and settings through the cli."""

from __future__ import annotations

import csv

from mutagen.flac import FLAC

from download_env import needs_sockseek, track
from neonseek.cli import main


def _run(capsys, env, *argv) -> tuple[int, str, str]:
    code = main(["--data-dir", str(env.ctx.data_dir), *argv])
    out, err = capsys.readouterr()
    return code, out, err


def _setup(env):
    env.file("Alpha Artist/First Album/01 - First Song.flac", 60)
    env.file("Beta Artist/Second Album/02 - Second Song.flac", 50)
    one = env.collection(
        "One",
        [
            track("Alpha Artist", "First Song", "First Album", 60),
            track("Mu Artist", "Missing Song", "Nowhere", 40),
        ],
    )
    two = env.collection("Two", [track("Beta Artist", "Second Song", "Second Album", 50)])
    return one, two  # fmt: skip


def test_download_needs_a_choice_and_lists_collections(capsys, env):
    one, two = _setup(env)
    code, out, err = _run(capsys, env, "download")
    assert code == 1 and "--collection ID" in err and "--selected" in err
    assert "One" in out and "Two" in out  # the list of collections with ids
    code, out, err = _run(capsys, env, "download", "--selected")
    assert code == 1 and "No collection is selected" in err
    assert all(env.state(t)["status"] == "pending" for t in env.ids(one) + env.ids(two))


def test_download_asks_before_starting(capsys, env):
    one, _ = _setup(env)
    code, out, err = _run(capsys, env, "download", "--collection", str(one))
    # stdin of the tests is no terminal: without --yes nothing starts
    assert code == 1 and "Nothing was downloaded" in err and "--yes" in err
    assert "FLAC pass: 2 tracks to search" in out and "up to 3 min 40 s" in out
    assert "not included" in out
    assert all(env.state(t)["status"] == "pending" for t in env.ids(one))


def test_download_without_library_says_what_to_do(capsys, env):
    one, _ = _setup(env)
    settings = env.ctx.settings()
    settings.library_dir = ""
    env.ctx.save_settings(settings)
    code, _, err = _run(capsys, env, "download", "--collection", str(one), "--yes")
    assert code == 1 and "library" in err and "neonseek settings library_dir" in err


@needs_sockseek
def test_download_selected_report_and_retry(capsys, env, tmp_path):
    one, two = _setup(env)
    assert _run(capsys, env, "select", str(two))[0] == 0
    code, out, err = _run(capsys, env, "download", "--selected", "--yes")
    assert code == 0, err
    assert "done       Beta Artist - Second Song  FLAC 44.1/16" in out
    assert "Two.m3u8" in out and "longest silence" in out
    # only the selected collection was downloaded
    assert [env.state(t)["status"] for t in env.ids(one)] == ["pending", "pending"]

    code, out, err = _run(capsys, env, "download", "--collection", str(one), "--yes")
    assert code == 0, err
    assert "not found  Mu Artist - Missing Song (nothing found on Soulseek)" in out
    missing = env.ids(one)[1]

    code, out, _ = _run(capsys, env, "report")
    assert code == 0 and "Mu Artist - Missing Song" in out and "not found" in out
    exported = tmp_path / "report.csv"
    assert _run(capsys, env, "report", "--csv", str(exported))[0] == 0
    with exported.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert [r["Track"] for r in rows] == ["Mu Artist - Missing Song"]

    code, out, _ = _run(capsys, env, "retry", str(missing), str(env.ids(one)[0]))
    assert "1 track starts again" in out and "Not changed" in out
    row = env.state(missing)
    assert (row["status"], row["stage"], row["attempts"]) == ("pending", "flac", 0)


@needs_sockseek
def test_collection_tags_and_retag(capsys, env):
    one, _ = _setup(env)
    assert _run(capsys, env, "download", "--collection", str(one), "--yes")[0] == 0
    path = env.state(env.ids(one)[0])["file_path"]
    assert "GENRE" not in FLAC(path)

    code, out, _ = _run(capsys, env, "collection", "tags", str(one), "--genre", "Hardcore Techno",
                        "--comment", "dark set")  # fmt: skip
    assert code == 0 and "neonseek retag --collection" in out
    assert "GENRE" not in FLAC(path)  # the files wait for retag
    code, out, _ = _run(capsys, env, "collections")
    assert "genre: Hardcore Techno | comment: dark set" in out

    code, out, _ = _run(capsys, env, "retag", "--collection", str(one))
    assert code == 0 and "Tags written to 1 files" in out and "Reload Tag" in out
    assert FLAC(path)["GENRE"] == ["Hardcore Techno"] and FLAC(path)["COMMENT"] == ["dark set"]

    # an empty text clears the collection field, files keep what they have (D-066)
    _run(capsys, env, "collection", "tags", str(one), "--genre", "")
    assert env.ctx.repo.get_collection(one)["genre"] is None
    code, out, _ = _run(capsys, env, "retag", "--collection", str(one))
    assert "1 already up to date" in out and FLAC(path)["GENRE"] == ["Hardcore Techno"]


def test_settings(capsys, env):
    code, out, _ = _run(capsys, env, "settings")
    assert code == 0 and "tags_from_source = overwrite" in out and "library_dir = " in out
    assert _run(capsys, env, "settings", "tags_from_source", "fill")[0] == 0
    assert env.ctx.settings().tags_from_source == "fill"
    code, _, err = _run(capsys, env, "settings", "tags_from_source", "always")
    assert code == 1 and "fill, overwrite, off" in err
    code, _, err = _run(capsys, env, "settings", "max_attempts", "0")
    assert code == 1 and "between 1 and 100" in err
    code, _, err = _run(capsys, env, "settings", "library_dir", "relative\\folder")
    assert code == 1 and "full path" in err
    code, _, err = _run(capsys, env, "settings", "password", "x")
    assert code == 1 and "Unknown setting" in err
