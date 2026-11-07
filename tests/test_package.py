import tomllib
from pathlib import Path

import neonseek

ROOT = Path(__file__).resolve().parents[1]


def test_version_comes_from_pyproject():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert neonseek.__version__ == pyproject["project"]["version"]
