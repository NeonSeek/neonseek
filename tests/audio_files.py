"""tiny but valid audio files for tests, written in pure python (no ffmpeg needed).

flac uses constant subframes, so a minute of silence is a few kilobytes. wav and aiff are
8 khz mono 8 bit to stay small. mp3 and m4a are only there to be rejected by extension or
by codec, so they are just a recognizable header.
"""

from __future__ import annotations

import io
import math
import struct
import wave
import zlib
from pathlib import Path

FLAC_RATE = 44100
FLAC_BLOCK = 4096
PCM_RATE = 8000


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _png_1x1() -> bytes:
    # one opaque magenta pixel, rgba, with the checksums every png reader checks
    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xd6\x27\x8f\xff")  # filter byte, then r g b a
    return (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", header) + _png_chunk(b"IDAT", pixels)
            + _png_chunk(b"IEND", b""))  # fmt: skip


# a 1x1 png, the smallest real cover image
PNG_1X1 = _png_1x1()
JPEG_HEADER = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


def _crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _utf8_number(value: int) -> bytes:
    # flac frame numbers use the utf-8 bit layout
    if value < 0x80:
        return bytes([value])
    if value < 0x800:
        return bytes([0xC0 | value >> 6, 0x80 | value & 0x3F])
    return bytes([0xE0 | value >> 12, 0x80 | (value >> 6) & 0x3F, 0x80 | value & 0x3F])


def flac_bytes(seconds: float, pad: int = 0) -> bytes:
    """44.1 khz, 16 bit, mono silence. pad adds a padding block of that size (a big file)."""
    total = round(seconds * FLAC_RATE)
    info = (FLAC_RATE << 44) | (0 << 41) | (15 << 36) | total
    streaminfo = struct.pack(">HH", FLAC_BLOCK, FLAC_BLOCK) + b"\0" * 6
    streaminfo += info.to_bytes(8, "big") + b"\0" * 16
    last = 0x00 if pad else 0x80
    out = bytearray(b"fLaC" + bytes([last]) + len(streaminfo).to_bytes(3, "big") + streaminfo)
    if pad:
        out += bytes([0x81]) + pad.to_bytes(3, "big") + b"\0" * pad
    frame_number = 0
    left = total
    while left > 0:
        size = min(FLAC_BLOCK, left)
        if size == FLAC_BLOCK:
            header = bytes([0xFF, 0xF8, 0xC9, 0x08]) + _utf8_number(frame_number)
        else:
            header = bytes([0xFF, 0xF8, 0x79, 0x08]) + _utf8_number(frame_number)
            header += struct.pack(">H", size - 1)
        header += bytes([_crc8(header)])
        # constant subframe holding one zero sample
        frame = header + b"\x00" + b"\x00\x00"
        out += frame + struct.pack(">H", _crc16(frame))
        frame_number += 1
        left -= size
    return bytes(out)


def _pcm(seconds: float) -> bytes:
    return bytes(128 + (i * 7) % 32 for i in range(round(seconds * PCM_RATE)))


def wav_bytes(seconds: float, pad: int = 0, format_tag: int = 1) -> bytes:
    """8 khz, 8 bit, mono. pad adds a junk chunk, format_tag fakes another codec."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(PCM_RATE)
        w.writeframes(_pcm(seconds))
    data = bytearray(buffer.getvalue())
    if format_tag != 1:
        data[20:22] = struct.pack("<H", format_tag)
    if pad:
        data += b"junk" + struct.pack("<I", pad) + b"\0" * pad
        data[4:8] = struct.pack("<I", len(data) - 8)
    return bytes(data)


def _extended(value: float) -> bytes:
    mantissa, exponent = math.frexp(value)
    return struct.pack(">HQ", exponent + 16382, int(mantissa * (1 << 64)))


def aiff_bytes(seconds: float) -> bytes:
    # aiff pcm is signed, 8 bit samples are plain bytes
    frames = bytes((b - 128) & 0xFF for b in _pcm(seconds))
    comm = struct.pack(">hLh", 1, len(frames), 8) + _extended(PCM_RATE)
    ssnd = struct.pack(">LL", 0, 0) + frames
    if len(ssnd) % 2:
        ssnd += b"\0"
    body = b"AIFF" + b"COMM" + struct.pack(">L", len(comm)) + comm
    body += b"SSND" + struct.pack(">L", len(ssnd)) + ssnd
    return b"FORM" + struct.pack(">L", len(body)) + body


def mp3_bytes(seconds: float) -> bytes:
    """an id3 header and mpeg-1 layer 3 frames of zeros, 128 kbps."""
    frame = b"\xff\xfb\x90\x64" + b"\0" * 413
    count = round(seconds * 44100 / 1152)
    id3 = b"ID3\x03\x00\x00\x00\x00\x00\x00"
    return id3 + frame * count


def m4a_bytes(seconds: float) -> bytes:
    # only the extension matters, sockseek never picks .m4a in our passes
    return b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A mp42isom" + b"\0" * 64


MAKERS = {
    ".flac": flac_bytes,
    ".wav": wav_bytes,
    ".aiff": aiff_bytes,
    ".aif": aiff_bytes,
    ".mp3": mp3_bytes,
    ".m4a": m4a_bytes,
}


def write_audio(path: Path, seconds: float, **options) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(MAKERS[path.suffix.lower()](seconds, **options))
    return path
