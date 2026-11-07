"""csv import, no spotify and no network needed (PLAN.md section 6.4)."""

from __future__ import annotations

import codecs
import csv
import hashlib
import io
import re
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from neonseek.errors import NeonSeekError
from neonseek.i18n import tr
from neonseek.sources.base import KIND_CSV, ORIGIN_FILE, CollectionRecord, TrackRecord
from neonseek.spotify.urls import parse_track_id

FIELDS = ("title", "artist", "album", "length", "isrc", "spotify_id")

# matched against the whole header, case insensitive, earlier alias wins
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    # "name", "time" and "nazwa", "czas": the .txt export of itunes and the music app (D-233)
    "title": ("title", "track name", "track", "name", "song", "tytuł", "nazwa"),
    "artist": ("artist", "artists", "artist name", "artist name(s)", "wykonawca"),
    "album": ("album", "album name"),
    "length": (
        "length",
        "duration",
        "time",
        "duration (ms)",
        "track duration (ms)",
        "duration_ms",
        "czas",
    ),
    "isrc": ("isrc",),
    "spotify_id": ("track uri", "spotify uri", "uri", "track id", "spotify id"),
}

# auto picks one of these for the whole length column
LENGTH_UNITS = ("s", "ms", "clock")
MS_MEDIAN_THRESHOLD = 20000
_MS_IN_HEADER = re.compile(r"(?<![a-z])ms(?![a-z])")
_DELIMITERS = ",;\t"
_SNIFF_CHARS = 64 * 1024
PREVIEW_ROWS = 20


class CsvImportError(NeonSeekError):
    """problem with the file itself."""


class CsvMappingError(NeonSeekError):
    """the columns could not be mapped. carries the header so the ui can offer a choice."""

    def __init__(self, message: str, columns: list[str]) -> None:
        super().__init__(message)
        self.columns = columns


@dataclass
class CsvTable:
    path: Path
    encoding: str
    delimiter: str
    header: list[str]
    rows: list[list[str]]
    sha256: str


@dataclass
class CsvParse:
    table: CsvTable
    columns: dict[str, int]  # field -> column index
    length_unit: str | None  # none when there is no length column
    tracks: list[TrackRecord]
    skipped_rows: int  # rows with data but without a title
    bad_lengths: int  # length cells that could not be read, imported without length
    overrides: dict[str, str | None] = field(default_factory=dict)
    length_unit_override: str | None = None

    def column_names(self) -> dict[str, str]:
        return {name: self.table.header[index] for name, index in self.columns.items()}

    def import_map(self) -> dict[str, object]:
        return {
            "columns": self.column_names(),
            "overrides": self.overrides,
            "length_unit": self.length_unit,
            "length_unit_override": self.length_unit_override,
            "delimiter": self.table.delimiter,
            "encoding": self.table.encoding,
        }


def _looks_utf16le(data: bytes) -> bool:
    # latin text in utf-16 without a bom: almost every second byte is zero
    sample = data[:4096]
    return len(sample) >= 4 and sample[1::2].count(0) * 10 >= len(sample[1::2]) * 8


def _decode(data: bytes) -> tuple[str, str]:
    # itunes and the music app export playlists as utf-16 with a bom ("unicode text")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    if _looks_utf16le(data):
        try:
            return data.decode("utf-16-le"), "utf-16"
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        # a few cp1250 bytes are undefined, those become replacement characters
        return data.decode("cp1250", errors="replace"), "cp1250"


def _detect_delimiter(text: str) -> str:
    sample = text[:_SNIFF_CHARS]
    if len(text) > _SNIFF_CHARS and "\n" in sample:
        sample = sample[: sample.rfind("\n")]
    first = text.splitlines()[0] if text else ""
    counts = {d: first.count(d) for d in _DELIMITERS}
    best = max(counts, key=lambda d: counts[d])
    try:
        found = csv.Sniffer().sniff(sample, delimiters=_DELIMITERS).delimiter
    except csv.Error:
        # sniffer gives up on a single column or very short files, count in the header
        return best if counts[best] > 0 else ","
    # a delimiter the header does not have is a guess from the data (commas in names of an
    # itunes export with old mac line ends), the header decides then
    if counts.get(found, 0) == 0 and counts[best] > 0:
        return best
    return found


def read_table(path: Path) -> CsvTable:
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise CsvImportError(
            tr("File {path} does not exist. Check the location and choose the file again.").format(
                path=path
            )
        ) from exc
    except OSError as exc:
        raise CsvImportError(
            tr(
                "Could not read {path} ({error}). Close programs that use the file and try again."
            ).format(path=path, error=exc.strerror or exc)
        ) from exc

    text, encoding = _decode(data)
    delimiter = _detect_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        all_rows = [row for row in reader]
    except csv.Error as exc:
        raise CsvImportError(
            tr("{path} is not a valid CSV file ({error}). Export it again as CSV.").format(
                path=path, error=exc
            )
        ) from exc
    if not all_rows or not any(cell.strip() for cell in all_rows[0]):
        raise CsvImportError(
            tr("{path} is empty or has no header row. The first row must name the columns.").format(
                path=path
            )
        )
    header = [cell.strip() for cell in all_rows[0]]
    return CsvTable(
        path=path,
        encoding=encoding,
        delimiter=delimiter,
        header=header,
        rows=all_rows[1:],
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _norm_header(name: str) -> str:
    return " ".join(name.strip().strip('"').casefold().split())


def auto_columns(header: list[str]) -> dict[str, int]:
    normalized = [_norm_header(h) for h in header]
    columns: dict[str, int] = {}
    used: set[int] = set()
    for name, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            index = next(
                (i for i, h in enumerate(normalized) if h == alias and i not in used), None
            )
            if index is not None:
                columns[name] = index
                used.add(index)
                break
    return columns


def _find_column(header: list[str], wanted: str) -> int | None:
    target = _norm_header(wanted)
    return next((i for i, h in enumerate(header) if _norm_header(h) == target), None)


def resolve_columns(
    header: list[str], overrides: dict[str, str | None], require_title: bool = True
) -> dict[str, int]:
    """auto mapping with manual overrides on top. a none override unmaps the field.

    without require_title it returns what is mapped so far, for the import dialog."""
    columns = auto_columns(header)
    for name, wanted in overrides.items():
        if name not in FIELDS:
            raise CsvMappingError(
                tr("Unknown field {name}. Fields: {fields}.").format(
                    name=name, fields=", ".join(FIELDS)
                ),
                header,
            )
        if wanted is None or not wanted.strip():
            columns.pop(name, None)
            continue
        index = _find_column(header, wanted)
        if index is None:
            raise CsvMappingError(
                tr("Column {column} is not in this file. Pick one of: {columns}.").format(
                    column=wanted, columns=", ".join(header)
                ),
                header,
            )
        # a column feeds one field, the manual choice takes it from the automatic one
        for other, other_index in list(columns.items()):
            if other_index == index and other != name:
                del columns[other]
        columns[name] = index
    if require_title and "title" not in columns:
        raise CsvMappingError(
            tr(
                "Could not find the column with track titles. Pick it by hand. "
                "Columns in this file: {columns}."
            ).format(columns=", ".join(header)),
            header,
        )
    return columns


def _number(value: str, allow_zero: bool = False) -> float | None:
    text = value.strip().replace(" ", "")
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if number > 0 or (allow_zero and number == 0):
        return number
    return None


def _clock_ms(value: str) -> int | None:
    parts = [p.strip() for p in value.strip().split(":")]
    if len(parts) > 3 or not all(parts):
        return None
    if not all(p.isdigit() for p in parts[:-1]):
        return None
    last = _number(parts[-1], allow_zero=len(parts) > 1)
    if last is None:
        return None
    total = 0.0
    for p in parts[:-1]:
        total = total * 60 + int(p)
    total = total * 60 + last
    return round(total * 1000) if total > 0 else None


def detect_length_unit(header_name: str, values: Iterable[str]) -> str:
    cells = [v.strip() for v in values if v and v.strip()]
    if cells and sum(":" in v for v in cells) * 2 > len(cells):
        return "clock"
    if _MS_IN_HEADER.search(_norm_header(header_name)):
        return "ms"
    numbers = [n for n in (_number(v) for v in cells) if n is not None]
    if numbers and statistics.median(numbers) > MS_MEDIAN_THRESHOLD:
        return "ms"
    return "s"


def parse_length(value: str, unit: str) -> int | None:
    """length in milliseconds or none when the cell is empty or unreadable."""
    if not value or not value.strip():
        return None
    if unit == "clock" or ":" in value:
        return _clock_ms(value)
    number = _number(value)
    if number is None:
        return None
    return round(number) if unit == "ms" else round(number * 1000)


def split_artists(cell: str) -> tuple[str, ...]:
    # heuristic from the plan: semicolon first, then comma with a space
    separator = ";" if ";" in cell else ", "
    return tuple(part.strip() for part in cell.split(separator) if part.strip())


def parse_csv(
    path: Path,
    overrides: dict[str, str | None] | None = None,
    length_unit: str | None = None,
) -> CsvParse:
    overrides = dict(overrides or {})
    if length_unit is not None and length_unit not in LENGTH_UNITS:
        raise CsvMappingError(
            tr("Unknown length unit {unit}. Use one of: {units}.").format(
                unit=length_unit, units=", ".join(LENGTH_UNITS)
            ),
            [],
        )
    table = read_table(path)
    columns = resolve_columns(table.header, overrides)

    def cell(row: list[str], name: str) -> str:
        index = columns.get(name)
        if index is None or index >= len(row):
            return ""
        return row[index].strip()

    unit: str | None = None
    if "length" in columns:
        unit = length_unit or detect_length_unit(
            table.header[columns["length"]], (cell(r, "length") for r in table.rows)
        )

    tracks: list[TrackRecord] = []
    skipped = 0
    bad_lengths = 0
    for row in table.rows:
        if not any(c.strip() for c in row):
            continue  # blank line, not a skipped track
        title = cell(row, "title")
        if not title:
            skipped += 1
            continue
        duration = None
        raw_length = cell(row, "length")
        if unit is not None and raw_length:
            duration = parse_length(raw_length, unit)
            if duration is None:
                bad_lengths += 1
        tracks.append(
            TrackRecord(
                title=title,
                artists=split_artists(cell(row, "artist")),
                album=cell(row, "album") or None,
                duration_ms=duration,
                isrc=cell(row, "isrc") or None,
                spotify_id=parse_track_id(cell(row, "spotify_id")),
            )
        )
    return CsvParse(
        table=table,
        columns=columns,
        length_unit=unit,
        tracks=tracks,
        skipped_rows=skipped,
        bad_lengths=bad_lengths,
        overrides={k: (v.strip() if v else None) for k, v in overrides.items()},
        length_unit_override=length_unit,
    )


def to_collection(parsed: CsvParse, name: str) -> CollectionRecord:
    return CollectionRecord(
        kind=KIND_CSV,
        origin=ORIGIN_FILE,
        name=name,
        source_ref=str(parsed.table.path),
        snapshot_id=parsed.table.sha256,
        import_map=parsed.import_map(),
        track_count=len(parsed.tracks),
        tracks=parsed.tracks,
    )
