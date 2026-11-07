"""artist and title from the title of a youtube video or a soundcloud upload (D-229).

uploads have one free text title. most read "Artist - Title (Official Video)", some only
"Title". the version stays (remix, edit, live, remastered), the decoration goes. when there is
no artist in the title, the channel or uploader is the artist. the user can correct both by
hand before searching (track menu, neonseek track edit).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# words that make a bracket pure decoration: (Official Video), [4K], [FREE DL], (Lyrics)
_JUNK_WORDS = (
    r"official|video|videoclip|clip|audio|lyrics?|lyric video|visuali[sz]er|4k|8k|uhd|hd|hq|"
    r"1080p|720p|free\s*(?:dl|download)|download|out now|premiere|teledysk|tekst|napisy|"
    r"m/?v|full song|sub(?:titles|s)?|color coded|letra|paroles|explicit|clean|"
    r"high[- ]?speed|full audio"
)
_JUNK_BRACKET = re.compile(
    r"\s*[\(\[\{【「](?=[^\)\]\}】」]*\b(?:" + _JUNK_WORDS + r")\b)[^\)\]\}】」]*[\)\]\}】」]",
    re.IGNORECASE,
)
# "| Official Video", "// Lyrics" at the end of a title
_JUNK_TAIL = re.compile(
    r"\s*(?:\||//|•)\s*[^|/•]*\b(?:" + _JUNK_WORDS + r")\b[^|/•]*$", re.IGNORECASE
)
# a bare "LYRICS" or "Official Video" at the very end, without a bracket
_BARE_TAIL = re.compile(
    r"\s+(?:-+\s*)?(?:official(?:\s+(?:music\s+)?(?:video|audio))?|lyrics?|lyric\s+video|"
    r"free\s*(?:dl|download))\s*$",
    re.IGNORECASE,
)
# "5. Artist - Title" or "02. Title" from numbered uploads
_NUMBER = re.compile(r"^\d{1,3}[.)]\s+(?=\S)")
_SEGMENTS = re.compile(r"\s+(?:\||//)\s+")
_PREMIERE = re.compile(r"^\s*(?:premiere|world premiere|exclusive)\s*[:|-]\s*", re.IGNORECASE)
_HASHTAGS = re.compile(r"(?:\s+#[\w-]+)+\s*$", re.UNICODE)
_DASHES = (" - ", " – ", " — ", " -- ", " ~ ")
_FEAT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+", re.IGNORECASE)
# guests on the artist side: 'A ft. B', 'A with B'
_ARTIST_GUESTS = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|with)\s+", re.IGNORECASE)
# 'Title' or "Title" after the artist: Artist 'Title'
_QUOTED = re.compile(r"^(?P<artist>.+?)\s+['\"‘“](?P<title>.+?)['\"’”]\s*(?P<rest>.*)$")
_QUOTES = "'\"‘’“”„,"
# channel name decoration
_TOPIC = re.compile(r"\s*-\s*topic$", re.IGNORECASE)
_VEVO = re.compile(r"(?<=[a-z0-9])vevo$", re.IGNORECASE)
_OFFICIAL = re.compile(r"(?:\s*-\s*|\s+|(?<=[a-z0-9]))official$", re.IGNORECASE)
# "NeonBandMusic", only glued to a lower case letter: "Neon Records Music" is a label
_MUSIC = re.compile(r"(?<=[a-z])Music$")
_CAMEL = re.compile(r"^[A-Z][a-z]+(?:[A-Z][a-z]+)+$")


@dataclass(frozen=True)
class Parsed:
    artists: tuple[str, ...]
    title: str
    # true when the artist came from the channel or uploader, not from the title
    from_uploader: bool = False


def clean_channel(name: str | None) -> str:
    """'X - Topic', 'XVEVO' and 'XOfficial' are all X."""
    text = " ".join((name or "").split())
    text = _TOPIC.sub("", text)
    text = _VEVO.sub("", text)
    text = _OFFICIAL.sub("", text)
    text = _MUSIC.sub("", text)
    # "NeonBand" was typed without the space a search needs
    if _CAMEL.match(text):
        text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    return text.strip()


def is_topic_channel(name: str | None) -> bool:
    """youtube's own channels of an artist: the title is only the track name."""
    return bool(_TOPIC.search(name or ""))


def clean_title(text: str) -> str:
    """drops decoration and keeps the version of the track."""
    text = " ".join((text or "").split())
    text = _PREMIERE.sub("", text)
    text = _HASHTAGS.sub("", text)
    previous = None
    while previous != text:
        previous = text
        text = _JUNK_BRACKET.sub("", text)
        text = _JUNK_TAIL.sub("", text)
        text = _BARE_TAIL.sub("", text)
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
    text = " ".join(text.split()).strip(" -|")
    # "Title | Film | Channel": the part with an artist dash, else the first part
    segments = [part for part in _SEGMENTS.split(text) if part.strip()]
    if len(segments) > 1:
        text = next((part for part in segments if _dash_at(part) is not None), segments[0])
    return _NUMBER.sub("", text).strip()


def _unquote(text: str) -> str:
    text = text.strip()
    while len(text) > 1 and text[0] in _QUOTES and text[-1] in _QUOTES:
        text = text.lstrip(_QUOTES).rstrip(_QUOTES).strip()
    return text


def _dash_at(text: str) -> tuple[int, str] | None:
    """the first artist dash outside of brackets: (position, dash)."""
    depth = 0
    for index, ch in enumerate(text):
        if ch in "([{【":
            depth += 1
        elif ch in ")]}】":
            depth = max(0, depth - 1)
        elif depth == 0:
            for dash in _DASHES:
                if text.startswith(dash, index):
                    return index, dash
    return None


def _split_feat(artist: str) -> tuple[str, ...]:
    """'A ft. B, C' is (A, B, C), 'A, B & C' is (A, B, C). '&' alone, 'x' and '/' stay, those
    are often one act (Duo & Partner)."""
    parts = _ARTIST_GUESTS.split(artist, maxsplit=1)
    names = [p.strip() for p in parts[0].split(", ")]
    if len(names) > 1:
        # "A, B & C" lists three, a lone "A & B" stays one act
        *head, last = names
        names = [*head, *re.split(r"\s+&\s+", last)]
    if len(parts) > 1:
        names += re.split(r",\s*|\s+&\s+", parts[1])
    names = [re.sub(r"^(?:&|and)\s+", "", n.strip(), flags=re.IGNORECASE) for n in names]
    return tuple(_unquote(n) for n in names if n.strip())


def _title_feat(title: str) -> tuple[str, tuple[str, ...]]:
    """'Song ft. B' outside brackets moves b to the artists. '(feat. B)' stays in the title,
    the way spotify writes it."""
    match = _FEAT.search(title)
    if match is None:
        return title, ()
    before = title[: match.start()]
    if before.count("(") > before.count(")") or before.count("[") > before.count("]"):
        return title, ()
    after = title[match.end() :]
    cut = min([i for i in (after.find(" ("), after.find(" [")) if i >= 0] or [len(after)])
    guests = tuple(p.strip() for p in re.split(r",|&", after[:cut]) if p.strip())
    return (before + after[cut:]).strip(), guests


def _same(a: str, b: str) -> bool:
    def norm(s: str) -> str:
        return "".join(ch for ch in s.casefold() if ch.isalnum())

    return bool(norm(a)) and norm(a) == norm(b)


def split_artist_title(raw_title: str, uploader: str | None) -> Parsed:
    """artist and title of a youtube video or a soundcloud upload."""
    channel = clean_channel(uploader)
    title = clean_title(raw_title)
    if not title:
        title = " ".join((raw_title or "").split())
    if is_topic_channel(uploader):
        clean, guests = _title_feat(title)
        return Parsed((channel, *guests) if channel else guests, clean or title)

    found = _dash_at(title)
    if found is not None:
        index, dash = found
        artist = _NUMBER.sub("", title[:index].strip())
        song = _NUMBER.sub("", _unquote(title[index + len(dash) :]))
        if artist and song:
            # "Title - Artist" with the artist as the channel: turn it round
            if channel and _same(song, channel) and not _same(artist, channel):
                artist, song = song, artist
            song, guests = _title_feat(song)
            return Parsed((*_split_feat(artist), *guests), song)

    quoted = _QUOTED.match(title)
    if quoted:
        song = (quoted.group("title") + " " + quoted.group("rest")).strip()
        song, guests = _title_feat(song)
        return Parsed((*_split_feat(quoted.group("artist")), *guests), song)

    song, guests = _title_feat(title)
    artists = (channel, *guests) if channel else guests
    return Parsed(artists, _unquote(song), from_uploader=bool(channel))


def strip_artist_prefix(title: str, artist: str) -> str:
    """soundcloud titles often repeat the artist: 'Artist - Title' with the artist known."""
    for dash in _DASHES:
        left, sep, right = title.partition(dash)
        if sep and _same(left, artist) and right.strip():
            return right.strip()
    return title
