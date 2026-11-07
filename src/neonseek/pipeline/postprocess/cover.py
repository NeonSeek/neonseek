"""cover: embeds the source cover when the file has none (PLAN.md section 8).

flac gets a PICTURE block, wav and aiff an id3 APIC frame. an existing cover is never replaced.
"""

from __future__ import annotations

from pathlib import Path

from mutagen.aiff import AIFF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, Encoding, PictureType
from mutagen.wave import WAVE

from neonseek.covers import MIME, sniff_type
from neonseek.i18n import tr
from neonseek.pipeline.postprocess import base
from neonseek.pipeline.postprocess.base import StepContext, StepError, TrackFile

ID3_VERSION = 3  # D-063
# rekordbox shows jpeg and png artwork only
EMBEDDABLE = {".jpg", ".png"}


def _id3_file(path: Path, fmt: str):
    return WAVE(path) if fmt == base.WAV else AIFF(path)


def has_cover(path: Path, fmt: str) -> bool:
    if fmt == base.FLAC:
        return bool(FLAC(path).pictures)
    audio = _id3_file(path, fmt)
    return audio.tags is not None and bool(audio.tags.getall("APIC"))


def embed_cover(path: Path, fmt: str, image: bytes) -> None:
    ext = sniff_type(image)
    if ext not in EMBEDDABLE:
        raise StepError(tr("The cover is not a JPEG or PNG image, rekordbox would not show it."))
    mime = MIME[ext]
    if fmt == base.FLAC:
        audio = FLAC(path)
        picture = Picture()
        picture.type = PictureType.COVER_FRONT
        picture.mime = mime
        picture.desc = ""
        picture.data = image
        audio.add_picture(picture)
        audio.save()
        return
    audio = _id3_file(path, fmt)
    if audio.tags is None:
        audio.add_tags()
    audio.tags.add(
        APIC(encoding=Encoding.LATIN1, mime=mime, type=PictureType.COVER_FRONT, desc="", data=image)
    )
    audio.save(v2_version=ID3_VERSION)


class EmbedCover:
    name = "cover"
    fatal = False

    def process(self, track_file: TrackFile, ctx: StepContext) -> TrackFile:
        track = ctx.repo.get_track(track_file.track_id)
        url = track["cover_url"] if track is not None else None
        if not url or track_file.format is None:
            return track_file
        if has_cover(track_file.path, track_file.format):
            return track_file
        image = ctx.covers.get(url).read_bytes()
        embed_cover(track_file.path, track_file.format, image)
        return track_file
