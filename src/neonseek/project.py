"""where neonseek's source code is public (gpl-3.0, D-203, D-214) and where donations go.

this is the only place with the addresses. the window, the cli, the installer and the readme
of the package all take them from here (scripts/build.ps1 reads it for the installer).
"""

from __future__ import annotations

# the public repository (D-265)
REPO_URL = "https://github.com/NeonSeek/neonseek"
# voluntary donations (D-203, D-265)
DONATION_LINK = "https://buymeacoffee.com/neonseek"

# a release is tagged v<version> in the public repository
TAG_PREFIX = "v"


def source_url(version: str) -> str:
    """the source code of exactly this version."""
    return f"{REPO_URL}/tree/{TAG_PREFIX}{version}"
