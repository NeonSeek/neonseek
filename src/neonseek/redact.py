"""cuts secrets out of text before it is shown or written to a log."""

from __future__ import annotations

import json
from collections.abc import Iterable
from urllib.parse import quote

MASK = "<hidden>"


def variants(secret: str) -> set[str]:
    # the same secret as it may appear in plain text, in json and in a url
    if not secret:
        return set()
    found = {secret, quote(secret, safe=""), quote(secret)}
    for ascii_only in (True, False):
        found.add(json.dumps(secret, ensure_ascii=ascii_only)[1:-1])
    return {v for v in found if v}


class Redactor:
    def __init__(self, secrets: Iterable[str] = (), mask: str = MASK) -> None:
        parts: set[str] = set()
        for secret in secrets:
            parts |= variants(secret)
        # longest first, so a secret that contains another one is cut whole
        self._parts = sorted(parts, key=len, reverse=True)
        self.mask = mask

    def __call__(self, text: str) -> str:
        for part in self._parts:
            if part in text:
                text = text.replace(part, self.mask)
        return text

    def hits(self, text: str) -> int:
        return sum(text.count(part) for part in self._parts)
