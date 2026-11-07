"""download runs on the pinned sockseek with --mock-files-dir (PLAN.md section 10, phase 2).

"without searching flac again" is checked on the run.csv of pass 1: the track is not there.
"""

from __future__ import annotations

import mutagen
import pytest
from mutagen.flac import FLAC

from audio_files import mp3_bytes
from download_env import COVER_URL, needs_sockseek, track
from neonseek import actions
from neonseek.config import NOT_FOUND
from neonseek.engine import base as engine
from neonseek.pipeline import runner
from neonseek.pipeline.postprocess import riff_info

pytestmark = needs_sockseek

BIG = 15 * 1024 * 1024  # with --mock-files-slow about 7 s, a flac block holds at most 16 mb


def finished(env, status: str) -> set[int]:
    return {e.track_id for e in env.events if isinstance(e, runner.TrackFinished)
            and e.status == status}  # fmt: skip


def test_test_collection_end_to_end(env):
    f = env.file
    f("Alpha Artist/First Album/01 - First Song.flac", 60)
    f("Alpha Artist/First Album/01 - First Song.wav", 60)
    # the wav sits in a folder named like the album, the flac does not
    f("Beta Artist/random rips/02 - Second Song.flac", 61)
    f("Beta Artist/Second Album/02 - Second Song.wav", 61)
    f("Gamma Artist/Third Album/03 - Third Song.wav", 50)
    f("Delta Artist/Fourth Album/04 - Fourth Song.aiff", 45)
    f("Epsilon Artist/Fifth Album/05 - Fifth Song.aif", 47)
    f("Zeta Artist/Sixth Album/06 - Sixth Song.mp3", 52)
    f("Eta Artist/Seventh Album/07 - Seventh Song.m4a", 53)
    f("Theta Artist/Eighth Album/08 - Eighth Song.flac", 58)
    f("Iota Artist/Ninth Album/09 - Ninth Song.flac", 41)
    f("Kappa Artist/Zażółć Album/10 - Café Ünïcode Song.flac", 46)
    f("Lambda Artist/Other/11 - Other Song.flac", 40)
    tracks = [
        track("Alpha Artist", "First Song", "First Album", 60, cover_url=COVER_URL,
              album_artists=("Alpha Artist",), release_date="2021-03-04", track_number=1,
              disc_number=1, isrc="USAAA2100001"),
        track("Beta Artist", "Second Song", "Second Album", 61),
        track("Gamma Artist", "Third Song", "Third Album", 50, cover_url=COVER_URL),
        track("Delta Artist", "Fourth Song", "Fourth Album", 45, cover_url=COVER_URL),
        track("Epsilon Artist", "Fifth Song", "Fifth Album", 47),
        track("Zeta Artist", "Sixth Song", "Sixth Album", 52),
        track("Eta Artist", "Seventh Song", "Seventh Album", 53),
        track("Theta Artist", "Eighth Song", "Eighth Album", 58),
        track("Iota Artist", "Ninth Song", "Ninth Album", None),  # length unknown
        track("Kappa Artist", "Café Ünïcode Song", "Zażółć Album", 46),
        track("Mu Artist", "Missing Song", "Nowhere", 60),
    ]  # fmt: skip
    cid = env.collection("Test ż", tracks, genre="Hardcore Techno")
    other = env.collection("Other", [track("Lambda Artist", "Other Song", "Other", 40)])
    ids = env.ids(cid)
    first, second, third, fourth, fifth, sixth, seventh, _, ninth, tenth, missing = ids

    outcome = env.run([cid])
    assert outcome.ended == runner.COMPLETED
    state = {tid: env.state(tid) for tid in ids}
    assert {tid for tid in ids if state[tid]["status"] == "done"} == {
        first,
        second,
        third,
        fourth,
        fifth,
        ids[7],
        ninth,
        tenth,
    }
    # flac wins even against a wav in a better named folder
    assert state[first]["format"] == "flac" and state[second]["format"] == "flac"
    assert state[third]["format"] == "wav" and state[third]["stage"] == "fallback"
    assert state[fourth]["format"] == "aiff" and state[fifth]["format"] == "aiff"
    # lossy and m4a never get in, and are final not_found after the fallback pass
    for tid in (sixth, seventh, missing):
        assert (state[tid]["status"], state[tid]["stage"]) == ("not_found", "fallback")
    # the other collection was not part of the run
    assert env.state(env.ids(other)[0])["status"] == "pending"
    assert "Lambda Artist - Other Song.flac" not in env.library_files()

    files = env.library_files()
    assert files == sorted(
        [
            "Alpha Artist - First Song.flac",
            "Beta Artist - Second Song.flac",
            "Delta Artist - Fourth Song.aiff",
            "Epsilon Artist - Fifth Song.aiff",
            "Gamma Artist - Third Song.wav",
            "Iota Artist - Ninth Song.flac",
            "Kappa Artist - Café Ünïcode Song.flac",
            "Theta Artist - Eighth Song.flac",
        ]
    )
    for tid in ids:
        if state[tid]["status"] == "done":
            path = state[tid]["file_path"]
            audio = mutagen.File(path)
            assert abs(audio.info.length * 1000 - state[tid]["file_duration_ms"]) < 5
    assert state[ninth]["length_verified"] == 0 and state[first]["length_verified"] == 1

    # cover and tags
    alpha = FLAC(state[first]["file_path"])
    assert alpha.pictures and alpha.pictures[0].mime == "image/png"
    assert alpha["TITLE"] == ["First Song"] and alpha["GENRE"] == ["Hardcore Techno"]
    assert alpha["ALBUMARTIST"] == ["Alpha Artist"] and alpha["DATE"] == ["2021-03-04"]
    assert alpha["ISRC"] == ["USAAA2100001"] and alpha["TRACKNUMBER"] == ["1"]
    with open(state[first]["file_path"], "rb") as raw:
        assert raw.read(4) == b"fLaC"  # never id3 in flac
    gamma = mutagen.File(state[third]["file_path"])
    assert gamma.tags.getall("APIC") and str(gamma.tags["TCON"]) == "Hardcore Techno"
    assert riff_info.read_info(state[third]["file_path"])["IGNR"] == "Hardcore Techno"
    delta = mutagen.File(state[fourth]["file_path"])
    assert delta.tags.getall("APIC") and delta.tags.version[:2] == (2, 3)

    # the playlist: relative paths, source order, done tracks only, utf-8
    playlist = env.library / "playlists" / "Test ż.m3u8"
    assert playlist in outcome.playlists
    lines = playlist.read_text(encoding="utf-8").splitlines()
    paths = [line for line in lines if line and not line.startswith("#")]
    assert paths[0] == "..\\tracks\\Alpha Artist - First Song.flac"
    assert "..\\tracks\\Kappa Artist - Café Ünïcode Song.flac" in paths
    assert len(paths) == 8
    assert not (env.library / ".staging").exists()

    # a second run downloads nothing again: nothing is left for either pass
    again = env.run([cid])
    assert not [e for e in env.events if isinstance(e, runner.PassStarted)]
    assert again.ended == runner.COMPLETED
    assert env.library_files() == files


def test_failed_flac_download_stays_failed_and_gets_no_wav(env):
    env.file("Alpha Artist/First Album/01 - First Song.flac", 60)
    env.file("Alpha Artist/First Album/01 - First Song.wav", 60)
    cid = env.collection("Fail", [track("Alpha Artist", "First Song", "First Album", 60)])
    (tid,) = env.ids(cid)
    env.flags["flac"] = ["--mock-files-fail-downloads", "1"]
    outcome = env.run([cid])
    row = env.state(tid)
    assert (row["status"], row["stage"], row["attempts"]) == ("failed", "flac", 1)
    assert [e.number for e in env.events if isinstance(e, runner.PassStarted)] == [1]
    assert env.library_files() == []
    assert outcome.counts == {"failed": 1}

    # the next run tries flac again, and gets it
    env.flags["flac"] = []
    env.run([cid])
    row = env.state(tid)
    assert (row["status"], row["format"]) == ("done", "flac")


def test_flac_failures_up_to_the_limit_then_wav(env):
    env.file("Alpha Artist/First Album/01 - First Song.flac", 60)
    env.file("Alpha Artist/First Album/01 - First Song.wav", 60)
    cid = env.collection("Fail", [track("Alpha Artist", "First Song", "First Album", 60)])
    (tid,) = env.ids(cid)
    env.set("max_attempts", "2")
    env.flags["flac"] = ["--mock-files-fail-downloads", "1"]
    env.run([cid])
    assert env.state(tid)["attempts"] == 1 and env.state(tid)["status"] == "failed"
    env.run([cid])
    row = env.state(tid)
    # the second failure used up the attempts, so the same run went on to the wav pass
    assert (row["status"], row["format"], row["stage"]) == ("done", "wav", "fallback")


def test_interrupted_in_pass_1(env):
    env.file("Wav Artist/Wav Album/01 - Wav Only.wav", 50)
    env.file("Slow Artist/Slow Album/02 - Slow Song.flac", 60, pad=BIG)
    cid = env.collection(
        "Cut",
        [track("Wav Artist", "Wav Only", "Wav Album", 50),
         track("Slow Artist", "Slow Song", "Slow Album", 60)],
    )  # fmt: skip
    wav_only, slow = env.ids(cid)
    env.flags["flac"] = ["--mock-files-slow"]
    seen: dict[str, bool] = {}

    def cancel_when(event) -> bool:
        if isinstance(event, runner.TrackFinished) and event.track_id == wav_only:
            seen["not_found"] = True
        if isinstance(event, engine.ItemDownloading) and event.track_id == slow:
            seen["downloading"] = True
        return len(seen) == 2

    outcome = env.run([cid], cancel_when=cancel_when)
    assert outcome.ended == runner.CANCELLED
    assert [e.number for e in env.events if isinstance(e, runner.PassStarted)] == [1]
    assert (env.state(wav_only)["status"], env.state(wav_only)["stage"]) == ("not_found", "flac")
    slow_row = env.state(slow)
    assert (slow_row["status"], slow_row["stage"], slow_row["attempts"]) == ("pending", "flac", 0)
    assert not (env.library / ".staging").exists()

    env.flags["flac"] = []
    again = env.run([cid])
    # pass 1 only searched the interrupted track, the wav-only one went straight to pass 2
    assert env.titles(again.run_id, 1) == ["Slow Song"]
    assert env.titles(again.run_id, 2) == ["Wav Only"]
    assert env.state(wav_only)["format"] == "wav" and env.state(slow)["format"] == "flac"


def test_interrupted_in_pass_2(env):
    env.file("Wav Artist/Wav Album/01 - Wav Only.wav", 50, pad=BIG)
    cid = env.collection("Cut2", [track("Wav Artist", "Wav Only", "Wav Album", 50)])
    (tid,) = env.ids(cid)
    env.flags["fallback"] = ["--mock-files-slow"]
    outcome = env.run(
        [cid], cancel_when=lambda e: isinstance(e, engine.ItemDownloading) and e.track_id == tid
    )
    assert outcome.ended == runner.CANCELLED
    row = env.state(tid)
    assert (row["status"], row["stage"], row["attempts"]) == ("pending", "fallback", 0)

    env.flags["fallback"] = []
    again = env.run([cid])
    assert env.titles(again.run_id, 1) is None  # no flac pass at all
    assert env.titles(again.run_id, 2) == ["Wav Only"]
    assert env.state(tid)["status"] == "done"


def test_failed_wav_download_is_retried_without_flac(env):
    env.file("Wav Artist/Wav Album/01 - Wav Only.wav", 50)
    cid = env.collection("WavFail", [track("Wav Artist", "Wav Only", "Wav Album", 50)])
    (tid,) = env.ids(cid)
    env.flags["fallback"] = ["--mock-files-fail-downloads", "1"]
    env.run([cid])
    row = env.state(tid)
    assert (row["status"], row["stage"], row["attempts"]) == ("failed", "fallback", 1)

    env.flags["fallback"] = []
    again = env.run([cid])
    assert env.titles(again.run_id, 1) is None
    assert env.titles(again.run_id, 2) == ["Wav Only"]
    assert env.state(tid)["format"] == "wav"


def test_setting_not_found_then_lossless(env):
    env.file("Wav Artist/Wav Album/01 - Wav Only.wav", 50)
    cid = env.collection("Policy", [track("Wav Artist", "Wav Only", "Wav Album", 50)])
    (tid,) = env.ids(cid)
    env.set("on_missing_flac", NOT_FOUND)
    first = env.run([cid])
    assert [p["pass"] for p in first.passes] == [1]
    assert (env.state(tid)["status"], env.state(tid)["stage"]) == ("not_found", "flac")

    env.set("on_missing_flac", "lossless")
    again = env.run([cid])
    # pass 1 has nothing to do, the track still goes to pass 2
    assert [p["pass"] for p in again.passes] == [2]
    assert env.state(tid)["format"] == "wav"


def test_setting_lossless_then_not_found(env):
    env.file("Wav Artist/Wav Album/01 - Wav Only.wav", 50)
    cid = env.collection("Policy2", [track("Wav Artist", "Wav Only", "Wav Album", 50)])
    (tid,) = env.ids(cid)
    env.flags["fallback"] = ["--mock-files-fail-downloads", "1"]
    env.run([cid])
    assert (env.state(tid)["status"], env.state(tid)["stage"]) == ("failed", "fallback")

    env.set("on_missing_flac", NOT_FOUND)
    env.flags["fallback"] = []
    again = env.run([cid])
    row = env.state(tid)
    assert (row["status"], row["stage"], row["attempts"]) == ("not_found", "flac", 0)
    assert again.passes == []  # not searched as flac again, and no fallback pass


def test_lossy_file_named_flac_never_gets_in(env):
    # sockseek only sees the extension, verify looks at the content
    env.mock.joinpath("Fake Artist/Fake Album").mkdir(parents=True)
    env.mock.joinpath("Fake Artist/Fake Album/01 - Fake Song.flac").write_bytes(mp3_bytes(40))
    cid = env.collection("Fake", [track("Fake Artist", "Fake Song", "Fake Album", None)])
    (tid,) = env.ids(cid)
    env.run([cid])
    row = env.state(tid)
    assert (row["status"], row["stage"], row["attempts"]) == ("failed", "flac", 1)
    assert "MP3" in row["last_error"]
    assert env.library_files() == [] and not (env.library / ".staging").exists()


def test_limit_takes_the_first_tracks(env):
    for n in range(1, 5):
        env.file(f"Artist {n}/Album/0{n} - Song {n}.flac", 40 + n)
    cid = env.collection("Limit", [track(f"Artist {n}", f"Song {n}", "Album", 40 + n)
                                   for n in range(1, 5)])  # fmt: skip
    outcome = env.run([cid], limit=2)
    assert env.titles(outcome.run_id, 1) == ["Song 1", "Song 2"]
    assert [env.state(t)["status"] for t in env.ids(cid)] == ["done", "done", "pending", "pending"]


def test_crash_recovery_keeps_stage_and_attempts(env):
    cid = env.collection("Crash", [track("A", "One", None, 40), track("B", "Two", None, 41)])
    one, two = env.ids(cid)
    repo = env.ctx.repo
    repo.update_download(one, {"status": "searching", "stage": "fallback", "attempts": 2})
    repo.update_download(two, {"status": "downloading", "stage": "flac", "attempts": 1})

    with actions.DownloadJob(env.ctx, [cid]) as job:
        assert job.plan.track_ids == [one, two]
    assert (env.state(one)["status"], env.state(one)["stage"], env.state(one)["attempts"]) == (
        "pending", "fallback", 2)  # fmt: skip
    assert (env.state(two)["status"], env.state(two)["stage"], env.state(two)["attempts"]) == (
        "pending", "flac", 1)  # fmt: skip


def test_track_in_two_collections_gets_both_genres(env):
    env.file("Alpha Artist/First Album/01 - First Song.flac", 60)
    shared = track("Alpha Artist", "First Song", "First Album", 60)
    a = env.collection("A", [shared], genre="Hardcore Techno", comment="dark")
    b = env.collection("B", [shared], genre="Industrial", comment="dark")
    env.run([b])
    (tid,) = env.ids(b)
    audio = FLAC(env.state(tid)["file_path"])
    assert audio["GENRE"] == ["Hardcore Techno, Industrial"]
    assert audio["COMMENT"] == ["dark"]
    assert env.ids(a) == [tid]


def test_pass_2_prefers_aiff_over_wav_in_the_same_folder(env):
    # without --pref-format sockseek takes the wav here (checked on 3.0.5, D-082)
    env.file("Beta Artist/Bbb Album/01 - Song Bbb.wav", 50)
    env.file("Beta Artist/Bbb Album/01 - Song Bbb.aif", 50)
    env.file("Alpha Artist/Aaa Album/01 - Song Aaa.wav", 50)
    env.file("Alpha Artist/Aaa Album/01 - Song Aaa.aiff", 50)
    cid = env.collection("Pref", [track("Beta Artist", "Song Bbb", "Bbb Album", 50),
                                  track("Alpha Artist", "Song Aaa", "Aaa Album", 50)])  # fmt: skip
    env.run([cid])
    assert [env.state(t)["format"] for t in env.ids(cid)] == ["aiff", "aiff"]
    assert env.library_files() == ["Alpha Artist - Song Aaa.aiff", "Beta Artist - Song Bbb.aiff"]


def test_overwrite_is_the_default_and_leaves_one_artist(env):
    path = env.file("Alpha Artist/First Album/01 - First Song.flac", 60)
    uploader = FLAC(path)
    uploader.add_tags()
    uploader["ARTIST"] = ["Alpha Artist", "Somebody"]
    uploader["ALBUM ARTIST"] = ["Uploader Album Artist"]
    uploader["TITLE"] = ["first song (uploader rip)"]
    uploader.save()
    record = track("Alpha Artist", "First Song", "First Album", 60, album_artists=("Alpha Artist",))
    cid = env.collection("Over", [record])
    assert env.ctx.settings().tags_from_source == "overwrite"
    env.run([cid])
    audio = FLAC(env.state(env.ids(cid)[0])["file_path"])
    assert audio["ARTIST"] == ["Alpha Artist"] and audio["TITLE"] == ["First Song"]
    assert audio["ALBUMARTIST"] == ["Alpha Artist"] and "ALBUM ARTIST" not in audio


@pytest.mark.parametrize("bad", ["--definitely-not-a-flag"])
def test_sockseek_usage_error_is_a_clear_error(env, bad):
    env.file("Alpha Artist/First Album/01 - First Song.flac", 60)
    cid = env.collection("Err", [track("Alpha Artist", "First Song", "First Album", 60)])
    env.flags["flac"] = [bad]
    outcome = env.run([cid])
    assert outcome.ended == runner.ERROR and "exit code 2" in outcome.message
    assert env.state(env.ids(cid)[0])["status"] == "pending"
