"""replaces the real catalog in tests/fixtures/spotify with a made up one (D-212): ids, names,
isrc, upc, cover urls, labels and copyrights of tracks, albums and artists, and the id of the
spotify-made playlist, the same way in every file. run it after make_spotify_fixtures.py.
the real -> fake id map goes to .local/ (not in git) for the personal data scan.

usage: uv run python scripts/fake_spotify_catalog.py   (from the repo root)
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
FX = ROOT / "tests" / "fixtures" / "spotify"
VERSION = re.compile(
    r" - ((?:[^-]*\b)(?:Mix|Remix|Edit|Version|Dub|Rework|VIP|Remaster(?:ed)?)\b.*)$", re.I
)

files = sorted(p for p in FX.rglob("*.json"))
docs = {}
fmt = {}
for p in files:
    raw = p.read_text(encoding="utf-8")
    data = json.loads(raw)
    for ea in (False, True):
        if json.dumps(data, indent=2, ensure_ascii=ea) + "\n" == raw:
            fmt[p] = ea
            break
    else:
        sys.exit(f"cannot reproduce {p}")
    docs[p] = data

if any(d.get("liked_first_track", "").startswith("0NeonFx") for d in docs.values()):
    sys.exit("the fixtures already hold the made up catalog")

ids: dict[str, str] = {}
names: dict[str, str] = {}
counters: dict[str, int] = {}
other: dict[str, str] = {}


def nxt(kind: str) -> int:
    counters[kind] = counters.get(kind, 0) + 1
    return counters[kind]


def fake_id(real: str, kind: str) -> str:
    if real not in ids:
        tag = {"track": "Trk", "album": "Alb", "artist": "Art"}[kind]
        head = f"0NeonFx{tag}"
        ids[real] = f"{head}{nxt('id-' + kind):0{22 - len(head)}d}"
    return ids[real]


def fake_name(real: str, kind: str) -> str:
    if real in names:
        return names[real]
    polish = any(ord(ch) > 127 for ch in real)
    base = {
        ("artist", False): "Fixture Artist",
        ("artist", True): "Wykonawca Zażółć",
        ("album", False): "Fixture Album",
        ("album", True): "Album Gęślą",
        ("track", False): "Fixture Track",
        ("track", True): "Utwór Jaźń",
    }[(kind, polish)]
    fake = f"{base} {nxt('name-' + kind):02d}"
    version = VERSION.search(real) if kind == "track" else None
    if version:
        fake += f" - {version.group(1)}"
    names[real] = fake
    return fake


def fake_other(real: str, kind: str, make) -> str:
    key = (kind, real)
    if key not in other:
        other[key] = make(nxt("other-" + kind))
    return other[key]


def catalog(obj) -> None:
    """first pass: ids and names of tracks, albums and artists."""
    if isinstance(obj, dict):
        kind = obj.get("type")
        if kind in ("track", "album", "artist") and isinstance(obj.get("id"), str):
            fake_id(obj["id"], kind)
            if isinstance(obj.get("name"), str):
                fake_name(obj["name"], kind)
        for v in obj.values():
            catalog(v)
    elif isinstance(obj, list):
        for v in obj:
            catalog(v)


def rewrite(obj):
    if isinstance(obj, dict):
        out = {}
        kind = obj.get("type")
        for k, v in obj.items():
            if k == "name" and kind in ("track", "album", "artist") and isinstance(v, str):
                out[k] = names[v]
            elif k == "isrc" and isinstance(v, str):
                out[k] = fake_other(v, "isrc", lambda n: f"QZNFX26{n:05d}")
            elif k == "upc" and isinstance(v, str):
                width = len(v)
                out[k] = fake_other(v, "upc", lambda n, w=width: f"{n:0{w}d}")
            elif k == "label" and isinstance(v, str):
                out[k] = "Fixture Records"
            elif k == "text" and isinstance(v, str) and kind in ("C", "P"):
                year = re.search(r"\b(19|20)\d\d\b", v)
                mark = "℗" if kind == "P" else "©"
                out[k] = f"{mark} {year.group(0) + ' ' if year else ''}Fixture Records"
            elif k == "url" and isinstance(v, str) and "i.scdn.co/image/" in v:
                head, _, tail = v.partition("/image/")
                out[k] = (
                    head
                    + "/image/"
                    + fake_other(v, "image", lambda n, t=tail: t[:16] + f"{n:0{len(t) - 16}x}")
                )
            else:
                out[k] = rewrite(v)
        return out
    if isinstance(obj, list):
        return [rewrite(v) for v in obj]
    if isinstance(obj, str):
        return ID_RX.sub(lambda m: ids[m.group(0)], obj) if ID_RX.search(obj) else obj
    return obj


for d in docs.values():
    catalog(d)
for p in files:
    if p.name == "_ids.json":
        made = docs[p].get("spotify_playlist", "")
        if made and not made.startswith("37i9dQZF1Neon"):
            ids[made] = "37i9dQZF1NeonFixture01"
ID_RX = re.compile("|".join(map(re.escape, sorted(ids, key=len, reverse=True))))

for p, d in docs.items():
    p.write_text(
        json.dumps(rewrite(d), indent=2, ensure_ascii=fmt[p]) + "\n", encoding="utf-8", newline="\n"
    )
renamed = 0
for p in files:
    m = re.fullmatch(r"track\.([0-9A-Za-z]{22})\.json", p.name)
    if m and m.group(1) in ids:
        subprocess.run(
            ["git", "mv", str(p), str(p.with_name(f"track.{ids[m.group(1)]}.json"))], check=True
        )
        renamed += 1
left = [p for p in FX.rglob("*.json") if any(r in p.read_text(encoding="utf-8") for r in ids)]
mapping = ROOT / ".local" / "fake_catalog_ids.json"
mapping.parent.mkdir(exist_ok=True)
real = {"ids": ids, "names": names, "other": sorted(value for _, value in other)}
mapping.write_text(json.dumps(real, indent=2, ensure_ascii=False), encoding="utf-8")
print(
    f"ids {len(ids)}, names {len(names)}, other {len(other)}, files {len(docs)}, "
    f"renamed {renamed}, files still holding a real id: {len(left)}"
)
