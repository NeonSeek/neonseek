"""sockseek adapter on a fake process: mapping, timeouts, login failures, redaction."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from neonseek import config
from neonseek.engine import base
from neonseek.engine import sockseek as ss
from neonseek.secrets import SoulseekLogin

FAKE = Path(__file__).resolve().parent / "fake_sockseek.py"
LOGIN = SoulseekLogin("tester", "Pa55-secret")


def ev(kind: str, **data) -> dict:
    return {"out": json.dumps({"type": kind, "timestamp": "2026-10-07T00:00:00Z", "data": data})}


def track_list(*pairs) -> dict:
    tracks = [{"index": i, "artist": a, "title": t} for i, (a, t) in enumerate(pairs)]
    return ev("track_list", total=len(tracks), tracks=tracks)


def engine(tmp_path, steps, **settings) -> tuple[ss.SockseekEngine, Path]:
    script = tmp_path / "script.json"
    script.write_text(json.dumps(steps), encoding="utf-8")
    config_ = ss.SockseekSettings(
        login=settings.pop("login", LOGIN),
        output_dir=tmp_path / "staging",
        run_csv=tmp_path / "logs" / "run.csv",
        log_file=tmp_path / "logs" / "sockseek.log",
        formats=settings.pop("formats", ss.FLAC_FORMATS),
        command=[sys.executable, str(FAKE), str(script)],
        **settings,
    )
    return ss.SockseekEngine(config_), script


def run(eng, items):
    events = []
    result = eng.run(items, events.append)
    return result, events


ITEMS = [
    base.EngineItem(11, "Alpha", "Song (feat. X)", "Album A", 60),
    base.EngineItem(12, "Beta", "Same", "B1", 40),
    base.EngineItem(13, "Beta", "Same", "B2", 62),
    base.EngineItem(14, "Gamma", "Gone", None, None),
]


def test_maps_rows_duplicates_and_failures(tmp_path):
    steps = [
        # sockseek reports titles after --remove-ft
        track_list(("Alpha", "Song"), ("Beta", "Same"), ("Beta", "Same"), ("Gamma", "Gone")),
        {"out": "  Alpha - Song (60s)"},
        ev("search_start", artist="Alpha", title="Song"),
        ev("download_start", artist="Alpha", title="Song", username="u1", filename="a\\1.flac",
           size=1000),
        ev("download_progress", jobId="j1", bytesTransferred=500, totalBytes=1000, percent=50),
        {"file": "2.flac"},
        ev("track_state", artist="Alpha", title="Song", terminalOutcome="Succeeded",
           downloadPath="{OUT}\\2.flac", username="u1", filename="a\\1.flac"),
        # the failure of the first "Same" arrives before the success of the second one
        ev("search_start", artist="Beta", title="Same"),
        ev("search_start", artist="Beta", title="Same"),
        ev("track_state", artist="Beta", title="Same", terminalOutcome="Failed",
           failureReason="NoSearchResults"),
        {"file": "4.flac"},
        ev("track_state", artist="Beta", title="Same", terminalOutcome="Succeeded",
           downloadPath="{OUT}\\4.flac", username="u2", filename="b\\same.flac"),
        ev("track_state", artist="Gamma", title="Gone", terminalOutcome="Failed",
           failureReason="AllDownloadsFailed"),
        {"exit": 1},
    ]  # fmt: skip
    eng, script = engine(tmp_path, steps)
    result, events = run(eng, ITEMS)

    assert result.ended == base.COMPLETED and result.exit_code == 1
    assert result.unresolved == []
    done = [e for e in events if isinstance(e, base.ItemDone)]
    # {row} is the position plus 2, so 4.flac is the second "Same" (40 s vs 62 s)
    assert [(e.track_id, e.local_path.name) for e in done] == [(11, "2.flac"), (13, "4.flac")]
    assert base.ItemNotFound(12, "NoSearchResults") in events
    assert base.ItemFailed(14, "AllDownloadsFailed") in events
    progress = [e for e in events if isinstance(e, base.ItemDownloading)]
    assert progress[-1] == base.ItemDownloading(11, 50, "u1", "a\\1.flac")
    assert base.LogLine("stdout", "  Alpha - Song (60s)") in events
    assert isinstance(events[-1], base.RunFinished)

    rows = ss.read_run_csv(eng.settings.run_csv)
    assert [r["Title"] for r in rows] == ["Song (feat. X)", "Same", "Same", "Gone"]
    assert rows[3]["Length"] == "" and rows[3]["Album"] == ""
    args = json.loads(script.with_suffix(".args.json").read_text(encoding="utf-8"))
    assert "--no-listen" in args and "--user=tester" in args and "--pass=Pa55-secret" in args
    assert args[args.index("--format") + 1] == "flac"
    assert args[args.index("--name-format") + 1] == "{row}"


def test_pref_format_only_when_asked(tmp_path):
    flac, _ = engine(tmp_path, [])
    assert "--pref-format" not in flac.args()
    fallback, _ = engine(tmp_path, [], formats=ss.FALLBACK_FORMATS,
                         pref_formats=ss.FALLBACK_PREFERRED)  # fmt: skip
    args = fallback.args()
    assert args[args.index("--pref-format") + 1] == "aiff,aif"
    assert args[args.index("--format") + 1] == "wav,aiff,aif"


def test_run_csv_keeps_one_record_per_line(tmp_path):
    path = tmp_path / "run.csv"
    ss.write_run_csv(path, [base.EngineItem(1, "A,\nB", 'Ti"tle\r\nx', "Al\tbum", 61)])
    text = path.read_text(encoding="utf-8")
    assert text.count("\n") == 2
    assert ss.read_run_csv(path) == [
        {"Artist": "A, B", "Title": 'Ti"tle x', "Album": "Al bum", "Length": "61"}
    ]
    with pytest.raises(ValueError):
        ss.write_run_csv(path, [base.EngineItem(2, "A", "  ", "Album only", 10)])


def test_start_timeout_kills_and_names_a_ban(tmp_path):
    eng, _ = engine(tmp_path, [{"err": "[soulseek] Logging in as tester.."}, {"sleep": 30}],
                    start_timeout_s=0.8)  # fmt: skip
    began = time.monotonic()
    result, events = run(eng, ITEMS[:1])
    assert time.monotonic() - began < 10
    assert result.ended == base.START_TIMEOUT
    assert "banned" in result.message and "internet" in result.message
    assert result.unresolved == [11]


def test_idle_timeout_kills_a_silent_run(tmp_path):
    steps = [track_list(("Alpha", "Song")), ev("search_start", artist="Alpha", title="Song"),
             {"sleep": 30}]  # fmt: skip
    eng, _ = engine(tmp_path, steps, idle_timeout_s=0.8)
    began = time.monotonic()
    result, events = run(eng, ITEMS[:1])
    assert time.monotonic() - began < 10
    assert result.ended == base.IDLE_TIMEOUT
    assert "minutes" in result.message
    assert result.unresolved == [11]
    assert base.ItemSearching(11) in events


def test_waiting_for_the_search_limit_is_not_idle(tmp_path):
    steps = [
        track_list(("Alpha", "Song"), ("Beta", "Same")),
        ev("search_start", artist="Alpha", title="Song"),
        {"err": "[   0.39] [005] SongJob: rate limited: Beta - Same"},
        # silent while waiting, shorter than the idle limit
        {"sleep": 1.0},
        ev("search_start", artist="Beta", title="Same"),
        ev("track_state", artist="Alpha", title="Song", terminalOutcome="Failed",
           failureReason="NoMatchingResults"),
        ev("track_state", artist="Beta", title="Same", terminalOutcome="Failed",
           failureReason="NoSearchResults"),
        {"exit": 1},
    ]  # fmt: skip
    eng, _ = engine(tmp_path, steps, idle_timeout_s=3)
    result, events = run(eng, ITEMS[:2])
    assert result.ended == base.COMPLETED
    assert base.RateLimitWait(12) in events
    # the real limits: the idle default must outlast sockseek's 220 s search window
    assert config.Settings().idle_timeout_minutes * 60 > 220


def test_rejected_login_is_a_clear_error(tmp_path):
    steps = [
        {"err": "[soulseek] Logging in as tester.."},
        {"err": "[error] [cli] Soulseek login failed: The server rejected login attempt: "
                "INVALIDPASS"},
        {"exit": 1},
    ]  # fmt: skip
    eng, _ = engine(tmp_path, steps)
    result, _ = run(eng, ITEMS[:1])
    assert result.ended == base.LOGIN_FAILED
    assert "did not accept the saved login" in result.message
    assert result.unresolved == [11]


def test_kicked_mid_run(tmp_path):
    steps = [
        track_list(("Alpha", "Song")),
        {"err": "[error] [soulseek] Soulseek server kicked this client, probably because the "
                "same account logged in elsewhere. Stopping this run."},
        {"exit": 1},
    ]  # fmt: skip
    eng, _ = engine(tmp_path, steps)
    result, _ = run(eng, ITEMS[:1])
    assert result.ended == base.LOGIN_FAILED and "logged in somewhere else" in result.message


def test_password_is_cut_from_lines_and_log_file(tmp_path):
    steps = [
        {"err": "debug: odd line with Pa55-secret inside"},
        {"log": "2026 [debug] pass=Pa55-secret"},
        track_list(("Alpha", "Song")),
        {"exit": 1},
    ]
    eng, _ = engine(tmp_path, steps)
    result, events = run(eng, ITEMS[:1])
    lines = [e.text for e in events if isinstance(e, base.LogLine)]
    assert lines and all("Pa55-secret" not in line for line in lines)
    log = eng.settings.log_file.read_text(encoding="utf-8")
    assert "pass=" in log and "Pa55-secret" not in log


def test_cancel_stops_the_process_and_cleans_staging(tmp_path):
    steps = [
        track_list(("Alpha", "Song"), ("Beta", "Same")),
        ev("search_start", artist="Alpha", title="Song"),
        ev("download_start", artist="Alpha", title="Song", username="u", filename="x.flac",
           size=5),
        {"sleep": 30},
    ]  # fmt: skip
    eng, _ = engine(tmp_path, steps)
    partial = eng.settings.output_dir / ss.STAGING_SUBDIR / "guid" / "x.flac.incomplete"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"part")

    def on_event(event):
        if isinstance(event, base.ItemDownloading):
            eng.cancel()

    began = time.monotonic()
    result = eng.run(ITEMS[:2], on_event)
    assert time.monotonic() - began < 10
    assert result.ended == base.CANCELLED
    assert result.unresolved == [11, 12]
    assert not (eng.settings.output_dir / ss.STAGING_SUBDIR).exists()


def test_unknown_exit_is_a_crash(tmp_path):
    eng, _ = engine(tmp_path, [track_list(("Alpha", "Song")), {"exit": 3}])
    result, _ = run(eng, ITEMS[:1])
    assert result.ended == base.CRASHED and "exit code 3" in result.message


def test_missing_binary_says_what_to_do(monkeypatch, tmp_path):
    monkeypatch.setenv(ss.SOCKSEEK_ENV, str(tmp_path / "nope.exe"))
    monkeypatch.setattr(ss, "__file__", str(tmp_path / "a" / "b" / "c" / "sockseek.py"))
    with pytest.raises(base.EngineError, match="Install NeonSeek again"):
        ss.find_binary()


# login check


def check(tmp_path, steps, **kwargs) -> ss.LoginCheck:
    script = tmp_path / "login.json"
    script.write_text(json.dumps(steps), encoding="utf-8")
    return ss.check_login(
        LOGIN, tmp_path / "work", command=[sys.executable, str(FAKE), str(script)], **kwargs
    )


def test_login_check_ok(tmp_path):
    steps = [{"err": "[soulseek] Logging in as tester.."},
             {"err": "[debug] [jobs] Soulseek services initialized"}, {"exit": 0}]  # fmt: skip
    assert check(tmp_path, steps).ok
    args = json.loads((tmp_path / "login.args.json").read_text(encoding="utf-8"))
    assert "--no-listen" in args and "--user=tester" in args
    assert not (tmp_path / "work").exists()


def test_login_check_sends_a_bad_password_once(tmp_path):
    steps = [
        {"err": "[error] [cli] Soulseek login failed: The server rejected login attempt: "
                "INVALIDPASS"},
        # a real sockseek would end here, the fake keeps going to prove the kill
        {"sleep": 30},
    ]  # fmt: skip
    began = time.monotonic()
    result = check(tmp_path, steps)
    assert time.monotonic() - began < 10
    assert result.outcome == ss.LOGIN_REJECTED and result.code == "INVALIDPASS"


def test_login_check_other_outcomes(tmp_path):
    line = "[error] [cli] Soulseek login failed: The server rejected login attempt: SVRPRIVATE"
    assert check(tmp_path, [{"err": line}]).outcome == ss.LOGIN_SERVER
    retry = (
        "[error] [soulseek] Initial Soulseek login failed. Reconnection will be attempted "
        "automatically in the background"
    )
    assert check(tmp_path, [{"err": retry}, {"sleep": 30}]).outcome == ss.LOGIN_UNREACHABLE
    began = time.monotonic()
    result = check(tmp_path, [{"sleep": 30}], timeout_s=0.8)
    assert result.outcome == ss.LOGIN_NO_ANSWER and time.monotonic() - began < 10
