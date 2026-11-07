"""phase 2 manual checks on the real soulseek network (PLAN.md section 12). dev only.

reads the soulseek login from windows credential manager (neonseek soulseek login) and
never prints it: every output is cut with the redactor, and the script reports how many
times the password and the username showed up before cutting.

scenarios:
  login     header-only csv: logs in and ends without any search
  newname   a random free username and password (creates a throwaway account). only the
            username is written to <work>/probe-user.txt, the password is never stored
  badpass   the probe username from newname with another random password. refuses to run
            on the saved username: a free name would be registered with a random password
  download  the tracks of --csv (a few rows), flac only, real search and download
  search    one search per row of --csv with --print results-full: shows what soulseek
            returns and how the app's conditions rank it, downloads nothing

search limits are never raised. every scenario keeps sockseek's defaults.

usage: uv run python scripts/live_check_soulseek.py --work <dir> SCENARIO [--csv F] [--no-listen]
"""

from __future__ import annotations

import argparse
import json
import secrets as pyrandom
import string
import subprocess
import sys
import threading
import time
from pathlib import Path

from neonseek import secrets
from neonseek.redact import Redactor

ROOT = Path(__file__).resolve().parents[1]
SOCKSEEK = ROOT / "vendor" / "sockseek" / "sockseek.exe"
CREATE_NO_WINDOW = 0x08000000
LOGIN_LIMIT_S = 30
# lines that mean the login did not go through (sockseek 3.0.5 SoulseekClientManager and
# DownloadRunScope). the process is killed at the first one, so a bad password is sent once
LOGIN_TROUBLE = (
    "login failed",
    "failed to ensure soulseek connection",
    "connection lost",
    "reconnect",
    "kicked",
)


def random_text(length: int) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(pyrandom.choice(alphabet) for _ in range(length))


def build_args(scenario: str, csv_path: Path, out: Path, log: Path, user: str, password: str):
    args = [
        str(csv_path),
        f"--user={user}",
        f"--pass={password}",
        "--no-config",
        "--output-dir", str(out),
        "--progress-json",
        "-v",
        "--log-file", str(log),
    ]  # fmt: skip
    if scenario == "download":
        args += [
            "--format", "flac",
            "--length-tol", "3",
            "--strict-title", "--strict-artist",
            "--remove-ft",
            "--no-write-index", "--no-skip-existing",
            "--name-format", "{row}",
        ]  # fmt: skip
    elif scenario == "search":
        # the app's match conditions, but every format, so the listing shows all results
        args += [
            "--print", "results-full",
            "--length-tol", "3",
            "--strict-title", "--strict-artist",
            "--remove-ft",
        ]  # fmt: skip
    return args


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=["login", "badpass", "newname", "download", "search"])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--csv", type=Path, help="run.csv for the download scenario")
    parser.add_argument("--no-listen", action="store_true")
    parser.add_argument("--timeout", type=float, help="kill sockseek after this many seconds")
    parser.add_argument(
        "--mock-files-dir", type=Path, help="dry run of this script without the network"
    )
    ns = parser.parse_args()

    if ns.mock_files_dir:
        stored = secrets.SoulseekLogin("mockuser", "Mock-Pass 123")
    else:
        stored = secrets.get_soulseek_login()
    if stored is None:
        print("no soulseek login saved, run: uv run neonseek soulseek login", file=sys.stderr)
        return 1
    probe_file = ns.work / "probe-user.txt"
    if ns.scenario == "badpass":
        if not probe_file.exists():
            print("run newname first, badpass only uses the probe account", file=sys.stderr)
            return 1
        user = probe_file.read_text(encoding="utf-8").strip()
        if not user.startswith("probe") or user.casefold() == stored.username.casefold():
            print("badpass only runs on the probe account", file=sys.stderr)
            return 1
        password = "Wrong-" + random_text(16)
    elif ns.scenario == "newname":
        user, password = "probe" + random_text(10), "P-" + random_text(16)
        ns.work.mkdir(parents=True, exist_ok=True)
        probe_file.write_text(user + "\n", encoding="utf-8")
    else:
        user, password = stored.username, stored.password
    # the stored password is counted and cut even in scenarios that do not use it
    redact = Redactor([password, stored.password], mask="<password>")
    run_pass = Redactor([password])
    saved_pass = Redactor([stored.password])
    users = Redactor([user, stored.username], mask="<user>")

    stamp = time.strftime("%H%M%S")
    run_dir = (ns.work / f"{ns.scenario}-{stamp}").resolve()
    out = run_dir / "out"
    out.mkdir(parents=True)
    log = run_dir / "sockseek.log"
    if ns.scenario in ("download", "search"):
        if ns.csv is None:
            print(f"{ns.scenario} needs --csv", file=sys.stderr)
            return 1
        csv_path = ns.csv.resolve()
    else:
        csv_path = run_dir / "empty.csv"
        csv_path.write_text("Artist,Title,Album,Length\n", encoding="utf-8")

    args = build_args(ns.scenario, csv_path, out, log, user, password)
    if ns.no_listen:
        args.append("--no-listen")
    if ns.mock_files_dir:
        args += ["--mock-files-dir", str(ns.mock_files_dir.resolve())]
    # login checks get a hard 30 s limit. a download gets 30 s to log in and send its first
    # json event, then the long limit
    timeout = ns.timeout or {"download": 900, "search": 90}.get(ns.scenario, LOGIN_LIMIT_S)
    first_event_limit = LOGIN_LIMIT_S if ns.scenario == "download" else None

    start = time.monotonic()
    proc = subprocess.Popen(
        [str(SOCKSEEK), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
    lines: list[tuple[float, str, str]] = []
    lock = threading.Lock()
    login_trouble = threading.Event()
    got_json = threading.Event()

    def pump(stream, name):
        for raw in iter(stream.readline, b""):
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            with lock:
                lines.append((time.monotonic() - start, name, text))
            if name == "out" and text.startswith("{"):
                got_json.set()
            trouble = any(marker in text.lower() for marker in LOGIN_TROUBLE)
            if trouble and not login_trouble.is_set():
                # kill right here, before the 1 s reconnect delay sends the login again
                login_trouble.set()
                proc.kill()

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, "out"), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, "err"), daemon=True),
    ]
    for t in threads:
        t.start()
    ended_by = "itself"
    while True:
        try:
            code = proc.wait(timeout=0.1)
            break
        except subprocess.TimeoutExpired:
            pass
        elapsed = time.monotonic() - start
        if first_event_limit and not got_json.is_set() and elapsed > first_event_limit:
            ended_by = "killed: no first json event in time"
        elif elapsed > timeout:
            ended_by = "killed: hard time limit"
        else:
            continue
        proc.kill()
        code = proc.wait()
        break
    if login_trouble.is_set():
        ended_by = "killed: at the first login failure line"
    for t in threads:
        t.join(timeout=5)
    seconds = time.monotonic() - start

    # count before cutting, then never keep the raw text
    counters = {"run": run_pass, "saved": saved_pass, "user": users}
    hits = {key: {"out": 0, "err": 0, "log": 0} for key in counters}
    for _, name, text in lines:
        for key, counter in counters.items():
            hits[key][name] += counter.hits(text)
    log_text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    for key, counter in counters.items():
        hits[key]["log"] = counter.hits(log_text)
    log.write_text(users(redact(log_text)), encoding="utf-8")

    def clean(text: str) -> str:
        return users(redact(text.replace(str(run_dir), "<RUN>")))

    with (run_dir / "lines.txt").open("w", encoding="utf-8") as f:
        for ts, name, text in lines:
            f.write(f"[{ts:8.2f}] {name} {clean(text)}\n")

    def first(predicate):
        return next((round(ts, 2) for ts, n, t in lines if predicate(n, t)), None)

    gaps = []
    for (t0, _, a), (t1, _, b) in zip(lines, lines[1:], strict=False):
        gaps.append((round(t1 - t0, 2), clean(a)[:140], clean(b)[:140]))
    gaps.sort(reverse=True)
    states = []
    for _, name, text in lines:
        if name == "out" and text.startswith("{"):
            try:
                event = json.loads(text)
            except ValueError:
                continue
            if event.get("type") == "track_state":
                data = event["data"]
                states.append(
                    {
                        k: clean(str(data[k])) if isinstance(data.get(k), str) else data.get(k)
                        for k in (
                            "artist",
                            "title",
                            "terminalOutcome",
                            "failureReason",
                            "username",
                            "filename",
                            "downloadPath",
                        )
                        if k in data
                    }
                )
    summary = {
        "scenario": ns.scenario,
        "no_listen": ns.no_listen,
        "exit_code": code,
        "ended_by": ended_by,
        "seconds": round(seconds, 2),
        "login_trouble_at": first(
            lambda n, t: any(marker in t.lower() for marker in LOGIN_TROUBLE)
        ),
        "first_stdout_json_at": first(lambda n, t: n == "out" and t.startswith("{")),
        "services_initialized_at": first(lambda n, t: "Soulseek services initialized" in t),
        "login_failed_lines": [clean(t) for _, _, t in lines if "login failed" in t.lower()],
        "listen_lines": [clean(t)[:200] for _, _, t in lines if "listen" in t.lower()],
        # run: the password sent in this run, saved: the one in credential manager
        "password_hits_before_cut": hits["run"],
        "saved_password_hits_before_cut": hits["saved"],
        "username_hits_before_cut": hits["user"],
        "longest_silences_s": gaps[:5],
        "track_states": states,
        "files": sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()),
    }
    text = json.dumps(summary, indent=1, ensure_ascii=False)
    (run_dir / "summary.json").write_text(text, encoding="utf-8")
    print(text)
    print(f"details: {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
