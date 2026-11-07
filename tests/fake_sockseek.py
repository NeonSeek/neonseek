"""stand-in for sockseek.exe in timeout and mapping tests. never touches the network.

usage: python fake_sockseek.py <script.json> <the sockseek arguments...>

the script is a list of steps:
  {"out": "line"} / {"err": "line"}   print a line, {OUT} becomes the --output-dir path
  {"sleep": seconds}
  {"file": "2.flac"}                  create a file in --output-dir
  {"log": "line"}                     append a line to --log-file
  {"exit": code}
the arguments sockseek got are written next to the script as <script>.args.json.
"""

import json
import sys
import time
from pathlib import Path


def option(args, name):
    for i, arg in enumerate(args):
        if arg == name and i + 1 < len(args):
            return args[i + 1]
    return None


def main():
    script = Path(sys.argv[1])
    args = sys.argv[2:]
    script.with_suffix(".args.json").write_text(json.dumps(args), encoding="utf-8")
    out_dir = Path(option(args, "--output-dir") or ".")
    log_file = option(args, "--log-file")
    out_json = json.dumps(str(out_dir))[1:-1]
    for step in json.loads(script.read_text(encoding="utf-8")):
        if "out" in step or "err" in step:
            stream = sys.stdout if "out" in step else sys.stderr
            text = step.get("out", step.get("err")).replace("{OUT}", out_json)
            stream.buffer.write((text + "\n").encode("utf-8"))
            stream.flush()
        elif "sleep" in step:
            time.sleep(step["sleep"])
        elif "file" in step:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / step["file"]).write_bytes(b"fake audio")
        elif "log" in step and log_file:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(step["log"] + "\n")
        elif "exit" in step:
            sys.exit(step["exit"])
    sys.exit(0)


if __name__ == "__main__":
    main()
