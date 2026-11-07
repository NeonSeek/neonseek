"""verify: the real format and codec of the file, never its extension (PLAN.md sections 7 and 8)."""

from __future__ import annotations

import io
from pathlib import Path

import mutagen
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.wave import WAVE

from neonseek.i18n import tr
from neonseek.pipeline.postprocess import base, riff_info
from neonseek.pipeline.postprocess.base import StepContext, StepError, TrackFile

FORMAT_FLAC, FORMAT_WAV, FORMAT_AIFF = base.FLAC, base.WAV, base.AIFF

# sockseek filters on whole seconds with --length-tol 3, one more second covers the rounding
TOLERANCE_MS = 4000


def sniff_format(path: Path) -> str | None:
    """flac, wav or aiff from the first bytes of the file, none for anything else."""
    with path.open("rb") as f:
        head = f.read(12)
        if head.startswith(b"ID3") and len(head) >= 10:
            # an id3 tag in front of flac is not standard, but such files exist
            size = 0
            for byte in head[6:10]:
                size = (size << 7) | (byte & 0x7F)
            f.seek(10 + size + (10 if head[5] & 0x10 else 0))
            return FORMAT_FLAC if f.read(4) == b"fLaC" else None
    if head.startswith(b"fLaC"):
        return FORMAT_FLAC
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return FORMAT_WAV
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return FORMAT_AIFF
    return None


def _describe(path: Path) -> str:
    # without a file name mutagen guesses from the content only, not from the extension
    try:
        with path.open("rb") as f:
            found = mutagen.File(io.BytesIO(f.read(2 * 1024 * 1024)))
    except Exception:
        found = None
    name = type(found).__name__ if found is not None else ""
    return name or tr("unknown")


class Verify:
    name = "verify"
    fatal = True

    def process(self, track_file: TrackFile, ctx: StepContext) -> TrackFile:
        path = track_file.path
        fmt = sniff_format(path)
        if fmt is None:
            raise StepError(
                tr("Not a FLAC, WAV or AIFF file (looks like: {kind}).").format(
                    kind=_describe(path)
                )
            )
        try:
            if fmt == FORMAT_FLAC:
                info = FLAC(path).info
            elif fmt == FORMAT_WAV:
                if not riff_info.wav_is_lossless(path):
                    raise StepError(
                        tr("The WAV file holds compressed audio (format {code:#06x}).").format(
                            code=riff_info.wav_codec(path)
                        )
                    )
                info = WAVE(path).info
            else:
                compression = riff_info.aiff_compression(path)
                if compression not in riff_info.AIFC_PCM:
                    raise StepError(
                        tr("The AIFF file holds compressed audio ({kind}).").format(
                            kind=compression.decode("latin-1").strip()
                        )
                    )
                info = AIFF(path).info
        except StepError:
            raise
        except (mutagen.MutagenError, riff_info.ChunkError, OSError) as exc:
            raise StepError(tr("The file is damaged ({error}).").format(error=exc)) from exc

        length_ms = round(info.length * 1000)
        if length_ms <= 0:
            raise StepError(tr("The file has no audio."))
        track = ctx.repo.get_track(track_file.track_id)
        expected = track["duration_ms"] if track is not None else None
        if expected and abs(length_ms - expected) > TOLERANCE_MS:
            raise StepError(
                tr("The file is {got} s long, the track is {want} s. Wrong version.").format(
                    got=round(length_ms / 1000), want=round(expected / 1000)
                )
            )
        track_file.format = fmt
        track_file.duration_ms = length_ms
        track_file.sample_rate = getattr(info, "sample_rate", None) or None
        track_file.bit_depth = getattr(info, "bits_per_sample", None) or None
        track_file.length_verified = bool(expected)
        return track_file
