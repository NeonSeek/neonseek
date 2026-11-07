"""phase 0 spike: run the pinned sockseek against a local mock library.

never touches the soulseek network: every run passes --mock-files-dir, which
makes sockseek use its in-process LocalFilesSoulseekClient. the --user/--pass
values are fake and only used to check that the password does not leak.

needs ffmpeg on PATH (only for generating the mock audio files).

usage: uv run python scripts/spike_sockseek.py --work <empty dir>
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOCKSEEK = ROOT / "vendor" / "sockseek" / "sockseek.exe"
FAKE_USER = "spikeuser"
FAKE_PASS = "SpikeSecret123"

# encoder settings per container, all from pink noise so flac is not tiny
CODECS = {
    "flac": ["-c:a", "flac", "-sample_fmt", "s16"],
    "flac96": ["-c:a", "flac", "-ar", "96000", "-sample_fmt", "s32", "-bits_per_raw_sample", "24"],
    "wav": ["-c:a", "pcm_s16le"],
    "aiff": ["-c:a", "pcm_s16be", "-f", "aiff"],
    "alac": ["-c:a", "alac", "-f", "ipod"],
    "aac": ["-c:a", "aac", "-b:a", "256k", "-f", "ipod"],
    "mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
}

# (relative path inside the mock dir, codec, seconds)
MOCK_MAIN = [
    ("Alpha Artist/First Album/01 - First Song.flac", "flac", 60),
    ("Alpha Artist/First Album/01 - First Song.wav", "wav", 60),
    ("Beta Artist/Second Album/02 - Second Song.wav", "wav", 50),
    ("Gamma Artist/Third Album/03 - Third Song.mp3", "mp3", 55),
    ("Delta Artist/Fourth Album/04 - Fourth Song.aiff", "aiff", 45),
    ("Epsilon Artist/Fifth Album/05 - Fifth Song.aif", "aiff", 47),
    ("Zeta Artist/Sixth Album/06 - Sixth Song.m4a", "alac", 52),
    ("Eta Artist/Seventh Album/07 - Seventh Song.m4a", "aac", 53),
    ("Theta Artist/Eighth Album/08 - Eighth Song.flac", "flac", 58),
    ("Iota Artist/Ninth Album/09 - Ninth Song.flac", "flac", 41),
    ("Kappa Artist/Tenth Album/10 - Tenth Song.flac", "flac", 43),
    ("Mu Artist/Twelfth Album/12 - Twelfth Song.flac", "flac", 44),
    ("Xi Artist/Short Album/13 - Same Song.flac", "flac", 40),
    ("Xi Artist/Long Album/13 - Same Song.flac", "flac", 62),
    ("Omikron Artist/Zażółć Album/14 - Café Ünïcode Song.flac", "flac", 46),
]

MOCK_RANK = [
    # case a: flac and wav side by side, identical paths
    ("Alpha Artist/First Album/01 - Song Aaa.flac", "flac", 60),
    ("Alpha Artist/First Album/01 - Song Aaa.wav", "wav", 60),
    # case b: wav sits in a folder named after the album, flac does not
    ("Alpha Artist/random rips/01 - Song Bbb.flac", "flac", 61),
    ("Alpha Artist/Bbb Album/01 - Song Bbb.wav", "wav", 61),
    # case c: flac 96/24 against wav 44.1/16
    ("Alpha Artist/Ccc Album/01 - Song Ccc.flac", "flac96", 62),
    ("Alpha Artist/Ccc Album/01 - Song Ccc.wav", "wav", 62),
]

MOCK_EXTRA = [
    # csv quoting: comma and apostrophe in the title
    ("Rho Artist/Rho Album/15 - Comma, Don't Song.flac", "flac", 48),
]

HEADER = ["Artist", "Title", "Album", "Length"]

ROWS_MAIN = [
    ["Alpha Artist", "First Song", "First Album", "60"],  # flac + wav
    ["Beta Artist", "Second Song", "Second Album", "50"],  # wav only
    ["Gamma Artist", "Third Song", "Third Album", "55"],  # mp3 only
    ["Delta Artist", "Fourth Song", "Fourth Album", "45"],  # .aiff
    ["Epsilon Artist", "Fifth Song", "Fifth Album", "47"],  # .aif
    ["Zeta Artist", "Sixth Song", "Sixth Album", "52"],  # alac in .m4a
    ["Eta Artist", "Seventh Song", "Seventh Album", "53"],  # aac in .m4a
    ["Theta Artist", "Eighth Song", "Eighth Album", "30"],  # file is 58 s
    ["Iota Artist", "Ninth Song", "Ninth Album", ""],  # empty length
    ["", "Tenth Song", "Tenth Album", "43"],  # empty artist
    ["Lambda Artist", "Missing Song", "Nowhere", "60"],  # nothing in mock
    ["Mu Artist", "Twelfth Song (feat. Nu Artist)", "Twelfth Album", "44"],  # remove-ft
    ["Xi Artist", "Same Song", "Short Album", "40"],  # same artist/title,
    ["Xi Artist", "Same Song", "Long Album", "62"],  # different length
    ["Omikron Artist", "Café Ünïcode Song", "Zażółć Album", "46"],  # non ascii
]

ROWS_RANK = [
    ["Alpha Artist", "Song Aaa", "First Album", "60"],
    ["Alpha Artist", "Song Bbb", "Bbb Album", "61"],
    ["Alpha Artist", "Song Ccc", "Ccc Album", "62"],
]


def make_audio(target: Path, codec: str, seconds: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"anoisesrc=d={seconds}:c=pink:r=44100:a=0.3",
        "-ac", "2", *CODECS[codec], str(target),
    ]  # fmt: skip
    subprocess.run(cmd, check=True)


def build_mock(directory: Path, files: list[tuple[str, str, int]]) -> None:
    for rel, codec, seconds in files:
        make_audio(directory / rel, codec, seconds)


def write_csv(path: Path, rows: list[list[str]], header: list[str] = HEADER) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def base_args(csv_path: Path, mock: Path, out: Path, log: Path) -> list[str]:
    # the adapter call from plan section 7, with fake credentials and a mock library
    return [
        str(csv_path),
        "--user", FAKE_USER, "--pass", FAKE_PASS,
        "--no-config",
        "--mock-files-dir", str(mock),
        "--output-dir", str(out),
        "--format", "flac,wav,aiff,alac", "--pref-format", "flac",
        "--length-tol", "3",
        "--strict-title", "--strict-artist",
        "--remove-ft",
        "--no-write-index", "--no-skip-existing",
        "--name-format", "{row}",
        "--progress-json",
        "--log-file", str(log),
    ]  # fmt: skip


def replace_flag(args: list[str], flag: str, value: str | None) -> list[str]:
    # swap the value of one flag, or drop it when value is None
    out = list(args)
    i = out.index(flag)
    if value is None:
        del out[i : i + 2]
    else:
        out[i + 1] = value
    return out


def run(name: str, args: list[str], work: Path, kill_after: float | None = None) -> dict:
    """run sockseek, keep stdout/stderr with arrival times, return a summary."""
    result_dir = work / "results" / name
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "args.json").write_text(json.dumps(args, indent=1), encoding="utf-8")

    start = time.monotonic()
    proc = subprocess.Popen(
        [str(SOCKSEEK), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )
    lines: dict[str, list[tuple[float, bytes]]] = {"stdout": [], "stderr": []}

    def pump(stream, key):
        for raw in iter(stream.readline, b""):
            lines[key].append((round(time.monotonic() - start, 2), raw.rstrip(b"\r\n")))

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, "stdout")),
        threading.Thread(target=pump, args=(proc.stderr, "stderr")),
    ]
    for t in threads:
        t.start()
    killed = False
    if kill_after is not None:
        try:
            proc.wait(timeout=kill_after)
        except subprocess.TimeoutExpired:
            proc.kill()
            killed = True
    code = proc.wait(timeout=600)
    for t in threads:
        t.join()

    for key in ("stdout", "stderr"):
        with (result_dir / f"{key}.txt").open("wb") as f:
            for ts, raw in lines[key]:
                f.write(f"[{ts:7.2f}] ".encode() + raw + b"\n")
        (result_dir / f"{key}.raw").write_bytes(b"\n".join(raw for _, raw in lines[key]))

    out_dir = Path(args[args.index("--output-dir") + 1])
    files = (
        sorted(str(p.relative_to(out_dir)) for p in out_dir.rglob("*") if p.is_file())
        if out_dir.exists()
        else []
    )
    summary = {
        "name": name,
        "exit_code": code,
        "killed": killed,
        "seconds": round(time.monotonic() - start, 2),
        "output_files": files,
    }
    (result_dir / "summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--only", nargs="*", help="run only these experiments")
    ns = parser.parse_args()
    work: Path = ns.work.resolve()
    work.mkdir(parents=True, exist_ok=True)

    if not SOCKSEEK.exists():
        print(
            "missing vendor/sockseek/sockseek.exe, run scripts/fetch_sockseek.ps1", file=sys.stderr
        )
        return 1
    if shutil.which("ffmpeg") is None:
        print("ffmpeg not found on PATH", file=sys.stderr)
        return 1

    mock_main = work / "mock_main"
    mock_rank = work / "mock_rank"
    if not mock_main.exists():
        build_mock(mock_main, MOCK_MAIN)
    if not mock_rank.exists():
        build_mock(mock_rank, MOCK_RANK)
    mock_extra = work / "mock_extra"
    if not mock_extra.exists():
        build_mock(mock_extra, MOCK_EXTRA)

    csv_dir = work / "csv"
    csv_dir.mkdir(exist_ok=True)
    write_csv(csv_dir / "main.csv", ROWS_MAIN)
    write_csv(csv_dir / "rank.csv", ROWS_RANK)
    write_csv(csv_dir / "formats.csv", [ROWS_MAIN[i] for i in (3, 4, 5, 6)])
    write_csv(csv_dir / "ratelimit.csv", [ROWS_MAIN[i] for i in (0, 1, 3, 9, 12, 13)])
    write_csv(csv_dir / "one.csv", [ROWS_MAIN[0]])
    # file is 60 s, tolerance 3: 57 and 63 should match, 56 and 64 should not
    write_csv(
        csv_dir / "lengthtol.csv",
        [["Alpha Artist", "First Song", "First Album", str(n)] for n in (56, 57, 63, 64)],
    )
    # csv.writer quotes the comma, the title also has an apostrophe
    write_csv(csv_dir / "quoting.csv", [["Rho Artist", "Comma, Don't Song", "Rho Album", "48"]])
    # blank line and a quoted multi-line cell before the real row
    (csv_dir / "rows.csv").write_text(
        "Artist,Title,Album,Length\n"
        "\n"
        '"Nobody","Line one\nline two","X","10"\n'
        "Beta Artist,Second Song,Second Album,50\n",
        encoding="utf-8",
    )

    def out(name: str) -> Path:
        return work / "out" / name

    def log(name: str) -> Path:
        return work / "logs" / f"{name}.log"

    (work / "logs").mkdir(exist_ok=True)
    experiments = {}

    a = base_args(csv_dir / "main.csv", mock_main, out("main"), log("main"))
    experiments["main"] = lambda a=a: run("main", a, work)

    for i in range(1, 4):
        r = base_args(csv_dir / "rank.csv", mock_rank, out(f"rank{i}"), log(f"rank{i}"))
        experiments[f"rank{i}"] = lambda r=r, i=i: run(f"rank{i}", r, work)

    r = base_args(csv_dir / "rank.csv", mock_rank, out("rank_print"), log("rank_print"))
    r = [x for x in r if x != "--progress-json"] + ["--print", "results-full"]
    experiments["rank_print"] = lambda r=r: run("rank_print", r, work)

    r = replace_flag(
        base_args(csv_dir / "rank.csv", mock_rank, out("rank_flac_only"), log("rank_flac_only")),
        "--format",
        "flac",
    )
    experiments["rank_flac_only"] = lambda r=r: run("rank_flac_only", r, work)

    for fmt in ("aiff", "aif", "alac", "m4a", "nonsense"):
        f = replace_flag(
            base_args(csv_dir / "formats.csv", mock_main, out(f"fmt_{fmt}"), log(f"fmt_{fmt}")),
            "--format",
            fmt,
        )
        experiments[f"fmt_{fmt}"] = lambda f=f, fmt=fmt: run(f"fmt_{fmt}", f, work)

    rl = base_args(csv_dir / "ratelimit.csv", mock_main, out("ratelimit"), log("ratelimit"))
    rl += ["--searches-per-time", "2", "--searches-renew-time", "15"]
    experiments["ratelimit"] = lambda rl=rl: run("ratelimit", rl, work)

    rv = base_args(csv_dir / "ratelimit.csv", mock_main, out("ratelimit_v"), log("ratelimit_v"))
    rv += ["--searches-per-time", "2", "--searches-renew-time", "15", "-v"]
    experiments["ratelimit_v"] = lambda rv=rv: run("ratelimit_v", rv, work)

    fl = base_args(csv_dir / "one.csv", mock_main, out("fail1"), log("fail1"))
    fl += ["--mock-files-fail-downloads", "1"]
    experiments["fail1"] = lambda fl=fl: run("fail1", fl, work)

    fl = base_args(csv_dir / "one.csv", mock_main, out("fail_all"), log("fail_all"))
    fl += ["--mock-files-fail-downloads", "50"]
    experiments["fail_all"] = lambda fl=fl: run("fail_all", fl, work)

    sl = base_args(csv_dir / "one.csv", mock_main, out("slow"), log("slow"))
    sl += ["--mock-files-slow"]
    experiments["slow"] = lambda sl=sl: run("slow", sl, work)

    kl = base_args(csv_dir / "ratelimit.csv", mock_main, out("kill"), log("kill"))
    kl += ["--mock-files-slow"]
    experiments["kill"] = lambda kl=kl: run("kill", kl, work, kill_after=6)

    lt = base_args(csv_dir / "lengthtol.csv", mock_main, out("lengthtol"), log("lengthtol"))
    experiments["lengthtol"] = lambda lt=lt: run("lengthtol", lt, work)

    qt = base_args(csv_dir / "quoting.csv", mock_extra, out("quoting"), log("quoting"))
    experiments["quoting"] = lambda qt=qt: run("quoting", qt, work)

    rw = base_args(csv_dir / "rows.csv", mock_main, out("rows"), log("rows"))
    experiments["rows"] = lambda rw=rw: run("rows", rw, work)

    nf = base_args(csv_dir / "main.csv", mock_main, out("missing_csv"), log("missing_csv"))
    nf[0] = str(csv_dir / "does-not-exist.csv")
    experiments["missing_csv"] = lambda nf=nf: run("missing_csv", nf, work)

    selected = ns.only or list(experiments)
    summaries = [experiments[name]() for name in selected]
    for s in summaries:
        print(json.dumps(s, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
