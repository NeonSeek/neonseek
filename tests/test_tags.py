"""tag step on flac, wav and aiff: modes fill, overwrite and off, collection tags, retag."""

from __future__ import annotations

import struct

import mutagen
import pytest
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.id3 import COMM, TIT2, TPE1
from mutagen.wave import WAVE

from audio_files import write_audio
from neonseek import actions, config
from neonseek.db import repo as db
from neonseek.pipeline.postprocess import riff_info
from neonseek.pipeline.postprocess.tag import TagValues, join_unique, tag_values, write_tags
from neonseek.sources import ingest
from neonseek.sources.base import CollectionRecord, TrackRecord

SOURCE = TagValues(
    title="Source Title",
    artist="Artist A, Artist B",
    album="Source Album",
    album_artist="Artist A",
    date="2021-03-04",
    track_number="7",
    disc_number="1",
    isrc="USAAA2100001",
)
USER = TagValues(**{**SOURCE.__dict__, "genre": "Hardcore Techno", "comment": "dark"})


def flac_with(tmp_path, **tags):
    path = write_audio(tmp_path / "a.flac", 30)
    audio = FLAC(path)
    audio.add_tags()
    for key, value in tags.items():
        audio[key] = [value]
    audio.save()
    return path


def id3_file(tmp_path, ext, title=None, comment=None):
    path = write_audio(tmp_path / f"a{ext}", 30)
    audio = WAVE(path) if ext == ".wav" else AIFF(path)
    audio.add_tags()
    if title:
        audio.tags.add(TIT2(encoding=3, text=[title]))
        audio.tags.add(TPE1(encoding=3, text=["Uploader Artist"]))
    if comment:
        audio.tags.add(COMM(encoding=3, lang="XXX", desc="", text=[comment]))
        audio.tags.add(COMM(encoding=3, lang="eng", desc="iTunNORM", text=["keep me"]))
    audio.save(v2_version=4)
    return path


# flac


def test_flac_fill_keeps_existing_fields(tmp_path):
    path = flac_with(tmp_path, TITLE="Uploader Title", GENRE="Old Genre")
    assert write_tags(path, "flac", USER, config.TAGS_FILL)
    audio = FLAC(path)
    assert audio["TITLE"] == ["Uploader Title"]  # filled already, kept
    assert audio["ALBUM"] == ["Source Album"] and audio["ARTIST"] == ["Artist A, Artist B"]
    assert audio["ALBUMARTIST"] == ["Artist A"] and audio["DATE"] == ["2021-03-04"]
    assert audio["TRACKNUMBER"] == ["7"] and audio["DISCNUMBER"] == ["1"]
    assert audio["ISRC"] == ["USAAA2100001"]
    # the user's genre always wins
    assert audio["GENRE"] == ["Hardcore Techno"] and audio["COMMENT"] == ["dark"]
    with open(path, "rb") as f:
        assert f.read(4) == b"fLaC"  # never id3 in flac
    # nothing new to write the second time
    assert not write_tags(path, "flac", USER, config.TAGS_FILL)


def test_flac_overwrite_and_off(tmp_path):
    path = flac_with(tmp_path, TITLE="Uploader Title", ALBUM="Uploader Album", ISRC="X")
    write_tags(path, "flac", TagValues(title="Source Title"), config.TAGS_OVERWRITE)
    audio = FLAC(path)
    # overwritten where the source has a value, kept where it has none
    assert audio["TITLE"] == ["Source Title"] and audio["ALBUM"] == ["Uploader Album"]

    path = flac_with(tmp_path, TITLE="Uploader Title")
    write_tags(path, "flac", USER, config.TAGS_OFF)
    audio = FLAC(path)
    assert audio["TITLE"] == ["Uploader Title"] and "ALBUM" not in audio
    # off is only about the source fields, collection tags are still written
    assert audio["GENRE"] == ["Hardcore Techno"]


def test_flac_album_artist_with_a_space_counts_as_filled(tmp_path):
    path = flac_with(tmp_path, **{"ALBUM ARTIST": "Someone"})
    write_tags(path, "flac", SOURCE, config.TAGS_FILL)
    assert "ALBUMARTIST" not in FLAC(path)


def test_flac_overwrite_leaves_exactly_one_value(tmp_path):
    path = flac_with(tmp_path, **{"ALBUM ARTIST": "Someone", "YEAR": "1999"})
    audio = FLAC(path)
    audio["ARTIST"] = ["Artist A", "Artist B"]  # uploader: one field per artist
    audio.tags.append(("artist", "Artist C"))
    audio.save()
    write_tags(path, "flac", SOURCE, config.TAGS_OVERWRITE)
    tags = FLAC(path).tags
    artist = [value for key, value in tags if key.upper() == "ARTIST"]
    assert artist == ["Artist A, Artist B"]
    assert tags["ALBUMARTIST"] == ["Artist A"] and "ALBUM ARTIST" not in tags
    assert tags["DATE"] == ["2021-03-04"] and "YEAR" not in tags
    assert not write_tags(path, "flac", SOURCE, config.TAGS_OVERWRITE)


def test_id3_overwrite_leaves_one_artist(tmp_path):
    path = write_audio(tmp_path / "a.aiff", 30)
    audio = AIFF(path)
    audio.add_tags()
    audio.tags.add(TPE1(encoding=3, text=["Artist X", "Artist Y"]))  # v2.4 multi value
    audio.save(v2_version=4)
    write_tags(path, "aiff", SOURCE, config.TAGS_OVERWRITE)
    assert AIFF(path).tags["TPE1"].text == ["Artist A, Artist B"]


def test_default_mode_is_overwrite():
    assert config.Settings().tags_from_source == config.TAGS_OVERWRITE


# aiff and wav


@pytest.mark.parametrize("ext", [".aiff", ".wav"])
def test_id3_fill_overwrite_off(tmp_path, ext):
    fmt = ext.lstrip(".")
    path = id3_file(tmp_path, ext, title="Uploader Title", comment="uploader comment")
    write_tags(path, fmt, USER, config.TAGS_FILL)
    tags = mutagen.File(path).tags
    assert tags.version[:2] == (2, 3)
    assert str(tags["TIT2"]) == "Uploader Title" and str(tags["TPE1"]) == "Uploader Artist"
    assert str(tags["TALB"]) == "Source Album" and str(tags["TPE2"]) == "Artist A"
    assert str(tags["TRCK"]) == "7" and str(tags["TPOS"]) == "1"
    assert str(tags["TSRC"]) == "USAAA2100001" and str(tags["TDRC"]) == "2021-03-04"
    assert str(tags["TCON"]) == "Hardcore Techno"
    plain = [f for f in tags.getall("COMM") if f.desc == ""]
    assert [(f.lang, f.text) for f in plain] == [("eng", ["dark"])]
    # other programs' comment frames stay
    assert [f.text for f in tags.getall("COMM") if f.desc == "iTunNORM"] == [["keep me"]]

    write_tags(path, fmt, TagValues(title="Source Title"), config.TAGS_OVERWRITE)
    assert str(mutagen.File(path).tags["TIT2"]) == "Source Title"

    path = id3_file(tmp_path, ext, title="Uploader Title")
    write_tags(path, fmt, USER, config.TAGS_OFF)
    tags = mutagen.File(path).tags
    assert str(tags["TIT2"]) == "Uploader Title" and "TALB" not in tags
    assert str(tags["TCON"]) == "Hardcore Techno"


def test_wav_gets_riff_info_for_rekordbox(tmp_path):
    path = write_audio(tmp_path / "a.wav", 30)
    audio_before = WAVE(path).info.length
    write_tags(path, "wav", USER, config.TAGS_FILL)
    info = riff_info.read_info(path)
    assert info == {
        "INAM": "Source Title",
        "IART": "Artist A, Artist B",
        "IPRD": "Source Album",
        "ICRD": "2021-03-04",
        "ITRK": "7",
        "IGNR": "Hardcore Techno",
        "ICMT": "dark",
    }
    # id3 is there too, and the audio did not change
    audio = WAVE(path)
    assert str(audio.tags["TIT2"]) == "Source Title"
    assert audio.info.length == audio_before
    assert riff_info.wav_is_lossless(path)


def append_info(path, entries: dict[str, bytes]) -> None:
    """an uploader's info chunk after the audio, raw bytes as other programs write them."""
    data = bytearray(path.read_bytes())
    body = b"INFO"
    for key, raw in entries.items():
        body += key.encode("ascii") + struct.pack("<I", len(raw)) + raw + b"\0" * (len(raw) & 1)
    data += b"LIST" + struct.pack("<I", len(body)) + body
    data[4:8] = struct.pack("<I", len(data) - 8)
    path.write_bytes(bytes(data))


def data_chunk(data: bytes) -> bytes:
    start = data.index(b"data")
    size = struct.unpack("<I", data[start + 4 : start + 8])[0]
    return data[start : start + 8 + size]


def test_riff_info_fill_keeps_the_uploader_and_handles_odd_sizes(tmp_path):
    path = write_audio(tmp_path / "a.wav", 30.0001)  # an odd number of samples
    append_info(path, {"INAM": "Zażółć\0".encode(), "ISFT": b"odd\0"})
    pcm_before = data_chunk(path.read_bytes())
    write_tags(path, "wav", TagValues(title="Source", genre="Techno"), config.TAGS_FILL)
    info = riff_info.read_info(path)
    assert info == {"INAM": "Zażółć", "ISFT": "odd", "IGNR": "Techno"}
    data = path.read_bytes()
    # one info chunk, now before the audio, and the audio is byte for byte the same
    assert data.count(b"LIST") == 1 and data.index(b"LIST") < data.index(b"data")
    assert data_chunk(data) == pcm_before
    assert struct.unpack("<I", data[4:8])[0] == len(data) - 8
    assert WAVE(path).info.length == pytest.approx(30, abs=0.01)


def test_riff_info_reads_windows_code_page(tmp_path):
    path = write_audio(tmp_path / "a.wav", 5)
    append_info(path, {"INAM": "Café\0".encode("cp1252")})
    assert riff_info.read_info(path) == {"INAM": "Café"}


# values from the database


def test_join_unique_keeps_order_and_drops_repeats():
    assert join_unique(["Techno", None, " techno ", "", "Industrial"]) == "Techno, Industrial"
    assert join_unique([None, "  "]) is None


def make_track(repo: db.Repo, *collections: tuple[str, str | None, str | None]) -> int:
    rec = TrackRecord(title="T", artists=("A", "B"), album="Al", album_artists=("A",),
                      release_date="2020", track_number=3, isrc="X1")  # fmt: skip
    for name, genre, comment in collections:
        record = CollectionRecord(kind="csv", origin="file", name=name, source_ref=name,
                                  snapshot_id=name, tracks=[rec])  # fmt: skip
        cid = ingest.ingest_collection(repo, record).collection_id
        with repo.transaction():
            repo.update_collection(cid, {"genre": genre, "comment": comment})
    return repo.collection_track_ids(cid)[0]


def test_track_in_two_collections(repo):
    tid = make_track(repo, ("one", "Hardcore Techno", "dark"), ("two", "Industrial", None),
                     ("three", "hardcore techno", "set 2"))  # fmt: skip
    values = tag_values(repo, tid)
    assert values.genre == "Hardcore Techno, Industrial"
    assert values.comment == "dark, set 2"
    assert values.artist == "A, B" and values.track_number == "3" and values.date == "2020"


def test_collection_without_tags_writes_nothing(repo):
    tid = make_track(repo, ("one", None, None))
    values = tag_values(repo, tid)
    assert values.genre is None and values.comment is None


def test_retag_writes_downloaded_files_only(ctx, tmp_path):
    repo = ctx.repo
    tid = make_track(repo, ("one", None, None))
    cid = repo.list_collections()[0].id
    path = write_audio(tmp_path / "lib" / "A - T.flac", 30)
    repo.update_download(tid, {"status": "done", "file_path": str(path), "format": "flac"})

    actions.set_collection_tags(ctx, cid, genre="  Hardcore   Techno ", comment="")
    assert repo.get_collection(cid)["genre"] == "Hardcore Techno"
    assert "GENRE" not in FLAC(path)  # changing the tags alone leaves files alone

    outcome = actions.retag(ctx, [cid])
    assert (outcome.written, outcome.unchanged, outcome.missing) == (1, 0, [])
    assert FLAC(path)["GENRE"] == ["Hardcore Techno"] and FLAC(path)["TITLE"] == ["T"]
    assert actions.retag(ctx, [cid]).unchanged == 1

    path.unlink()
    assert len(actions.retag(ctx, [cid]).missing) == 1
