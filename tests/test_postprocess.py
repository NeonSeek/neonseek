"""verify, cover, place, playlists and the search budget, without sockseek."""

from __future__ import annotations

import sqlite3
import struct

import pytest
from mutagen.flac import FLAC, Picture

from audio_files import JPEG_HEADER, PNG_1X1, aiff_bytes, mp3_bytes, wav_bytes, write_audio
from neonseek import config
from neonseek.covers import CoverCache
from neonseek.db import repo as db
from neonseek.pipeline import playlists, search_budget
from neonseek.pipeline.postprocess.base import StepContext, StepError, TrackFile
from neonseek.pipeline.postprocess.cover import EmbedCover, embed_cover, has_cover
from neonseek.pipeline.postprocess.place import Place, safe_name
from neonseek.pipeline.postprocess.verify import Verify
from neonseek.sources import ingest
from neonseek.sources.base import CollectionRecord, TrackRecord


class NoHttp:
    def get(self, url, **kwargs):
        raise AssertionError(f"no download expected: {url}")


@pytest.fixture
def step_ctx(repo, tmp_path) -> StepContext:
    warnings = []
    return StepContext(
        repo=repo,
        settings=config.Settings(),
        library_dir=tmp_path / "lib",
        covers=CoverCache(tmp_path / "covers", NoHttp()),
        warn=lambda step, tid, msg: warnings.append((step, tid, msg)),
        claim_path=lambda tid, path: repo.update_download(tid, {"file_path": str(path)}),
    )


def add_track(repo, title="Song", artist="Artist", seconds=60, cover_url=None) -> int:
    record = CollectionRecord(
        kind="csv", origin="file", name=f"c-{title}", source_ref=f"c-{title}",
        snapshot_id=title,
        tracks=[TrackRecord(title=title, artists=(artist,) if artist else (),
                            duration_ms=seconds * 1000 if seconds else None,
                            cover_url=cover_url)],
    )  # fmt: skip
    cid = ingest.ingest_collection(repo, record).collection_id
    return repo.collection_track_ids(cid)[0]


# verify


@pytest.mark.parametrize("name", ["x.flac", "x.wav", "x.aiff"])
def test_verify_reads_real_format_and_length(step_ctx, tmp_path, name):
    tid = add_track(step_ctx.repo, seconds=60)
    path = write_audio(tmp_path / name, 61.5)
    result = Verify().process(TrackFile(tid, path), step_ctx)
    assert result.format == name.split(".")[1] and result.length_verified is True
    assert result.duration_ms == 61500 and result.sample_rate in (44100, 8000)


def test_verify_goes_by_content_not_extension(step_ctx, tmp_path):
    tid = add_track(step_ctx.repo)
    wav_named_flac = tmp_path / "x.flac"
    wav_named_flac.write_bytes(wav_bytes(60))
    assert Verify().process(TrackFile(tid, wav_named_flac), step_ctx).format == "wav"
    mp3 = tmp_path / "y.wav"
    mp3.write_bytes(mp3_bytes(60))
    with pytest.raises(StepError, match="MP3"):
        Verify().process(TrackFile(tid, mp3), step_ctx)


def test_verify_rejects_compressed_wav_and_aiff(step_ctx, tmp_path):
    tid = add_track(step_ctx.repo)
    mp3_in_wav = tmp_path / "x.wav"
    mp3_in_wav.write_bytes(wav_bytes(60, format_tag=0x55))
    with pytest.raises(StepError, match="compressed"):
        Verify().process(TrackFile(tid, mp3_in_wav), step_ctx)
    ulaw = bytearray(aiff_bytes(60))
    # turn it into aiff-c with ulaw: form type and a compression type after the comm fields
    comm = ulaw.index(b"COMM")
    ulaw[8:12] = b"AIFC"
    ulaw[comm + 4 : comm + 8] = struct.pack(">L", 22)
    ulaw[comm + 26 : comm + 26] = b"ulaw"
    ulaw[4:8] = struct.pack(">L", len(ulaw) - 8)
    path = tmp_path / "x.aiff"
    path.write_bytes(bytes(ulaw))
    with pytest.raises(StepError, match="ulaw"):
        Verify().process(TrackFile(tid, path), step_ctx)


def test_verify_length_and_unknown_length(step_ctx, tmp_path):
    tid = add_track(step_ctx.repo, seconds=60)
    with pytest.raises(StepError, match="Wrong version"):
        Verify().process(TrackFile(tid, write_audio(tmp_path / "x.flac", 66)), step_ctx)
    unknown = add_track(step_ctx.repo, title="No Length", seconds=None)
    result = Verify().process(TrackFile(unknown, write_audio(tmp_path / "y.flac", 66)), step_ctx)
    assert result.length_verified is False and result.duration_ms == 66000


def test_verify_damaged_file(step_ctx, tmp_path):
    tid = add_track(step_ctx.repo)
    path = tmp_path / "x.flac"
    path.write_bytes(b"fLaC" + b"\x00" * 10)
    with pytest.raises(StepError, match="damaged"):
        Verify().process(TrackFile(tid, path), step_ctx)


# cover


@pytest.mark.parametrize(
    ("name", "fmt"), [("x.flac", "flac"), ("x.wav", "wav"), ("x.aiff", "aiff")]
)
def test_cover_is_embedded_once(step_ctx, tmp_path, name, fmt):
    url = "https://covers.test/a.png"
    cached = step_ctx.covers.directory / f"{CoverCache.key(url)}.png"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(PNG_1X1)
    tid = add_track(step_ctx.repo, cover_url=url)
    path = write_audio(tmp_path / name, 60)
    EmbedCover().process(TrackFile(tid, path, format=fmt), step_ctx)
    assert has_cover(path, fmt)
    # an existing cover is never replaced
    cached.write_bytes(JPEG_HEADER)
    before = path.read_bytes()
    EmbedCover().process(TrackFile(tid, path, format=fmt), step_ctx)
    assert path.read_bytes() == before


def test_existing_flac_cover_stays(tmp_path):
    path = write_audio(tmp_path / "x.flac", 60)
    audio = FLAC(path)
    picture = Picture()
    picture.mime, picture.data, picture.type = "image/jpeg", JPEG_HEADER, 3
    audio.add_picture(picture)
    audio.save()
    assert has_cover(path, "flac")


def test_webp_cover_is_not_embedded(tmp_path):
    path = write_audio(tmp_path / "x.flac", 60)
    with pytest.raises(StepError, match="JPEG or PNG"):
        embed_cover(path, "flac", b"RIFF\0\0\0\0WEBPVP8 ")


# place


def test_safe_name_for_windows():
    assert safe_name('AC/DC: "Back" <in> Black?*|') == "AC_DC_ _Back_ _in_ Black___"
    assert safe_name("CON") == "CON_" and safe_name("nul.txt") == "nul.txt_"
    assert safe_name("  dots...  ") == "dots"
    assert safe_name("") == "track"
    assert len(safe_name("x" * 400)) == 150


def test_place_moves_and_avoids_collisions(step_ctx, tmp_path):
    repo = step_ctx.repo
    one = add_track(repo, title="Same", artist="Artist", seconds=60)
    two = add_track(repo, title="Same", artist="Artist", seconds=120)
    first = Place().process(
        TrackFile(one, write_audio(tmp_path / "a.flac", 60), format="flac"), step_ctx
    )
    assert first.path == step_ctx.library_dir / "tracks" / "Artist - Same.flac"
    repo.update_download(one, {"status": "done"})
    second = Place().process(
        TrackFile(two, write_audio(tmp_path / "b.flac", 120), format="flac"), step_ctx
    )
    assert second.path.name == f"Artist - Same [{two:06d}].flac"
    assert first.path.exists() and second.path.exists()


def test_place_takes_back_its_own_interrupted_move(step_ctx, tmp_path):
    repo = step_ctx.repo
    tid = add_track(repo)
    target = step_ctx.library_dir / "tracks" / "Artist - Song.flac"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old copy from an interrupted run")
    repo.update_download(tid, {"file_path": str(target), "status": "pending"})
    result = Place().process(
        TrackFile(tid, write_audio(tmp_path / "a.flac", 60), format="flac"), step_ctx
    )
    assert result.path == target and target.read_bytes()[:4] == b"fLaC"


def test_place_never_overwrites_a_foreign_file(step_ctx, tmp_path):
    tid = add_track(step_ctx.repo)
    foreign = step_ctx.library_dir / "tracks" / "Artist - Song.aiff"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"someone else's file")
    result = Place().process(
        TrackFile(tid, write_audio(tmp_path / "a.aif", 60), format="aiff"), step_ctx
    )
    assert result.path.name == f"Artist - Song [{tid:06d}].aiff"
    assert foreign.read_bytes() == b"someone else's file"


# playlists


def test_playlist_names_of_twin_collections(repo, tmp_path):
    tracks = [TrackRecord(title="T", artists=("A",), duration_ms=61000)]
    ids = []
    for ref in ("one", "two"):
        record = CollectionRecord(kind="csv", origin="file", name="Mix: Best?", source_ref=ref,
                                  snapshot_id=ref, tracks=tracks)  # fmt: skip
        ids.append(ingest.ingest_collection(repo, record).collection_id)
    tid = repo.collection_track_ids(ids[0])[0]
    library = tmp_path / "lib"
    track_file = library / "tracks" / "A - T.flac"
    repo.update_download(tid, {"status": "done", "file_path": str(track_file),
                               "duration_ms": 60600})  # fmt: skip
    first = playlists.write_playlist(repo, ids[0], library)
    assert first.name == f"Mix_ Best_ [{ids[0]}].m3u8"
    text = first.read_bytes().decode("utf-8")
    assert text == "#EXTM3U\r\n#EXTINF:61,A - T\r\n..\\tracks\\A - T.flac\r\n"


# search budget across sockseek processes


def test_search_budget():
    plan = search_budget.plan_start
    assert plan([], 1000.0, 50) == search_budget.StartPlan(0.0)
    # old searches outside the window do not count
    assert plan([500.0], 1000.0, 50) == search_budget.StartPlan(0.0)
    # 5 used: a small pass starts now with the rest of the window only
    assert plan([990.0] * 5, 1000.0, 3) == search_budget.StartPlan(0.0, 29)
    # a bigger pass waits until the window is empty, then gets the full default
    assert plan([900.0, 990.0], 1000.0, 40) == search_budget.StartPlan(210.0)
    # nothing left in the window: wait for it to renew, never a process with a limit of 0
    assert plan([800.0] + [990.0] * 33, 1000.0, 1) == search_budget.StartPlan(210.0)
    assert plan([990.0] * 40, 1000.0, 1) == search_budget.StartPlan(210.0)
    assert search_budget.search_estimate_s(0) == 0
    assert search_budget.search_estimate_s(34) == 220
    assert search_budget.search_estimate_s(35) == 440


# schema


def test_version_1_database_migrates(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    db._migrate_v1(raw)
    raw.execute("INSERT INTO collections (kind, origin, name) VALUES ('csv', 'file', 'x')")
    raw.commit()
    raw.close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 4
    row = conn.execute("SELECT name, genre, comment FROM collections").fetchone()
    assert tuple(row) == ("x", None, None)
    assert conn.execute("SELECT COUNT(*) FROM searches").fetchone()[0] == 0
    # version 3: excluded tracks remember their status, the library move has a journal
    assert conn.execute("SELECT COUNT(*) FROM file_moves").fetchone()[0] == 0
    assert "excluded_from" in {r[1] for r in conn.execute("PRAGMA table_info(downloads)")}
    # version 4: ids of tracks on apple music, youtube and soundcloud (D-228)
    assert conn.execute("SELECT COUNT(*) FROM track_sources").fetchone()[0] == 0
    conn.close()
