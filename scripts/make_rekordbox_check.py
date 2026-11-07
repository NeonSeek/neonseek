"""dev tool: a small test set for the manual rekordbox checks at the end of phase 2.

writes a flac, a wav and an aiff with a tone, a cover of a different color each, every tag field
of PLAN.md section 8 (with polish letters) and an .m3u8 with relative paths and a non-ascii name.
cover and tags go through the same code as the download (cover and tag steps), so rekordbox sees
exactly what neonseek writes. needs ffmpeg in PATH, never touches the library or the database.

usage:
  uv run python scripts/make_rekordbox_check.py --out <empty folder>          # build the set
  uv run python scripts/make_rekordbox_check.py --out <same folder> --retag   # change genre and
                                                                               # comment, for the
                                                                               # Reload Tag check
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from neonseek import config
from neonseek.pipeline.postprocess.cover import embed_cover
from neonseek.pipeline.postprocess.tag import TagValues, write_tags

FILES = {
    # format: (file name, cover color, ffmpeg codec options)
    "flac": (
        "NeonSeek Test - Zażółć FLAC.flac",
        "0xD6278F",
        ["-c:a", "flac", "-sample_fmt", "s16"],
    ),  # fmt: skip
    "wav": ("NeonSeek Test - Zażółć WAV.wav", "0x1F8F8C", ["-c:a", "pcm_s16le"]),
    "aiff": ("NeonSeek Test - Zażółć AIFF.aiff", "0xF2C14E", ["-c:a", "pcm_s16be", "-f", "aiff"]),
}
PLAYLIST = "NeonSeek test ż.m3u8"


def values(fmt: str, retag: bool) -> TagValues:
    return TagValues(
        title=f"Zażółć gęślą jaźń ({fmt.upper()})",
        artist="NeonSeek Test, Artystka Ż",
        album="Album Ąę Test",
        album_artist="NeonSeek Album Artist",
        date="2021-03-04",
        track_number="7",
        disc_number="2",
        isrc="PLAAA2100001",
        genre="Industrial Techno" if retag else "Hardcore Techno",
        comment="po retag: łódź" if retag else "komentarz ąęś",
    )


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def build(out: Path) -> None:
    tracks = out / "tracks"
    tracks.mkdir(parents=True, exist_ok=True)
    lines = ["#EXTM3U"]
    for fmt, (name, color, codec) in FILES.items():
        target = tracks / name
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=20:sample_rate=44100",
               "-ac", "2", *codec, str(target))  # fmt: skip
        cover = out / f"cover-{fmt}.png"
        ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s=300x300", "-frames:v", "1", str(cover))
        embed_cover(target, fmt, cover.read_bytes())
        cover.unlink()
        write_tags(target, fmt, values(fmt, retag=False), config.TAGS_FILL)
        tags = values(fmt, retag=False)
        lines += [f"#EXTINF:20,{tags.artist} - {tags.title}", f"..\\tracks\\{name}"]
    playlists = out / "playlists"
    playlists.mkdir(exist_ok=True)
    (playlists / PLAYLIST).write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")


def retag(out: Path) -> None:
    for fmt, (name, _, _) in FILES.items():
        write_tags(out / "tracks" / name, fmt, values(fmt, retag=True), config.TAGS_FILL)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--retag", action="store_true")
    args = parser.parse_args()
    if shutil.which("ffmpeg") is None:
        print("ffmpeg is needed in PATH", file=sys.stderr)
        return 1
    if args.retag:
        retag(args.out)
        print(f"genre and comment changed in {args.out / 'tracks'}, now use Reload Tag")
        return 0
    if args.out.exists() and any(args.out.iterdir()):
        print(f"{args.out} is not empty, choose an empty folder", file=sys.stderr)
        return 1
    build(args.out)
    print(f"test set written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
