from __future__ import annotations

import pytest

from neonseek.sources import csv_file
from neonseek.sources.csv_file import (
    CsvImportError,
    CsvMappingError,
    detect_length_unit,
    parse_csv,
    parse_length,
    split_artists,
)


def test_exportify_comma_ms(csv_dir):
    parsed = parse_csv(csv_dir / "exportify_comma_ms.csv")
    table = parsed.table
    assert table.delimiter == ","
    assert table.encoding == "utf-8"
    # bom is not part of the first header
    assert table.header[0] == "Track URI"
    assert parsed.column_names() == {
        "title": "Track Name",
        "artist": "Artist Name(s)",
        "album": "Album Name",
        "length": "Track Duration (ms)",
        "isrc": "ISRC",
        "spotify_id": "Track URI",
    }
    assert parsed.length_unit == "ms"
    assert len(parsed.tracks) == 4
    assert parsed.skipped_rows == 0

    first, second, third, fourth = parsed.tracks
    assert first.title == "Midnight Drive"
    assert first.artists == ("Neon Arcade",)
    assert first.album == "Night Shift"
    assert first.duration_ms == 245000
    assert first.isrc == "QZAAA2600001"
    assert first.spotify_id == "0NeonFixtureTrack00001"
    assert second.artists == ("Neon Arcade", "Lumen")
    assert second.duration_ms == 198500
    assert third.title == "Kraków Nights - Extended Mix"
    assert third.artists == ("Zażółć",)
    # url with language segment and query in the uri column
    assert fourth.spotify_id == "0NeonFixtureTrack00004"
    assert fourth.album is None
    assert fourth.isrc is None


def test_semicolon_cp1250_clock(csv_dir):
    parsed = parse_csv(csv_dir / "semicolon_mmss_cp1250.csv")
    assert parsed.table.delimiter == ";"
    assert parsed.table.encoding == "cp1250"
    assert parsed.column_names() == {
        "title": "Tytuł",
        "artist": "Wykonawca",
        "album": "Album",
        "length": "Time",
    }
    assert parsed.length_unit == "clock"
    titles = [t.title for t in parsed.tracks]
    assert titles == ["Kraków Nights - Extended Mix", "Łódź Dub", "Midnight Drive", "Godzina"]
    lengths = [t.duration_ms for t in parsed.tracks]
    assert lengths == [412000, 245000, 245000, 3723000]
    # semicolon inside a quoted cell splits artists
    assert parsed.tracks[1].artists == ("Artysta Ą", "Artysta Ę")


def test_comma_seconds_skips_rows_without_title(csv_dir):
    parsed = parse_csv(csv_dir / "comma_seconds.csv")
    assert parsed.table.delimiter == ","
    assert parsed.length_unit == "s"
    assert [t.title for t in parsed.tracks] == [
        "Midnight Drive",
        "Comma, Don't Song",
        "Glass Hearts (feat. Lumen)",
        "Only Title",
        "Midnight Drive",
    ]
    # the orphan album row counts, the blank line does not
    assert parsed.skipped_rows == 1
    assert parsed.tracks[2].duration_ms == 198500
    assert parsed.tracks[3].duration_ms is None
    assert parsed.tracks[3].album is None
    assert parsed.bad_lengths == 0


def test_no_length_column(csv_dir):
    parsed = parse_csv(csv_dir / "no_length.csv")
    assert parsed.column_names() == {"title": "Song", "artist": "Artists"}
    assert parsed.length_unit is None
    assert all(t.duration_ms is None for t in parsed.tracks)
    assert parsed.tracks[1].artists == ("Neon Arcade", "Lumen")


def test_tab_and_median_ms(csv_dir):
    parsed = parse_csv(csv_dir / "tab_duration_ms.tsv")
    assert parsed.table.delimiter == "\t"
    assert parsed.column_names()["title"] == "Name"
    assert parsed.length_unit == "ms"
    assert [t.duration_ms for t in parsed.tracks] == [245000, 15000, 600000]


def test_length_unit_override(csv_dir):
    parsed = parse_csv(csv_dir / "tab_duration_ms.tsv", length_unit="s")
    assert parsed.length_unit == "s"
    assert parsed.tracks[0].duration_ms == 245000 * 1000


def test_custom_headers_need_mapping(csv_dir):
    with pytest.raises(CsvMappingError) as info:
        parse_csv(csv_dir / "custom_headers.csv")
    assert info.value.columns == ["Performer", "Piece", "Secs", "Code"]

    parsed = parse_csv(
        csv_dir / "custom_headers.csv",
        {"title": "piece", "artist": "Performer", "length": "Secs", "isrc": "code"},
    )
    assert parsed.column_names() == {
        "title": "Piece",
        "artist": "Performer",
        "length": "Secs",
        "isrc": "Code",
    }
    assert parsed.tracks[0].title == "Midnight Drive"
    assert parsed.tracks[0].duration_ms == 245000
    assert parsed.tracks[0].isrc == "QZAAA2600001"


def test_override_takes_column_from_automatic_field(csv_dir):
    # "Album Name" auto maps to album, a manual title choice moves it
    parsed = parse_csv(csv_dir / "exportify_comma_ms.csv", {"title": "Album Name"})
    assert parsed.column_names()["title"] == "Album Name"
    assert "album" not in parsed.columns


def test_override_can_unmap_a_field(csv_dir):
    parsed = parse_csv(csv_dir / "comma_seconds.csv", {"length": None})
    assert parsed.length_unit is None
    assert all(t.duration_ms is None for t in parsed.tracks)


def test_bad_override_errors(csv_dir):
    with pytest.raises(CsvMappingError, match="Unknown field"):
        parse_csv(csv_dir / "comma_seconds.csv", {"bpm": "Length"})
    with pytest.raises(CsvMappingError, match="not in this file"):
        parse_csv(csv_dir / "comma_seconds.csv", {"title": "Nope"})


def test_missing_and_empty_files(tmp_path):
    with pytest.raises(CsvImportError, match="does not exist"):
        parse_csv(tmp_path / "nope.csv")
    empty = tmp_path / "empty.csv"
    empty.write_bytes(b"")
    with pytest.raises(CsvImportError, match="no header row"):
        parse_csv(empty)


def test_single_column_file(tmp_path):
    path = tmp_path / "titles.csv"
    path.write_text("Title\nFirst\nSecond\n", encoding="utf-8")
    parsed = parse_csv(path)
    assert [t.title for t in parsed.tracks] == ["First", "Second"]


def test_unreadable_length_imports_without_length(tmp_path):
    path = tmp_path / "x.csv"
    path.write_text("Title,Length\nA,abc\nB,200\n", encoding="utf-8")
    parsed = parse_csv(path)
    assert [t.duration_ms for t in parsed.tracks] == [None, 200000]
    assert parsed.bad_lengths == 1


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        ("245", "s", 245000),
        ("245.5", "s", 245500),
        ("245,5", "s", 245500),
        ("245000", "ms", 245000),
        ("4:05", "clock", 245000),
        ("4:05.5", "clock", 245500),
        ("1:02:03", "clock", 3723000),
        ("0:00", "clock", None),
        ("3:00", "clock", 180000),
        ("45", "clock", 45000),
        ("4:05", "s", 245000),
        ("", "s", None),
        ("-5", "s", None),
        ("1:2:3:4", "clock", None),
        ("a:05", "clock", None),
        ("nan", "s", None),
    ],
)
def test_parse_length(value, unit, expected):
    assert parse_length(value, unit) == expected


@pytest.mark.parametrize(
    ("header", "values", "expected"),
    [
        ("Length", ["245", "198"], "s"),
        ("Duration (ms)", ["245", "198"], "ms"),
        ("duration_ms", ["245"], "ms"),
        ("Length", ["245000", "198000"], "ms"),
        ("Time", ["4:05", "3:18", ""], "clock"),
        ("Items", ["245"], "s"),
        ("Length", [], "s"),
    ],
)
def test_detect_length_unit(header, values, expected):
    assert detect_length_unit(header, values) == expected


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("A", ("A",)),
        ("A; B", ("A", "B")),
        ("A, B", ("A", "B")),
        ("Earth, Wind & Fire", ("Earth", "Wind & Fire")),
        ("A,B", ("A,B",)),
        ("Earth, Wind & Fire; Other", ("Earth, Wind & Fire", "Other")),
        ("", ()),
    ],
)
def test_split_artists(cell, expected):
    assert split_artists(cell) == expected


def test_alias_table_has_every_field():
    assert set(csv_file.HEADER_ALIASES) == set(csv_file.FIELDS)
