"""csv acceptance for phase 1, through the cli and without any spotify setup."""

from __future__ import annotations

import shutil

import pytest

from neonseek import actions, config
from neonseek.cli import main


def _run(capsys, data_dir, *argv) -> tuple[int, str, str]:
    code = main(["--data-dir", str(data_dir), *argv])
    out, err = capsys.readouterr()
    return code, out, err


def _dump(data_dir) -> dict[str, list[tuple]]:
    ctx = actions.AppContext(data_dir)
    conn = ctx.repo.conn
    dump = {
        table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2")]
        for table in ("collections", "tracks", "collection_tracks", "downloads")
    }
    ctx.close()
    return dump


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


def test_typical_headers_import_without_questions(capsys, data_dir, csv_dir):
    code, out, err = _run(
        capsys, data_dir, "import", "csv", str(csv_dir / "exportify_comma_ms.csv")
    )
    assert code == 0, err
    assert 'Imported "exportify_comma_ms" as collection 1: 4 tracks (4 new' in out
    # nothing about spotify was configured or needed
    assert not config.settings_path(data_dir).exists()

    code, out, _ = _run(capsys, data_dir, "collections")
    assert code == 0
    # every collection starts unselected, the user picks what to download
    assert "csv" in out and "0/4" in out and "[ ]" in out and "exportify_comma_ms" in out


def test_unusual_headers_need_map(capsys, data_dir, csv_dir):
    path = str(csv_dir / "custom_headers.csv")
    code, _, err = _run(capsys, data_dir, "import", "csv", path)
    assert code == 1
    assert "track titles" in err and '--map "title=' in err
    assert _dump(data_dir)["collections"] == []

    code, out, err = _run(
        capsys,
        data_dir,
        "import",
        "csv",
        path,
        "--name",
        "Odd",
        "--map",
        "title=Piece,artist=Performer",
        "--map",
        "length=Secs,isrc=Code",
    )
    assert code == 0, err
    assert 'Imported "Odd" as collection 1: 2 tracks' in out
    tracks = _dump(data_dir)["tracks"]
    assert {t[3] for t in tracks} == {"Midnight Drive", "Glass Hearts"}


def test_same_content_changes_nothing(capsys, data_dir, csv_dir, tmp_path):
    path = tmp_path / "list.csv"
    shutil.copyfile(csv_dir / "custom_headers.csv", path)
    _run(capsys, data_dir, "import", "csv", str(path), "--map", "title=Piece,artist=Performer")
    before = _dump(data_dir)

    # the stored mapping is reused, no --map needed
    code, out, err = _run(capsys, data_dir, "import", "csv", str(path))
    assert code == 0, err
    assert "up to date, nothing changed" in out
    code, out, _ = _run(capsys, data_dir, "reimport", "1")
    assert code == 0 and "up to date" in out
    assert _dump(data_dir) == before


def test_changed_content_replaces_list_and_keeps_tracks(capsys, data_dir, csv_dir, tmp_path):
    path = tmp_path / "list.csv"
    shutil.copyfile(csv_dir / "comma_seconds.csv", path)
    _run(capsys, data_dir, "import", "csv", str(path), "--name", "Mine")
    first = _dump(data_dir)
    assert len(first["collection_tracks"]) == 5
    assert len(first["tracks"]) == 4  # midnight drive is listed twice

    path.write_text(
        "Artist,Title,Album,Length\nNeon Arcade,Midnight Drive,Night Shift,246\nNew,Song,,100\n",
        encoding="utf-8",
    )
    code, out, err = _run(capsys, data_dir, "reimport", "1")
    assert code == 0, err
    assert 'Updated "Mine" as collection 1: 2 tracks (1 new, 1 already in the library)' in out
    assert "3 tracks are no longer in this list" in out
    second = _dump(data_dir)
    assert len(second["collections"]) == 1
    assert len(second["collection_tracks"]) == 2
    # old tracks and their download rows stay
    assert len(second["tracks"]) == 5
    assert len(second["downloads"]) == 5


def test_same_file_by_other_spelling_is_one_collection(capsys, data_dir, csv_dir, tmp_path):
    path = tmp_path / "list.csv"
    shutil.copyfile(csv_dir / "comma_seconds.csv", path)
    _run(capsys, data_dir, "import", "csv", str(path))
    other_spelling = str(path).upper() if str(path)[0].isalpha() else str(path)
    code, out, _ = _run(capsys, data_dir, "import", "csv", other_spelling)
    assert code == 0
    assert "up to date" in out
    assert len(_dump(data_dir)["collections"]) == 1


def test_missing_file_keeps_collection_and_asks_for_file(capsys, data_dir, csv_dir, tmp_path):
    path = tmp_path / "list.csv"
    shutil.copyfile(csv_dir / "comma_seconds.csv", path)
    _run(capsys, data_dir, "import", "csv", str(path))
    moved = tmp_path / "moved.csv"
    path.rename(moved)

    code, _, err = _run(capsys, data_dir, "reimport", "1")
    assert code == 1
    assert "no longer at" in err and "--collection 1" in err
    assert len(_dump(data_dir)["collections"]) == 1

    code, out, err = _run(capsys, data_dir, "import", "csv", str(moved), "--collection", "1")
    assert code == 0, err
    assert "up to date" not in out  # the path changed, so the collection was updated
    row = _dump(data_dir)["collections"][0]
    assert row[4] == str(moved.resolve())


def test_import_missing_file(capsys, data_dir, tmp_path):
    code, _, err = _run(capsys, data_dir, "import", "csv", str(tmp_path / "nope.csv"))
    assert code == 1
    assert "does not exist" in err


def test_preview_imports_nothing(capsys, data_dir, csv_dir):
    code, out, _ = _run(
        capsys, data_dir, "import", "csv", str(csv_dir / "semicolon_mmss_cp1250.csv"), "--preview"
    )
    assert code == 0
    assert "cp1250" in out and "Length unit: clock" in out
    assert "Artysta Ą; Artysta Ę - Łódź Dub (4:05)" in out
    assert "Tracks in file: 4" in out
    assert _dump(data_dir)["collections"] == []


def test_dedup_across_csv_files(capsys, data_dir, csv_dir):
    _run(capsys, data_dir, "import", "csv", str(csv_dir / "exportify_comma_ms.csv"))
    code, out, _ = _run(
        capsys, data_dir, "import", "csv", str(csv_dir / "semicolon_mmss_cp1250.csv")
    )
    assert code == 0
    # kraków nights and midnight drive already exist with ids, csv rows without ids join them
    assert "4 tracks (2 new, 2 already in the library)" in out
    assert len(_dump(data_dir)["tracks"]) == 6


def test_select_deselect_remove(capsys, data_dir, csv_dir):
    _run(capsys, data_dir, "import", "csv", str(csv_dir / "no_length.csv"))
    code, _, _ = _run(capsys, data_dir, "deselect", "1")
    assert code == 0
    assert _dump(data_dir)["collections"][0][12] == 0
    _run(capsys, data_dir, "select", "1")
    assert _dump(data_dir)["collections"][0][12] == 1

    code, out, _ = _run(capsys, data_dir, "remove", "1")
    assert code == 0 and "files stay" in out
    dump = _dump(data_dir)
    assert dump["collections"] == [] and dump["collection_tracks"] == []
    assert len(dump["tracks"]) == 2 and len(dump["downloads"]) == 2

    code, _, err = _run(capsys, data_dir, "select", "7")
    assert code == 1 and "no collection with id 7" in err


def test_bad_map_syntax(capsys, data_dir, csv_dir):
    code, _, err = _run(
        capsys, data_dir, "import", "csv", str(csv_dir / "no_length.csv"), "--map", "title"
    )
    assert code == 1 and "field=column" in err


def test_empty_collection_list(capsys, data_dir):
    code, out, _ = _run(capsys, data_dir, "collections")
    assert code == 0 and "No collections yet" in out


def test_tracks_lists_a_collection_with_states(capsys, csv_dir, tmp_path):
    data = tmp_path / "data"
    assert main(["--data-dir", str(data), "import", "csv", str(csv_dir / "comma_seconds.csv")]) == 0
    capsys.readouterr()
    assert main(["--data-dir", str(data), "tracks", "--collection", "1"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "[1] comma_seconds"
    assert "not downloaded" in out and "tracks" in out.splitlines()[1]
    assert main(["--data-dir", str(data), "tracks", "--collection", "1", "--status", "done"]) == 0
    assert "No tracks." in capsys.readouterr().out
    assert main(["--data-dir", str(data), "tracks", "--collection", "9"]) == 1
    assert "no collection with id 9" in capsys.readouterr().err


def test_collections_as_json_for_scripts(capsys, data_dir, csv_dir):
    # D-254: a hidden option the smoke test reads, the same in every language
    import json

    from neonseek import i18n

    assert _run(capsys, data_dir, "collections", "--json") == (0, "[]\n", "")
    _run(capsys, data_dir, "import", "csv", str(csv_dir / "exportify_comma_ms.csv"))
    i18n.set_language("pl")
    code, out, _ = _run(capsys, data_dir, "collections", "--json")
    assert code == 0 and out.isascii()
    [row] = json.loads(out)
    assert row["kind"] == "csv" and row["name"] == "exportify_comma_ms"
    assert row["tracks"] == 4 and row["done"] == 0 and row["read"] is True
    assert row["selected"] is False and row["readable"] is True
    assert row["entries"] == 4


def test_json_counts_repeats_in_a_list_apart(capsys, data_dir, tmp_path):
    # D-259: a list with the same track twice has more places than different tracks
    import json

    lines = ["Title,Artist", "One,Band", "Two,Band", "One,Band", "Three,Band"]
    path = tmp_path / "repeats.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _run(capsys, data_dir, "import", "csv", str(path))
    [row] = json.loads(_run(capsys, data_dir, "collections", "--json")[1])
    assert (row["tracks"], row["entries"]) == (3, 4)
    with pytest.raises(SystemExit):
        main(["--data-dir", str(data_dir), "collections", "--help"])
    assert "--json" not in capsys.readouterr().out
