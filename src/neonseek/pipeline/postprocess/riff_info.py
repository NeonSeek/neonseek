"""chunks of wav (riff, little endian) and aiff (iff, big endian) files, and the riff info tags.

rekordbox reads tags of wav files only from the LIST/INFO chunk (PLAN.md section 8), which
mutagen does not write, so it is done here. audio and every other chunk are copied byte for byte.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from neonseek.errors import NeonSeekError

# wav format tags that hold lossless pcm
WAVE_PCM = 0x0001
WAVE_FLOAT = 0x0003
WAVE_EXTENSIBLE = 0xFFFE
# aiff-c compression types that are plain pcm
AIFC_PCM = {b"NONE", b"sowt", b"twos", b"raw ", b"fl32", b"FL32", b"fl64", b"FL64", b"in24",
            b"in32"}  # fmt: skip


class ChunkError(NeonSeekError):
    pass


@dataclass(frozen=True)
class Chunk:
    id: bytes
    offset: int  # start of the 8 byte header
    size: int  # data size without the pad byte
    padded: bool = True  # some writers leave out the pad byte after an odd size

    @property
    def data_offset(self) -> int:
        return self.offset + 8

    @property
    def end(self) -> int:
        return self.offset + 8 + self.size + (self.size & 1 if self.padded else 0)


def _header_fits(f: BinaryIO, pos: int, file_size: int, fmt: str) -> bool:
    """a printable chunk id whose size stays inside the file."""
    f.seek(pos)
    raw = f.read(8)
    if len(raw) < 8 or not all(0x20 <= b <= 0x7E for b in raw[:4]):
        return False
    return pos + 8 + struct.unpack(fmt, raw)[1] <= file_size


def _read_chunks(f: BinaryIO, big_endian: bool, magic: tuple[bytes, ...]) -> tuple[bytes, list]:
    f.seek(0, os.SEEK_END)
    file_size = f.tell()
    f.seek(0)
    head = f.read(12)
    if len(head) < 12 or head[:4] not in magic:
        raise ChunkError("not a riff or iff file")
    form = head[8:12]
    chunks = []
    offset = 12
    fmt = ">4sI" if big_endian else "<4sI"
    while offset + 8 <= file_size:
        f.seek(offset)
        chunk_id, size = struct.unpack(fmt, f.read(8))
        if offset + 8 + size > file_size:
            if chunk_id in (b"data", b"SSND"):
                # some writers leave a wrong size on the audio chunk, the file end is the limit
                size = file_size - offset - 8
            else:
                raise ChunkError(f"chunk {chunk_id!r} runs past the end of the file")
        chunk = Chunk(chunk_id, offset, size)
        if (
            size & 1
            and chunk.end < file_size
            and not _header_fits(f, chunk.end, file_size, fmt)
            and _header_fits(f, chunk.end - 1, file_size, fmt)
        ):
            chunk = Chunk(chunk_id, offset, size, padded=False)
        chunks.append(chunk)
        offset = chunk.end
    return form, chunks


def wav_chunks(f: BinaryIO) -> list[Chunk]:
    form, chunks = _read_chunks(f, big_endian=False, magic=(b"RIFF",))
    if form != b"WAVE":
        raise ChunkError("riff file is not wave")
    return chunks


def aiff_chunks(f: BinaryIO) -> tuple[bytes, list[Chunk]]:
    return _read_chunks(f, big_endian=True, magic=(b"FORM",))


def _find(chunks: list[Chunk], chunk_id: bytes) -> Chunk | None:
    return next((c for c in chunks if c.id == chunk_id), None)


def wav_codec(path: Path | str) -> int:
    """format tag of the fmt chunk. for extensible files, the tag of the sub format."""
    with Path(path).open("rb") as f:
        chunks = wav_chunks(f)
        fmt = _find(chunks, b"fmt ")
        if fmt is None or fmt.size < 16:
            raise ChunkError("wav file without a fmt chunk")
        f.seek(fmt.data_offset)
        data = f.read(min(fmt.size, 40))
    tag = struct.unpack("<H", data[:2])[0]
    if tag == WAVE_EXTENSIBLE and len(data) >= 26:
        # the first two bytes of the sub format guid are the real format tag
        tag = struct.unpack("<H", data[24:26])[0]
    return tag


def wav_is_lossless(path: Path | str) -> bool:
    return wav_codec(path) in (WAVE_PCM, WAVE_FLOAT)


def aiff_compression(path: Path | str) -> bytes:
    """compression type of an aiff-c file, NONE for plain aiff."""
    with Path(path).open("rb") as f:
        form, chunks = aiff_chunks(f)
        if form == b"AIFF":
            return b"NONE"
        if form != b"AIFC":
            raise ChunkError("iff file is not aiff")
        comm = _find(chunks, b"COMM")
        if comm is None or comm.size < 22:
            raise ChunkError("aiff-c file without a compression type")
        f.seek(comm.data_offset + 18)
        return f.read(4)


# riff info


def _decode(raw: bytes) -> str:
    raw = raw.split(b"\0", 1)[0]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # older writers use the windows code page
        return raw.decode("cp1252", errors="replace")


def _info_list(f: BinaryIO, chunks: list[Chunk]) -> list[Chunk]:
    found = []
    for chunk in chunks:
        if chunk.id == b"LIST" and chunk.size >= 4:
            f.seek(chunk.data_offset)
            if f.read(4) == b"INFO":
                found.append(chunk)
    return found


def read_info(path: Path | str) -> dict[str, str]:
    """all LIST/INFO fields, keys like INAM. a later chunk wins over an earlier one."""
    values: dict[str, str] = {}
    with Path(path).open("rb") as f:
        chunks = wav_chunks(f)
        for chunk in _info_list(f, chunks):
            f.seek(chunk.data_offset + 4)
            body = f.read(chunk.size - 4)
            pos = 0
            while pos + 8 <= len(body):
                key, size = struct.unpack("<4sI", body[pos : pos + 8])
                raw = body[pos + 8 : pos + 8 + size]
                values[key.decode("latin-1")] = _decode(raw)
                pos += 8 + size + (size & 1)
    return values


def _info_chunk(values: dict[str, str]) -> bytes:
    body = b"INFO"
    for key, text in values.items():
        if not text:
            continue
        raw = text.encode("utf-8") + b"\0"
        body += struct.pack("<4sI", key.encode("ascii"), len(raw)) + raw
        if len(raw) & 1:
            body += b"\0"
    return struct.pack("<4sI", b"LIST", len(body)) + body


def write_info(path: Path | str, values: dict[str, str]) -> None:
    """replaces every LIST/INFO chunk with one holding values, placed right before data.

    the file is written next to the original and swapped in, so a crash keeps the old file.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".info-tmp")
    try:
        _rewrite(path, tmp, values)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def _rewrite(path: Path, tmp: Path, values: dict[str, str]) -> None:
    with path.open("rb") as src:
        chunks = wav_chunks(src)
        old_info = {c.offset for c in _info_list(src, chunks)}
        if _find(chunks, b"data") is None:
            raise ChunkError("wav file without a data chunk")
        new_info = _info_chunk(values) if any(values.values()) else b""
        with tmp.open("wb") as dst:
            dst.write(b"RIFF\0\0\0\0WAVE")
            placed = False
            for chunk in chunks:
                if chunk.offset in old_info:
                    continue
                if chunk.id == b"data" and not placed:
                    dst.write(new_info)
                    placed = True
                dst.write(struct.pack("<4sI", chunk.id, chunk.size))
                src.seek(chunk.data_offset)
                _copy(src, dst, chunk.size)
                if chunk.size & 1:
                    dst.write(b"\0")
            size = dst.tell() - 8
            dst.seek(4)
            dst.write(struct.pack("<I", size))


def _copy(src: BinaryIO, dst: BinaryIO, length: int) -> None:
    left = length
    while left:
        block = src.read(min(left, 1024 * 1024))
        if not block:
            raise ChunkError("file ended inside a chunk")
        dst.write(block)
        left -= len(block)
