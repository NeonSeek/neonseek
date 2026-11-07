"""contract with the pinned sockseek: the search limit line on stderr matches the adapter.

runs on --mock-files-dir with a lowered --searches-per-time. lowering is allowed, only raising
the limits is forbidden (PLAN.md section 0), and the mock makes no network connection.
"""

from __future__ import annotations

from audio_files import write_audio
from download_env import BINARY, needs_sockseek
from neonseek.engine import base
from neonseek.engine import sockseek as ss
from neonseek.secrets import SoulseekLogin

pytestmark = needs_sockseek


def test_rate_limited_line_matches_the_adapter(tmp_path):
    mock = tmp_path / "mock"
    names = ["First", "Second", "Third", "Fourth"]
    for n, name in enumerate(names, start=1):
        write_audio(mock / f"Artist {n}/Album/0{n} - {name} Song.flac", 40 + n)
    items = [base.EngineItem(100 + n, f"Artist {n}", f"{name} Song", "Album", 40 + n)
             for n, name in enumerate(names, start=1)]  # fmt: skip
    engine = ss.SockseekEngine(
        ss.SockseekSettings(
            login=SoulseekLogin("tester", "Pa55-word"),
            output_dir=tmp_path / "staging",
            run_csv=tmp_path / "run.csv",
            log_file=tmp_path / "sockseek.log",
            formats=ss.FLAC_FORMATS,
            command=[str(BINARY)],
            extra_args=(
                "--mock-files-dir",
                str(mock),
                "--searches-per-time",
                "2",
                "--searches-renew-time",
                "3",
            ),  # fmt: skip
        )
    )
    events = []
    result = engine.run(items, events.append)

    assert result.ended == base.COMPLETED and result.unresolved == []
    lines = [e.text for e in events if isinstance(e, base.LogLine) and e.stream == "stderr"]
    limited = [line for line in lines if "SongJob: rate limited:" in line]
    assert limited, "sockseek 3.0.5 prints the search limit line at -v"
    assert all(ss.RATE_LIMIT_RE.search(line) for line in limited)
    waits = [e.track_id for e in events if isinstance(e, base.RateLimitWait)]
    # every line was mapped back to one of our tracks
    assert waits and set(waits) <= {item.track_id for item in items}
    assert len([e for e in events if isinstance(e, base.ItemDone)]) == 4
    assert result.searches == 4
    # the wait is silent on both streams, so it is the longest gap
    assert result.longest_silence_s >= 2
