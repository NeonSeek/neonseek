"""cli commands behind window functions: themes, folder, collection info, about, search."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from neonseek.cli import main, utf8_when_redirected

LOCALE = Path(__file__).resolve().parents[1] / "src" / "neonseek" / "locales" / "pl.json"


def _run(capsys, data, *argv):
    code = main(["--data-dir", str(data), *argv])
    out, err = capsys.readouterr()
    return code, out, err


def _import(capsys, data, csv_dir, name="comma_seconds.csv"):
    assert main(["--data-dir", str(data), "import", "csv", str(csv_dir / name)]) == 0
    capsys.readouterr()


def test_themes_and_the_theme_setting(capsys, tmp_path):
    data = tmp_path / "data"
    code, out, _ = _run(capsys, data, "themes")
    assert code == 0 and "* neon" in out and "daylight" in out and "ice" in out
    assert _run(capsys, data, "settings", "theme", "phosphor")[0] == 0
    assert "* phosphor" in _run(capsys, data, "themes")[1]
    code, _, err = _run(capsys, data, "settings", "theme", "gone")
    assert code == 1 and "There is no theme gone" in err


def test_folder_and_collection_info(capsys, tmp_path, csv_dir):
    data = tmp_path / "data"
    _import(capsys, data, csv_dir)
    code, _, err = _run(capsys, data, "folder", "--collection", "1")
    assert code == 1 and "library" in err
    library = tmp_path / "lib"
    _run(capsys, data, "settings", "library_dir", str(library))
    assert _run(capsys, data, "folder")[1].strip() == str(library)
    assert _run(capsys, data, "folder", "--collection", "1")[1].strip() == str(library)
    code, out, _ = _run(capsys, data, "collection", "info", "1")
    assert code == 0 and "[1] comma_seconds" in out and "CSV file" in out
    assert "comma_seconds.csv" in out and "Selected: no" in out


def test_tracks_search_and_whole_library(capsys, tmp_path, csv_dir):
    data = tmp_path / "data"
    _import(capsys, data, csv_dir)
    _import(capsys, data, csv_dir, "semicolon_mmss_cp1250.csv")
    code, out, _ = _run(capsys, data, "tracks", "--all")
    assert code == 0 and out.startswith("Whole library")
    everything = out
    code, out, _ = _run(capsys, data, "tracks", "--all", "--search", "zzz-nothing")
    assert code == 0 and "No tracks." in out
    code, out, _ = _run(capsys, data, "tracks", "--collection", "1", "--search", "midnight")
    rows = [line for line in out.splitlines()[2:] if line.strip()[:1].isdigit()]
    assert len(rows) == 1 and "Midnight" in rows[0]
    code, _, err = _run(capsys, data, "tracks")
    assert code == 1 and "--collection ID, or use --all" in err
    assert len(everything.splitlines()) > 3


def test_about_shows_versions_and_licenses(capsys, tmp_path, fake_sockseek):
    fake_sockseek([{"out": "3.0.5"}, {"exit": 0}])
    code, out, _ = _run(capsys, tmp_path / "data", "about")
    assert code == 0 and "Sockseek 3.0.5" in out and "AGPL-3.0" in out and "OFL-1.1" in out
    assert "GPL-3.0-or-later" in out
    assert "Source code of this version: " in out and "/tree/v" in out and "LGPL-3.0" in out
    assert "THIRD_PARTY_LICENSES" in out
    # D-265: the project page and the donation page
    assert "Project page: https://github.com/NeonSeek/neonseek\n" in out
    assert "Support NeonSeek: https://buymeacoffee.com/neonseek\n" in out
    assert "Source code of this version: https://github.com/NeonSeek/neonseek/tree/v" in out
    fake_sockseek([{"out": "3.0.5"}, {"exit": 0}])
    code, out, _ = _run(capsys, tmp_path / "data", "about", "--diagnostics")
    assert code == 0 and "Soulseek login saved: no" in out


def test_text_size_setting(capsys, tmp_path):
    data = tmp_path / "data"
    assert "ui_scale = 100" in _run(capsys, data, "settings")[1]
    assert _run(capsys, data, "settings", "ui_scale", "125%")[0] == 0
    assert _run(capsys, data, "settings", "ui_scale")[1].strip() == "125"
    code, _, err = _run(capsys, data, "settings", "ui_scale", "133")
    assert code == 1 and "90%, 100%, 110%, 125%, 150%" in err


def test_redirected_output_is_utf8_on_any_code_page():
    # D-260: a pipe or a file gets utf-8, a console keeps writing by itself
    piped = io.TextIOWrapper(io.BytesIO(), encoding="cp1250")
    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1250")
    console.isatty = lambda: True
    utf8_when_redirected(piped, console)
    assert piped.encoding == "utf-8" and piped.errors == "replace"
    assert console.encoding == "cp1250" and console.errors == "replace"


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_the_cli_writes_utf8_into_a_pipe(tmp_path, stream):
    # the python of python -m neonseek, without the variables that would force utf-8: the
    # frozen exe ignores them anyway
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    data = tmp_path / "data"
    base = [sys.executable, "-m", "neonseek", "--data-dir", str(data)]
    subprocess.run([*base, "settings", "language", "pl"], env=env, capture_output=True,
                   timeout=120, check=True)  # fmt: skip
    command = ["collections"] if stream == "stdout" else ["tracks"]
    done = subprocess.run([*base, *command], env=env, capture_output=True, timeout=120)
    text = getattr(done, stream).decode("utf-8")
    expected = (
        "No collections yet. Import a CSV file or add a Spotify link."
        if stream == "stdout"
        else "Name a collection with --collection ID, or use --all."
    )
    translated = json.loads(LOCALE.read_text(encoding="utf-8"))["messages"][expected]
    assert translated in text and translated.isascii() is False
