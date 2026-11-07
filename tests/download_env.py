"""a download setup on the real pinned sockseek with --mock-files-dir: no network at all."""

from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from audio_files import PNG_1X1, write_audio
from neonseek import actions
from neonseek import secrets as secret_store
from neonseek.engine import sockseek
from neonseek.sources import ingest
from neonseek.sources.base import CollectionRecord, TrackRecord

BINARY = Path(__file__).resolve().parents[1] / "vendor" / "sockseek" / "sockseek.exe"
needs_sockseek = pytest.mark.skipif(
    not BINARY.is_file(), reason="run scripts/fetch_sockseek.ps1 to get the pinned sockseek"
)
COVER_URL = "https://covers.test/a.png"


def _serve_covers(request: httpx.Request) -> httpx.Response:
    if str(request.url).startswith("https://covers.test/"):
        return httpx.Response(200, content=PNG_1X1, headers={"content-type": "image/png"})
    raise AssertionError(f"tests must not reach the network: {request.url}")


def track(artist, title, album=None, seconds=None, **extra) -> TrackRecord:
    return TrackRecord(
        title=title,
        artists=(artist,) if artist else (),
        album=album,
        duration_ms=seconds * 1000 if seconds else None,
        **extra,
    )


@dataclass
class DownloadEnv:
    ctx: actions.AppContext
    mock: Path
    library: Path
    # per pass: extra sockseek flags like --mock-files-fail-downloads
    flags: dict[str, list[str]] = field(default_factory=lambda: {"flac": [], "fallback": []})
    events: list = field(default_factory=list)

    def file(self, rel: str, seconds: float, **options) -> Path:
        return write_audio(self.mock / rel, seconds, **options)

    def collection(self, name: str, tracks: list[TrackRecord], **values) -> int:
        record = CollectionRecord(kind="csv", origin="file", name=name, source_ref=f"C:/{name}.csv",
                                  snapshot_id=name, tracks=tracks)  # fmt: skip
        cid = ingest.ingest_collection(self.ctx.repo, record).collection_id
        if values:
            with self.ctx.repo.transaction():
                self.ctx.repo.update_collection(cid, values)
        return cid

    def ids(self, collection_id: int) -> list[int]:
        return self.ctx.repo.collection_track_ids(collection_id)

    def run(
        self,
        collection_ids: list[int],
        limit: int | None = None,
        cancel_when: Callable[[object], bool] | None = None,
    ):
        self.events = []
        with actions.DownloadJob(self.ctx, collection_ids, limit) as job:

            def on_event(event):
                self.events.append(event)
                if cancel_when is not None and cancel_when(event):
                    job.cancel()

            return job.run(on_event)

    def state(self, track_id: int):
        return self.ctx.repo.track_states([track_id])[track_id]

    def run_csv(self, run_id: int, number: int) -> list[dict] | None:
        path = self.ctx.data_dir / "logs" / f"sockseek-{run_id}-{number}.csv"
        if not path.exists():
            return None
        with path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def titles(self, run_id: int, number: int) -> list[str] | None:
        rows = self.run_csv(run_id, number)
        return None if rows is None else [r["Title"] for r in rows]

    def library_files(self) -> list[str]:
        folder = self.library / "tracks"
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def set(self, key: str, value: str) -> None:
        actions.change_setting(self.ctx, key, value)


@pytest.fixture
def env(tmp_path, monkeypatch, ctx) -> DownloadEnv:
    mock = tmp_path / "mock"
    mock.mkdir()
    library = tmp_path / "Music Library ż"
    setup = DownloadEnv(ctx, mock, library)

    def extra_args(formats: tuple[str, ...]) -> tuple[str, ...]:
        flags = setup.flags["flac" if formats == sockseek.FLAC_FORMATS else "fallback"]
        return ("--mock-files-dir", str(mock), *flags)

    monkeypatch.setattr(actions.AppContext, "sockseek_extra_args", staticmethod(extra_args))
    ctx.http_factory = lambda: httpx.Client(transport=httpx.MockTransport(_serve_covers))
    secret_store.set_soulseek_login(secret_store.SoulseekLogin("tester", "Pa55-word"))
    actions.change_setting(ctx, "library_dir", str(library))
    return setup
