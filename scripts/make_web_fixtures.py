"""writes tests/fixtures/web/ytdlp_soundcloud.json: what the pinned yt-dlp makes of the made up
soundcloud answers in soundcloud_api.json. nothing goes online, yt-dlp only sees the fake
answers of tests/web_fake.py. run again after a yt-dlp update, the contract test in
tests/test_web_sources.py compares both.

usage: uv run python scripts/make_web_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yt_dlp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

from neonseek.sources.ytdlp import YtDlp  # noqa: E402
from web_fake import FIXTURES, FakeSoundCloudNet, soundcloud_fixture_ids  # noqa: E402

SET_URL = "https://soundcloud.com/fixture-uploader/sets/fixture-set"


def plain(value):
    """json without yt-dlp's inner keys (functions, ip headers, version)."""
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()
                if not k.startswith("_") or k == "_type"}  # fmt: skip
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def main() -> None:
    net = FakeSoundCloudNet()
    yt_dlp.YoutubeDL.urlopen = lambda ydl, request: net.urlopen(ydl, request)
    extractor = YtDlp(cache_dir=None)
    found_set = extractor.extract(SET_URL)
    tracks = extractor.soundcloud_tracks(soundcloud_fixture_ids())
    out = {
        "_comment": "made by scripts/make_web_fixtures.py from soundcloud_api.json with yt-dlp "
        f"{yt_dlp.version.__version__}. what FakeExtractor answers for soundcloud.",
        "set": plain(found_set),
        "tracks": {k: plain(v) for k, v in tracks.items()},
    }
    path = FIXTURES / "ytdlp_soundcloud.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{path}: set with {len(found_set['entries'])} entries, {len(tracks)} tracks")
    print("requests:", *net.requests, sep="\n  ")


if __name__ == "__main__":
    main()
