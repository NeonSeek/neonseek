"""text compared without case and accents: the track search and the manual search."""

from __future__ import annotations

import unicodedata

# letters that unicode does not split into a base letter and an accent
_NO_DECOMPOSITION = str.maketrans(
    {"ł": "l", "ø": "o", "đ": "d", "ð": "d", "þ": "th", "æ": "ae", "œ": "oe", "ı": "i"}
)


def fold(text: str) -> str:
    """case and accents do not matter: Łódź and lodz are the same."""
    decomposed = unicodedata.normalize("NFKD", text.casefold().translate(_NO_DECOMPOSITION))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def fold_map(text: str) -> tuple[str, list[int]]:
    """the folded text and, for each folded character, where it came from in the text."""
    folded, origin = [], []
    for index, ch in enumerate(text):
        for part in fold(ch):
            folded.append(part)
            origin.append(index)
    return "".join(folded), origin


def find_all(text: str, query: str) -> list[tuple[int, int]]:
    """start and end in text of every place that matches the query, folded."""
    wanted = fold(query.strip())
    if not wanted:
        return []
    folded, origin = fold_map(text)
    found = []
    start = folded.find(wanted)
    while start >= 0:
        end = start + len(wanted) - 1
        found.append((origin[start], origin[end] + 1))
        start = folded.find(wanted, start + len(wanted))
    return found
