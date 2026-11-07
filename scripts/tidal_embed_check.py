"""reads tidal links live from the embed pages and shows what the parser makes of them, to
see quickly whether tidal changed its pages (docs/tidal-notes.md). tests never go online, this
script does. keep the links you check out of the repository.

usage: uv run python scripts/tidal_embed_check.py <tidal link> [<tidal link> ...]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from neonseek.errors import NeonSeekError  # noqa: E402
from neonseek.sources import apple, tidal  # noqa: E402
from neonseek.sources import links as weblinks  # noqa: E402


def check(http, pacer, link: str) -> bool:
    ref = weblinks.parse_web_ref(link)
    if ref.service != weblinks.TIDAL:
        print(f"{link}: not a tidal link")
        return False
    if ref.kind == weblinks.TRACK:
        track = tidal.fetch_track(http, pacer, ref.id, print)
        print(f"  track: {', '.join(track.artists)} - {track.title} | {track.album} | "
              f"{track.duration_ms} ms | cover {'yes' if track.cover_url else 'no'}")  # fmt: skip
        return bool(track.title and track.artists)
    found = tidal.fetch_list(http, pacer, ref.id, ref.kind == weblinks.ALBUM, print)
    print(f"  {found.name}: {found.entries} entries, {len(found.tracks)} tracks, "
          f"{found.videos} videos, complete {found.complete}")  # fmt: skip
    gaps = {
        "artists": sum(1 for t in found.tracks if not t.artists),
        "album": sum(1 for t in found.tracks if not t.album),
        "length": sum(1 for t in found.tracks if not t.duration_ms),
        "cover": sum(1 for t in found.tracks if not t.cover_url),
    }
    # a few gaps are tidal's own data, a field missing on every track means the page changed
    for name, count in gaps.items():
        if count:
            print(f"  missing {name}: {count} of {len(found.tracks)}")
    for track in found.tracks[:5]:
        print(f"    {', '.join(track.artists)} - {track.title} | {track.album}")
    for note in found.notes:
        print(f"  note: {note}")
    if not found.tracks:
        return found.videos > 0
    return all(count < len(found.tracks) for count in gaps.values())


def main(links: list[str]) -> int:
    if not links:
        print(__doc__)
        return 2
    http = apple.new_web_client()
    pacer = tidal.Pacer(sleep=time.sleep)
    ok = True
    for link in links:
        print(link)
        try:
            ok = check(http, pacer, link) and ok
        except NeonSeekError as exc:
            print(f"  error: {exc.message}")
            ok = False
    print("parser ok" if ok else "parser needs a look")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
