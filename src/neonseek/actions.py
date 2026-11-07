"""use cases shared by the cli and the gui. both are thin layers over these functions."""

from __future__ import annotations

import contextlib
import csv
import dataclasses
import json
import os
import platform
import secrets as _random
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

import neonseek
from neonseek import applog, config, i18n, project, recycle, themes
from neonseek import library as library_files
from neonseek import secrets as secret_store
from neonseek.covers import CoverCache
from neonseek.db import repo as db
from neonseek.engine import sockseek
from neonseek.errors import CollectionError, ConfigError, NeonSeekError
from neonseek.i18n import N_, tr
from neonseek.pipeline import playlists, policy, runner, search_budget
from neonseek.pipeline.postprocess import tag as tagging
from neonseek.redact import Redactor
from neonseek.run_lock import AlreadyRunningError, RunLock
from neonseek.sources import apple, csv_file, ingest, spotify_account, spotify_url, web_url, ytdlp
from neonseek.sources import links as weblinks
from neonseek.sources.base import (
    KIND_CSV,
    KIND_SINGLES,
    KIND_SPOTIFY_ALBUM,
    KIND_SPOTIFY_LIKED,
    KIND_SPOTIFY_PLAYLIST,
    WEB_KINDS,
    TrackRecord,
    display_name,
    row_name,
)
from neonseek.spotify import auth as spotify_auth
from neonseek.spotify.client import SpotifyClient, new_http_client

# length_unit value that drops a manual unit and goes back to detection
AUTO_UNIT = "auto"

# apple music and tidal pages and the lookup api, and yt-dlp for youtube and soundcloud. tests swap
# both, nothing in the tests may reach those services
new_web_client = apple.new_web_client
new_ytdlp = ytdlp.YtDlp


class AppContext:
    """one user's data dir with a lazily opened database."""

    # tests point sockseek at a fake process or add --mock-files-dir (per format list).
    # a normal run leaves both empty and uses the bundled sockseek.exe
    sockseek_command: list[str] | None = None
    sockseek_extra_args: Callable[[tuple[str, ...]], tuple[str, ...]] | None = None
    # the soulseek search window across processes. tests on a mock library set it to 0
    search_window_s: float = search_budget.RENEW_S

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or config.data_dir()
        self._repo: db.Repo | None = None
        self._http = None
        self._spotify: SpotifyClient | None = None
        self._web_http = None
        # tests swap these for fakes, nothing in the tests may reach spotify
        self.http_factory = new_http_client
        self.web_http_factory = new_web_client
        self.ytdlp_factory = new_ytdlp
        self.token_store: spotify_auth.TokenStore = spotify_auth.KeyringTokenStore()
        # the only way a file is deleted. tests use a fake bin, never the real one
        self.recycle_bin: recycle.RecycleBin = recycle.default_bin()

    @property
    def repo(self) -> db.Repo:
        if self._repo is None:
            self._repo = db.Repo(db.connect(config.db_path(self.data_dir)))
        return self._repo

    def settings(self) -> config.Settings:
        return config.load_settings(config.settings_path(self.data_dir))

    def save_settings(self, settings: config.Settings) -> None:
        config.save_settings(settings, config.settings_path(self.data_dir))

    @property
    def http(self):
        if self._http is None:
            self._http = self.http_factory()
        return self._http

    def spotify_auth(self) -> spotify_auth.SpotifyAuth:
        return spotify_auth.SpotifyAuth(
            self.settings().spotify_client_id, self.http, self.token_store
        )

    def spotify(self) -> SpotifyClient:
        """client for the logged in user. asks for a login instead of failing later."""
        if self._spotify is None:
            auth = self.spotify_auth()
            if not auth.logged_in():
                raise spotify_auth.not_logged_in()
            self._spotify = SpotifyClient(auth, self.http)
        return self._spotify

    def web(self) -> web_url.WebClients:
        """apple music, youtube, soundcloud and tidal. yt-dlp keeps its cache in the data folder."""
        if self._web_http is None:
            self._web_http = self.web_http_factory()
        log = applog.AppLog(self.data_dir)
        return web_url.WebClients(
            http=self._web_http,
            extractor=self.ytdlp_factory(
                config.cache_dir(self.data_dir) / "yt-dlp",
                lambda message: log.write(f"yt-dlp: {message}", "warn"),
            ),
        )

    def fork(self) -> AppContext:
        """the same user data for another thread: sqlite connections stay in their thread."""
        other = AppContext(self.data_dir)
        other.http_factory = self.http_factory
        other.web_http_factory = self.web_http_factory
        other.ytdlp_factory = self.ytdlp_factory
        other.token_store = self.token_store
        other.recycle_bin = self.recycle_bin
        return other

    def close(self) -> None:
        if self._repo is not None:
            self._repo.conn.close()
            self._repo = None
        if self._http is not None:
            self._http.close()
            self._http = None
        if self._web_http is not None:
            self._web_http.close()
            self._web_http = None
        self._spotify = None


class MissingSourceFileError(CollectionError):
    def __init__(self, message: str, collection_id: int, path: str) -> None:
        super().__init__(message)
        self.collection_id = collection_id
        self.path = path


@dataclass
class CsvImportOutcome:
    collection_id: int
    name: str
    created: bool
    unchanged: bool
    renamed: bool
    parse: csv_file.CsvParse
    result: ingest.IngestResult | None


def _stored_map(row) -> dict:
    raw = row["import_map_json"] if row is not None else None
    return json.loads(raw) if raw else {}


def _csv_collection(ctx: AppContext, collection_id: int):
    row = ctx.repo.get_collection(collection_id)
    if row is None:
        raise CollectionError(
            tr("There is no collection with id {id}. Check the list of collections.").format(
                id=collection_id
            )
        )
    if row["kind"] != KIND_CSV:
        raise CollectionError(
            tr(
                "Collection {id} does not come from a CSV file. Spotify collections are "
                "updated with sync."
            ).format(id=collection_id)
        )
    return row


def _effective_options(
    stored: dict,
    overrides: dict[str, str | None] | None,
    length_unit: str | None,
) -> tuple[dict[str, str | None], str | None]:
    merged = dict(stored.get("overrides") or {})
    if overrides:
        merged.update(overrides)
    if length_unit is None:
        unit = stored.get("length_unit_override")
    elif length_unit == AUTO_UNIT:
        unit = None
    else:
        unit = length_unit
    return merged, unit


def preview_csv(
    path: Path,
    overrides: dict[str, str | None] | None = None,
    length_unit: str | None = None,
) -> csv_file.CsvParse:
    unit = None if length_unit in (None, AUTO_UNIT) else length_unit
    return csv_file.parse_csv(Path(path).resolve(), overrides, unit)


@dataclass
class CsvPreview:
    """what the import dialog shows for one mapping. read in a worker thread (D-111)."""

    header: list[str]
    auto: dict[str, str]  # field -> column found by the header names
    columns: dict[str, str]  # field -> column the dialog shows as chosen
    parse: csv_file.CsvParse | None  # none when the mapping has no title column
    error: str | None = None


def csv_preview(
    path: Path,
    overrides: dict[str, str | None] | None = None,
    length_unit: str | None = None,
) -> CsvPreview:
    """the file read with a mapping. a file that cannot be read raises, a mapping without a
    title column gives what is mapped so far, so the user can pick the title."""
    table = csv_file.read_table(Path(path).resolve())
    header = table.header
    auto = {name: header[i] for name, i in csv_file.auto_columns(header).items()}
    try:
        parsed = preview_csv(path, overrides, length_unit)
    except csv_file.CsvMappingError as exc:
        try:
            found = csv_file.resolve_columns(header, overrides or {}, require_title=False)
        except csv_file.CsvMappingError:
            found = csv_file.auto_columns(header)
        columns = {name: header[i] for name, i in found.items()}
        return CsvPreview(header, auto, columns, None, exc.message)
    return CsvPreview(header, auto, parsed.column_names(), parsed)


def import_csv(
    ctx: AppContext,
    path: Path,
    *,
    name: str | None = None,
    overrides: dict[str, str | None] | None = None,
    length_unit: str | None = None,
    collection_id: int | None = None,
) -> CsvImportOutcome:
    """imports a csv file. the same path updates its collection instead of adding a second one.

    collection_id points an existing csv collection at a new file location.
    """
    repo = ctx.repo
    path = Path(path).resolve()
    if collection_id is not None:
        existing = _csv_collection(ctx, collection_id)
        other = repo.find_collection_by_path(KIND_CSV, str(path))
        if other is not None and other["id"] != collection_id:
            raise CollectionError(
                tr(
                    "{path} is already imported as collection {id}. Re-import that "
                    "collection instead."
                ).format(path=path, id=other["id"])
            )
    else:
        existing = repo.find_collection_by_path(KIND_CSV, str(path))

    stored = _stored_map(existing)
    merged, unit = _effective_options(stored, overrides, length_unit)
    parsed = csv_file.parse_csv(path, merged, unit)
    name = name.strip() if name and name.strip() else None

    if existing is not None:
        cid = int(existing["id"])
        same = (
            existing["snapshot_id"] == parsed.table.sha256
            and db.same_path(existing["source_ref"] or "", str(path))
            and stored.get("columns") == parsed.column_names()
            and stored.get("length_unit") == parsed.length_unit
        )
        if same:
            renamed = bool(name and name != existing["name"])
            if renamed or stored.get("overrides") != parsed.overrides:
                with repo.transaction():
                    repo.update_collection(
                        cid,
                        {
                            "name": name or existing["name"],
                            "import_map_json": json.dumps(
                                parsed.import_map(), ensure_ascii=False, sort_keys=True
                            ),
                        },
                    )
            return CsvImportOutcome(
                collection_id=cid,
                name=name or existing["name"],
                created=False,
                unchanged=True,
                renamed=renamed,
                parse=parsed,
                result=None,
            )
        final_name = name or existing["name"]
    else:
        cid = None
        final_name = name or path.stem

    record = csv_file.to_collection(parsed, final_name)
    result = ingest.ingest_collection(repo, record, collection_id=cid)
    return CsvImportOutcome(
        collection_id=result.collection_id,
        name=final_name,
        created=result.created,
        unchanged=False,
        renamed=bool(existing is not None and final_name != existing["name"]),
        parse=parsed,
        result=result,
    )


def reimport_csv(ctx: AppContext, collection_id: int) -> CsvImportOutcome:
    row = _csv_collection(ctx, collection_id)
    path = row["source_ref"] or ""
    if not path or not Path(path).is_file():
        raise MissingSourceFileError(
            tr(
                "The file of collection {id} is no longer at {path}. Choose its new "
                "location to re-import it. The collection and its tracks stay as they are."
            ).format(id=collection_id, path=path),
            collection_id,
            path,
        )
    return import_csv(ctx, Path(path), collection_id=collection_id)


def collection_entries(ctx: AppContext) -> dict[int, int]:
    """places in each list as the source gives them, repeats included (D-259)."""
    return ctx.repo.entry_counts()


def list_collections(ctx: AppContext) -> list[db.CollectionSummary]:
    """the collections as the window and the cli show them: the built in lists have their
    names in the language of the user (D-180)."""
    return [
        dataclasses.replace(c, name=display_name(c.kind, c.name))
        for c in ctx.repo.list_collections()
    ]


def _require_collections(ctx: AppContext, ids: Iterable[int]) -> list[int]:
    ids = list(dict.fromkeys(ids))
    missing = [i for i in ids if ctx.repo.get_collection(i) is None]
    if missing:
        raise CollectionError(
            tr("There is no collection with id {ids}. Check the list of collections.").format(
                ids=", ".join(str(i) for i in missing)
            )
        )
    return ids


def set_selected(ctx: AppContext, ids: Iterable[int], selected: bool) -> list[int]:
    ids = _require_collections(ctx, ids)
    with ctx.repo.transaction():
        for collection_id in ids:
            ctx.repo.update_collection(collection_id, {"selected": int(selected)})
    return ids


def remove_collections(ctx: AppContext, ids: Iterable[int]) -> list[int]:
    """removes collections from the app. tracks, downloads and files stay."""
    ids = _require_collections(ctx, ids)
    with ctx.repo.transaction():
        for collection_id in ids:
            ctx.repo.delete_collection(collection_id)
    return ids


def selected_collection_ids(ctx: AppContext) -> list[int]:
    return sorted(c.id for c in ctx.repo.list_collections() if c.selected)


# tracks and their files (PLAN.md sections 5 and 8). nothing here runs during a download


class LibraryBusyError(NeonSeekError):
    pass


@contextlib.contextmanager
def _no_download(ctx: AppContext):
    """the run lock of downloads: excluding, deleting and moving wait for the download."""
    lock = RunLock(ctx.data_dir / "download.lock")
    try:
        lock.acquire()
    except AlreadyRunningError:
        raise LibraryBusyError(
            tr(
                "A download or a library move is running. Wait until it ends or cancel it, "
                "then try again."
            )
        ) from None
    try:
        yield
    finally:
        lock.release()


def _require_tracks(ctx: AppContext, track_ids: Iterable[int]) -> list[int]:
    ids = list(dict.fromkeys(track_ids))
    found = ctx.repo.track_states(ids)
    missing = [i for i in ids if i not in found]
    if missing:
        raise CollectionError(
            tr(
                "There is no track with id {ids}. Track IDs are in neonseek tracks and neonseek "
                "report."
            ).format(ids=", ".join(str(i) for i in missing))
        )
    return ids


def exclude_tracks(ctx: AppContext, track_ids: Iterable[int]) -> list[int]:
    """tracks without a file stop being downloaded (D-126). returns the ids that changed."""
    ids = _require_tracks(ctx, track_ids)
    with _no_download(ctx):
        return ctx.repo.exclude(ids)


def include_tracks(ctx: AppContext, track_ids: Iterable[int]) -> list[int]:
    """excluded tracks go back to the status they had before. returns the ids that changed."""
    ids = _require_tracks(ctx, track_ids)
    with _no_download(ctx):
        return ctx.repo.include(ids)


@dataclass
class TrackInfo:
    row: object  # track and download columns (repo.track_states)
    collections: list[tuple[int, str]]
    file_exists: bool | None  # none when the track has no file


def track_info(ctx: AppContext, track_id: int) -> TrackInfo:
    """everything known about one track, for properties and neonseek track info."""
    (tid,) = _require_tracks(ctx, [track_id])
    row = ctx.repo.track_states([tid])[tid]
    collections = [(c["id"], row_name(c)) for c in ctx.repo.collections_of_track(tid)]
    exists = None
    if row["status"] == db.DONE and row["file_path"]:
        exists = Path(row["file_path"]).is_file()
    return TrackInfo(row, collections, exists)


@dataclass(frozen=True)
class FileRef:
    track_id: int
    label: str
    path: Path


def _bin_problem(ctx: AppContext, paths: Iterable[Path]) -> str | None:
    for path in paths:
        problem = ctx.recycle_bin.problem(path)
        if problem:
            return problem
    return None


@dataclass
class DeletePlan:
    """what delete file would do. only files neonseek placed in the library go (D-127)."""

    files: list[FileRef]  # go to the recycle bin
    gone: list[FileRef]  # the file is no longer there, the track is excluded anyway
    outside: list[FileRef]  # outside the library folder, never touched
    without_file: list[int]  # not downloaded, nothing to delete
    problem: str | None = None  # why the recycle bin cannot take them


def delete_plan(ctx: AppContext, track_ids: Iterable[int]) -> DeletePlan:
    ids = _require_tracks(ctx, track_ids)
    library = library_dir(ctx.settings())
    states = ctx.repo.track_states(ids)
    plan = DeletePlan([], [], [], [])
    for tid in ids:
        row = states[tid]
        if row["status"] != db.DONE or not row["file_path"]:
            plan.without_file.append(tid)
            continue
        ref = FileRef(tid, runner.track_label(row), Path(row["file_path"]))
        if not ref.path.exists():
            plan.gone.append(ref)
        elif not library_files.owned_file(row["file_path"], library):
            plan.outside.append(ref)
        else:
            plan.files.append(ref)
    plan.problem = _bin_problem(ctx, (ref.path for ref in plan.files))
    return plan


@dataclass
class DeleteOutcome:
    recycled: list[FileRef] = field(default_factory=list)
    gone: list[FileRef] = field(default_factory=list)
    outside: list[FileRef] = field(default_factory=list)
    failed: list[tuple[FileRef, str]] = field(default_factory=list)
    # gone from the library, but the recycle bin record was not found: check the bin
    unconfirmed: list[tuple[FileRef, str]] = field(default_factory=list)
    playlists: list[Path] = field(default_factory=list)


# what became of a file sent to the recycle bin
RECYCLED = "recycled"
UNCONFIRMED = "unconfirmed"
NOT_DELETED = "not_deleted"


def _recycle(ctx: AppContext, path: Path) -> tuple[str, str | None]:
    """one file to the recycle bin. a file that left the library is never treated as still
    there, even when the bin did not confirm it: the database follows the disk."""
    try:
        ctx.recycle_bin.recycle(path)
    except NeonSeekError as exc:
        problem = exc.message
    except OSError as exc:
        problem = exc.strerror or str(exc)
    else:
        return RECYCLED, None
    return (NOT_DELETED if path.exists() else UNCONFIRMED), problem


def delete_track_files(ctx: AppContext, track_ids: Iterable[int]) -> DeleteOutcome:
    """delete file (D-127): files to the recycle bin, the tracks become excluded and the
    playlists of all their collections are written again at once."""
    with _no_download(ctx):
        plan = delete_plan(ctx, track_ids)
        if plan.files and plan.problem:
            raise recycle.RecycleError(plan.problem)
        outcome = DeleteOutcome(gone=plan.gone, outside=plan.outside)
        for ref in plan.files:
            result, problem = _recycle(ctx, ref.path)
            if result == NOT_DELETED:
                outcome.failed.append((ref, problem))
                continue
            # a track whose file left the library is never done again
            ctx.repo.file_gone(ref.track_id, exclude=True)
            if result == UNCONFIRMED:
                outcome.unconfirmed.append((ref, problem))
            else:
                outcome.recycled.append(ref)
        for ref in plan.gone:
            ctx.repo.file_gone(ref.track_id, exclude=True)
        sure = [ref for ref, _ in outcome.unconfirmed]
        changed = [ref.track_id for ref in outcome.recycled + outcome.gone + sure]
        library = library_dir(ctx.settings())
        outcome.playlists = [
            playlists.write_playlist(ctx.repo, cid, library)
            for cid in ctx.repo.collections_of_tracks(changed)
        ]
    return outcome


@dataclass
class RemovePlan:
    """removing collections with their files: only tracks in no other collection (D-127)."""

    collections: list[tuple[int, str]]
    files: list[FileRef]
    gone: list[FileRef]  # their file is already missing
    playlists: list[Path]  # playlist files of these collections
    shared: int  # downloaded tracks also in other collections, their files stay
    outside: int  # files outside the library folder, never touched
    problem: str | None = None

    @property
    def count(self) -> int:
        return len(self.files) + len(self.playlists)


def remove_plan(ctx: AppContext, collection_ids: Iterable[int]) -> RemovePlan:
    ids = _require_collections(ctx, collection_ids)
    repo = ctx.repo
    names = [(cid, row_name(repo.get_collection(cid))) for cid in ids]
    settings = ctx.settings()
    plan = RemovePlan(names, [], [], [], 0, 0)
    if not settings.library_dir:
        return plan
    library = Path(settings.library_dir)
    removed = set(ids)
    track_ids: list[int] = []
    for cid in ids:
        track_ids += repo.collection_track_ids(cid)
    states = repo.track_states(track_ids)
    for tid in dict.fromkeys(track_ids):
        row = states[tid]
        if row["status"] != db.DONE or not row["file_path"]:
            continue
        others = {c["id"] for c in repo.collections_of_track(tid)} - removed
        if others:
            plan.shared += 1
            continue
        ref = FileRef(tid, runner.track_label(row), Path(row["file_path"]))
        if not ref.path.exists():
            plan.gone.append(ref)
        elif not library_files.owned_file(row["file_path"], library):
            plan.outside += 1
        else:
            plan.files.append(ref)
    for cid in ids:
        path = playlists.playlist_path(repo, cid, library)
        if path.is_file():
            plan.playlists.append(path)
    plan.problem = _bin_problem(ctx, [ref.path for ref in plan.files] + plan.playlists)
    return plan


@dataclass
class RemoveOutcome:
    ids: list[int]
    recycled: list[FileRef] = field(default_factory=list)
    playlists: list[Path] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # file, problem
    unconfirmed: list[tuple[str, str]] = field(default_factory=list)  # gone, bin not confirmed


def remove_collections_and_files(ctx: AppContext, collection_ids: Iterable[int]) -> RemoveOutcome:
    """remove with "also move downloaded files to the recycle bin" (D-127)."""
    with _no_download(ctx):
        plan = remove_plan(ctx, collection_ids)
        if plan.count and plan.problem:
            raise recycle.RecycleError(plan.problem)
        ids = [cid for cid, _ in plan.collections]
        outcome = RemoveOutcome(ids)
        gone: list[FileRef] = list(plan.gone)
        for path in plan.playlists:
            result, problem = _recycle(ctx, path)
            if result == NOT_DELETED:
                outcome.failed.append((str(path), problem))
            elif result == UNCONFIRMED:
                outcome.unconfirmed.append((str(path), problem))
            else:
                outcome.playlists.append(path)
        for ref in plan.files:
            result, problem = _recycle(ctx, ref.path)
            if result == NOT_DELETED:
                outcome.failed.append((str(ref.path), problem))
                continue
            if result == UNCONFIRMED:
                outcome.unconfirmed.append((str(ref.path), problem))
                gone.append(ref)
            else:
                outcome.recycled.append(ref)
        remove_collections(ctx, ids)
        # in no collection now: not downloaded, a list added again downloads them again
        for ref in outcome.recycled + gone:
            ctx.repo.file_gone(ref.track_id, exclude=False)
    return outcome


# moving the library to another folder (D-128)


def library_files_in(ctx: AppContext, folder: str) -> int:
    """downloaded files neonseek placed in a library folder, a hint after a change."""
    return sum(
        1
        for row in ctx.repo.downloaded_files()
        if library_files.owned_file(row["file_path"], Path(folder))
    )


def library_move_plan(ctx: AppContext, folder: str) -> library_files.MovePlan:
    old = library_dir(ctx.settings())
    new = Path(config.check_library_dir(folder))
    problem = library_files.target_problem(old, new)
    if problem:
        raise ConfigError(problem)
    plan = library_files.plan_move(ctx.repo, old, new)
    if plan.too_long:
        raise ConfigError(too_long_message(plan))
    return plan


TOO_LONG_SHOWN = 10


def too_long_message(plan: library_files.MovePlan) -> str:
    """a move that would give files a path over the windows limit is refused, with the
    files, before anything moves."""
    names = [f"  {path.name} ({length})" for path, length in plan.too_long[:TOO_LONG_SHOWN]]
    if len(plan.too_long) > TOO_LONG_SHOWN:
        names.append(tr("  ... and {count} more").format(count=len(plan.too_long) - TOO_LONG_SHOWN))
    head = tr(
        "In {folder} these files would get a path longer than {limit} characters, which "
        "Windows programs and the Recycle Bin cannot handle:"
    ).format(folder=plan.new, limit=library_files.MAX_PATH_CHARS)
    tail = tr("Choose a folder with a shorter path. Nothing was moved.")
    return "\n".join([head, *names, tail])


def size_label(size: int) -> str:
    if size >= 1024**3:
        return f"{size / 1024**3:.1f} GB"
    return f"{size / 1024**2:.0f} MB"


@dataclass
class LibraryMoveOutcome:
    plan: library_files.MovePlan
    result: library_files.MoveResult
    complete: bool
    playlists: list[Path]
    recovered: int = 0


def move_library(
    ctx: AppContext,
    folder: str,
    progress: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> LibraryMoveOutcome:
    """moves the files neonseek placed to a new library folder. the setting changes only
    when every file arrived, an interruption never loses a file (PLAN.md section 8)."""
    repo = ctx.repo
    with _no_download(ctx):
        recovered = library_files.recover_moves(repo)
        plan = library_move_plan(ctx, folder)
        if plan.needs_space:
            raise ConfigError(
                tr(
                    "The new folder needs {size} free, its drive has {free}. Free some space or "
                    "choose another drive. Nothing was moved."
                ).format(size=size_label(plan.size), free=size_label(plan.free or 0))
            )
        try:
            plan.new.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(
                tr("Could not create {path} ({error}). Choose another folder.").format(
                    path=plan.new, error=exc.strerror or exc
                )
            ) from exc
        result = library_files.move_files(
            repo, plan, progress or (lambda *_: None), cancelled or (lambda: False)
        )
        complete = result.complete(plan)
        targets = library_files.playlist_collections(
            repo, plan.old, result.moved + recovered.track_ids
        )
        if complete:
            library_files.move_playlists(repo, targets, plan.old, plan.new)
            settings = ctx.settings()
            settings.library_dir = str(plan.new)
            ctx.save_settings(settings)
            where = plan.new
        else:
            # the old folder stays the library, its playlists show where the files are now
            where = plan.old
        written = [playlists.write_playlist(repo, cid, where) for cid in targets]
    return LibraryMoveOutcome(plan, result, complete, written, len(recovered))


def recover_library_move(ctx: AppContext) -> int:
    """at start: settles a library move a crash interrupted. skipped while a download runs."""
    if not ctx.repo.pending_moves():
        return 0
    try:
        with _no_download(ctx):
            found = library_files.recover_moves(ctx.repo)
            library = ctx.settings().library_dir
            if found and library:
                for cid in library_files.playlist_collections(
                    ctx.repo, Path(library), found.track_ids
                ):
                    playlists.write_playlist(ctx.repo, cid, Path(library))
            return len(found)
    except LibraryBusyError:
        return 0


# settings


def change_setting(ctx: AppContext, key: str, value: str) -> object:
    settings = ctx.settings()
    stored = config.set_setting(settings, key, value)
    if key == "theme":
        found = available_themes(ctx)
        if stored not in found:
            raise ConfigError(
                tr("There is no theme {name}. Themes: {names}.").format(
                    name=stored, names=", ".join(found)
                )
            )
    ctx.save_settings(settings)
    if key == "language":
        i18n.set_language(str(stored))
    return stored


def start_language(ctx: AppContext, remember: bool) -> str:
    """the language of the settings for this run. without one, windows decides, and the window
    remembers that choice, so it stays even when windows changes later (D-163)."""
    try:
        settings = ctx.settings()
    except ConfigError:
        # a damaged settings file is reported later, in the language of windows
        return i18n.set_language(i18n.detect_language())
    code = settings.language or i18n.detect_language()
    if remember and not settings.language:
        settings.language = code
        # a failed save asks windows again at the next start
        with contextlib.suppress(OSError):
            ctx.save_settings(settings)
    return i18n.set_language(code)


def available_themes(ctx: AppContext, log=lambda _: None) -> dict[str, themes.Theme]:
    """built in themes and the user's own toml files from the themes folder."""
    return themes.load_themes(ctx.data_dir, log)


class NoLibraryError(ConfigError):
    pass


def library_dir(settings: config.Settings) -> Path:
    if not settings.library_dir:
        raise NoLibraryError(tr("Choose the folder for your music library first, in Settings."))
    return Path(settings.library_dir)


# collection tags (PLAN.md section 8)


def _one_line_or_none(text: str) -> str | None:
    # tags are one line, an empty text clears the field
    return " ".join(text.split()) or None


def clean_tag(text: str) -> str | None:
    """a genre or comment as it is stored: one line, none when empty."""
    return _one_line_or_none(text)


def set_collection_tags(
    ctx: AppContext, collection_id: int, genre: str | None = None, comment: str | None = None
):
    """genre and comment written into the files of this collection. none keeps a value,
    an empty text clears it. files do not change until retag."""
    _require_collections(ctx, [collection_id])
    values = {}
    if genre is not None:
        values["genre"] = _one_line_or_none(genre)
    if comment is not None:
        values["comment"] = _one_line_or_none(comment)
    if values:
        with ctx.repo.transaction():
            ctx.repo.update_collection(collection_id, values)
    return ctx.repo.get_collection(collection_id)


@dataclass
class RetagOutcome:
    written: int = 0
    unchanged: int = 0
    missing: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def retag(ctx: AppContext, collection_ids: Iterable[int]) -> RetagOutcome:
    """writes tags again into downloaded files, without downloading anything."""
    ids = _require_collections(ctx, collection_ids)
    settings = ctx.settings()
    outcome = RetagOutcome()
    track_ids: list[int] = []
    for cid in ids:
        track_ids += ctx.repo.collection_track_ids(cid)
    states = ctx.repo.track_states(track_ids)
    for track_id in dict.fromkeys(track_ids):
        row = states.get(track_id)
        if row is None or row["status"] != db.DONE or not row["file_path"]:
            continue
        label = runner.track_label(row)
        path = Path(row["file_path"])
        if not path.is_file():
            outcome.missing.append(f"{label} ({path})")
            continue
        try:
            values = tagging.tag_values(ctx.repo, track_id)
            changed = tagging.write_tags(path, row["format"], values, settings.tags_from_source)
        except Exception as exc:
            outcome.failed.append((label, str(exc) or type(exc).__name__))
            continue
        if changed:
            outcome.written += 1
        else:
            outcome.unchanged += 1
    return outcome


def reload_tags_hint() -> str:
    return tr(
        "rekordbox does not see new tags by itself: select the tracks in rekordbox, right-click "
        "and choose Reload Tag."
    )


# download (PLAN.md sections 7 and 8)


@dataclass
class DownloadPlan:
    collections: list[tuple[int, str]]
    track_ids: list[int]  # what this run works on, in collection order
    first_pass: int  # searched as flac
    fallback: int  # already known for the wav/aiff pass
    done: int
    total: int
    empty: list[tuple[int, str]]  # collections without any track read yet
    search_estimate_s: int
    # on_missing_flac = lossless: a wav/aiff pass may follow, the summary says so only then
    fallback_pass: bool = True
    # chosen tracks of "download this track only" (D-125), empty for collections
    chosen: list[tuple[int, str]] = field(default_factory=list)

    @property
    def nothing_to_do(self) -> bool:
        return not self.track_ids


class DownloadJob:
    """holds the run lock from the summary to the end of the run."""

    def __init__(
        self,
        ctx: AppContext,
        collection_ids: Iterable[int],
        limit: int | None = None,
        track_ids: Iterable[int] | None = None,
    ):
        """collections, or with track_ids only these tracks (D-125). the playlists of their
        collections are written again afterwards."""
        self.ctx = ctx
        self._runner: runner.Runner | None = None
        self._cancelled = False
        self.chosen = None if track_ids is None else list(track_ids)
        self._lock = RunLock(ctx.data_dir / "download.lock")
        self._lock.acquire()
        try:
            self._prepare(list(collection_ids), limit)
        except BaseException:
            self._lock.release()
            raise

    def _prepare(self, collection_ids: list[int], limit: int | None) -> None:
        ctx = self.ctx
        if self.chosen is None:
            ids = _require_collections(ctx, collection_ids)
        else:
            self.chosen = _require_tracks(ctx, self.chosen)
            ids = ctx.repo.collections_of_tracks(self.chosen)
        self.settings = ctx.settings()
        self.library = library_dir(self.settings)
        self.login = secret_store.get_soulseek_login()
        if self.login is None:
            raise NoSoulseekLoginError(no_soulseek_login_message())
        repo = ctx.repo
        # whatever an earlier crash left in progress starts again, stage and attempts stay
        repo.reset_in_progress()
        if self.settings.on_missing_flac == config.NOT_FOUND:
            repo.revert_fallback()

        ordered: list[int] = []
        names, empty = [], []
        for cid in ids:
            row = repo.get_collection(cid)
            names.append((cid, row_name(row)))
            tracks = repo.collection_track_ids(cid)
            if not tracks:
                empty.append((cid, row_name(row)))
            ordered += tracks
        if self.chosen is not None:
            ordered, empty = self.chosen, []
        ordered = list(dict.fromkeys(ordered))
        states = repo.track_states(ordered)
        s = self.settings

        def state(tid: int) -> tuple[str, str, int] | None:
            r = states[tid]
            if self.chosen is None:
                return r["status"], r["stage"], r["attempts"]
            # chosen tracks are made ready only after the user said start
            return policy.state_for_own_download(
                r["status"], r["stage"], r["attempts"], r["excluded_from"]
            )

        def first(tid: int) -> bool:
            now = state(tid)
            return now is not None and policy.wants_pass1(*now, s)

        def second(tid: int) -> bool:
            now = state(tid)
            return now is not None and policy.wants_pass2(*now, s)

        todo = [tid for tid in ordered if first(tid) or second(tid)]
        if limit is not None:
            todo = todo[:limit]
        pass1 = sum(1 for tid in todo if first(tid))
        self.plan = DownloadPlan(
            collections=names,
            track_ids=todo,
            first_pass=pass1,
            fallback=sum(1 for tid in todo if second(tid)),
            done=sum(1 for r in states.values() if r["status"] == db.DONE),
            total=len(ordered),
            empty=empty,
            search_estimate_s=search_budget.search_estimate_s(pass1),
            fallback_pass=s.on_missing_flac == config.LOSSLESS,
            chosen=[(tid, runner.track_label(states[tid])) for tid in self.chosen or []],
        )

    def _engine(self, run_id: int, number: int, formats: tuple[str, ...], per_time: int | None):
        ctx = self.ctx
        logs = config.logs_dir(ctx.data_dir)
        extra: list[str] = []
        if per_time is not None:
            # only ever lowered, never above the sockseek default (PLAN.md section 0). the
            # budget never gives 0 (it waits instead), 1 is the floor should it ever happen
            per_time = max(1, min(per_time, search_budget.SEARCHES_PER_TIME))
            extra += ["--searches-per-time", str(per_time)]
        if ctx.sockseek_extra_args is not None:
            extra += list(ctx.sockseek_extra_args(formats))
        return sockseek.SockseekEngine(
            sockseek.SockseekSettings(
                login=self.login,
                output_dir=self.library / runner.STAGING_DIR,
                run_csv=logs / f"sockseek-{run_id}-{number}.csv",
                log_file=logs / f"sockseek-{run_id}-{number}.log",
                formats=formats,
                pref_formats=(
                    sockseek.FALLBACK_PREFERRED if formats == sockseek.FALLBACK_FORMATS else ()
                ),
                idle_timeout_s=self.settings.idle_timeout_minutes * 60,
                command=ctx.sockseek_command,
                extra_args=tuple(extra),
            )
        )

    def run(self, on_event: Callable[[object], None]) -> runner.RunOutcome:
        ctx = self.ctx
        try:
            self.library.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(
                tr(
                    "Could not create the library folder {path} ({error}). Choose another "
                    "folder in Settings."
                ).format(path=self.library, error=exc.strerror or exc)
            ) from exc
        if self.chosen is not None and not self._cancelled:
            # what the summary promised: excluded come back, not found and failed retry
            ctx.repo.prepare_for_download(self.plan.track_ids)
        self._runner = runner.Runner(
            repo=ctx.repo,
            settings=self.settings,
            library_dir=self.library,
            covers=CoverCache(config.covers_dir(ctx.data_dir), lambda: ctx.http),
            logs_dir=config.logs_dir(ctx.data_dir),
            engine_factory=self._engine,
            collection_ids=[cid for cid, _ in self.plan.collections],
            track_ids=self.plan.track_ids,
            on_event=on_event,
            search_window_s=ctx.search_window_s,
        )
        if self._cancelled:
            # cancelled from another thread before the runner existed
            self._runner.cancel()
        return self._runner.run()

    def cancel(self) -> None:
        self._cancelled = True
        if self._runner is not None:
            self._runner.cancel()

    def close(self) -> None:
        self._lock.release()

    def __enter__(self) -> DownloadJob:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def recover_interrupted(ctx: AppContext) -> int:
    """at start: tracks a crash left searching or downloading go back to pending, stage and
    attempts stay (PLAN.md section 7). skipped while a download runs, its tracks are busy."""
    lock = RunLock(ctx.data_dir / "download.lock")
    try:
        lock.acquire()
    except AlreadyRunningError:
        return 0
    try:
        return ctx.repo.reset_in_progress()
    finally:
        lock.release()


def write_playlists(ctx: AppContext, collection_ids: Iterable[int]) -> list[Path]:
    library = library_dir(ctx.settings())
    return [playlists.write_playlist(ctx.repo, cid, library) for cid in collection_ids]


# report and retry


REASONS = {
    "NoSearchResults": N_("nothing found on Soulseek"),
    "NoMatchingResults": N_("found, but no file matched the format, length or name"),
    "AllDownloadsFailed": N_("every download attempt failed"),
    "OutOfDownloadRetries": N_("every download attempt failed"),
}


def reason_text(reason: str | None) -> str:
    if not reason:
        return ""
    return tr(REASONS[reason]) if reason in REASONS else reason


@dataclass
class ReportRow:
    track_id: int
    label: str
    album: str | None
    status: str
    stage: str
    attempts: int
    waiting_fallback: bool
    reason: str
    collections: list[tuple[int, str]]


def report(ctx: AppContext, collection_ids: Iterable[int] | None = None) -> list[ReportRow]:
    """not_found and failed tracks of all collections, or of the given ones."""
    wanted = set(_require_collections(ctx, collection_ids)) if collection_ids else None
    settings = ctx.settings()
    rows = []
    for row in ctx.repo.problem_tracks():
        collections = [(c["id"], row_name(c)) for c in ctx.repo.collections_of_track(row["id"])]
        if wanted is not None and not wanted & {cid for cid, _ in collections}:
            continue
        rows.append(
            ReportRow(
                track_id=row["id"],
                label=runner.track_label(row),
                album=row["album"],
                status=row["status"],
                stage=row["stage"],
                attempts=row["attempts"],
                waiting_fallback=policy.waiting_for_fallback(
                    row["status"], row["stage"], row["attempts"], settings
                ),
                reason=reason_text(row["last_error"]),
                collections=collections,
            )
        )
    return rows


def export_report(rows: list[ReportRow], path: Path) -> None:
    # utf-8 with bom, so excel shows accents right
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Track ID", "Track", "Album", "Status", "Stage", "Attempts", "Reason",
                         "Collections"])  # fmt: skip
        for r in rows:
            status = "waiting for WAV/AIFF" if r.waiting_fallback else r.status
            writer.writerow([r.track_id, r.label, r.album or "", status, r.stage, r.attempts,
                             r.reason, "; ".join(name for _, name in r.collections)])  # fmt: skip


def retry_tracks(
    ctx: AppContext, track_ids: Iterable[int] = (), collection_ids: Iterable[int] = ()
) -> list[int]:
    """the retry button of the report: stage flac, attempts 0, pending. returns changed ids."""
    ids = list(track_ids)
    collection_ids = list(collection_ids)
    if collection_ids:
        ids += [r.track_id for r in report(ctx, collection_ids)]
    return ctx.repo.retry(ids)


# soulseek account


class SoulseekLoginError(NeonSeekError):
    pass


class NoSoulseekLoginError(SoulseekLoginError):
    pass


@dataclass
class SoulseekLoginOutcome:
    login: secret_store.SoulseekLogin
    warnings: list[str] = field(default_factory=list)


def _clean_username(username: str) -> str:
    # spaces typed around the name are dropped, the server would reject them anyway
    username = username.strip()
    problem = secret_store.username_problem(username)
    if problem:
        raise SoulseekLoginError(problem)
    return username


def check_soulseek_login(ctx: AppContext, login: secret_store.SoulseekLogin) -> sockseek.LoginCheck:
    """logs in once with an empty list: no search, a bad password is sent only once."""
    work = ctx.data_dir / "tmp" / f"login-{_random.token_hex(4)}"
    return sockseek.check_login(login, work, command=ctx.sockseek_command)


def _login_problem(check: sockseek.LoginCheck, new_account: bool, username: str) -> str:
    if check.outcome == sockseek.LOGIN_REJECTED:
        if new_account:
            return tr(
                'The name "{name}" is already taken on Soulseek. Choose another name.'
            ).format(name=username)
        return tr(
            'Soulseek did not accept this password for "{name}". Check the password and try again.'
        ).format(name=username)
    if check.outcome == sockseek.LOGIN_BAD_NAME:
        return tr("Soulseek does not accept this username. Choose another name.")
    if check.outcome == sockseek.LOGIN_SERVER:
        return sockseek.login_failure_message(check.detail)
    if check.outcome == sockseek.LOGIN_KICKED:
        return sockseek.kicked_message()
    if check.outcome == sockseek.LOGIN_NO_ANSWER:
        return sockseek.start_timeout_message(sockseek.LOGIN_CHECK_TIMEOUT_S)
    return tr(
        "Could not reach Soulseek. Check your internet connection and that no security "
        "program blocks NeonSeek, then try again."
    )


def _save_after_check(
    ctx: AppContext, login: secret_store.SoulseekLogin, new_account: bool
) -> None:
    check = check_soulseek_login(ctx, login)
    if not check.ok:
        nothing = tr("Nothing was saved.")
        raise SoulseekLoginError(f"{_login_problem(check, new_account, login.username)} {nothing}")
    secret_store.set_soulseek_login(login)


def create_soulseek_account(ctx: AppContext, username: str) -> SoulseekLoginOutcome:
    """new account: the user picks the name, the app makes the password (D-053).

    the first login creates the account. a taken name is reported, never retried."""
    login = secret_store.SoulseekLogin(_clean_username(username), secret_store.generate_password())
    _save_after_check(ctx, login, new_account=True)
    return SoulseekLoginOutcome(login)


def use_own_soulseek_account(ctx: AppContext, username: str, password: str) -> SoulseekLoginOutcome:
    """an existing account, name and password typed by the user. the password is taken as typed."""
    username = _clean_username(username)
    if not password:
        raise SoulseekLoginError(tr("Enter a Soulseek password."))
    warnings = []
    if password.casefold() == username.casefold():
        warnings.append(same_as_name_warning())
    _save_after_check(ctx, secret_store.SoulseekLogin(username, password), new_account=False)
    return SoulseekLoginOutcome(secret_store.SoulseekLogin(username, password), warnings)


def same_as_name_warning() -> str:
    return tr(
        "Your password is the same as your username. Sockseek writes the username into its "
        "logs, so the password ends up there too. Change the password in a Soulseek program "
        "if you can."
    )


def soulseek_check_started_message() -> str:
    """the first line of every login check: what happens and how long it may take (D-250)."""
    return tr(
        "Connecting to Soulseek to check the login (no search, at most {seconds} seconds)..."
    ).format(seconds=int(sockseek.LOGIN_CHECK_TIMEOUT_S))


def verify_soulseek_login(ctx: AppContext) -> sockseek.LoginCheck:
    login = secret_store.get_soulseek_login()
    if login is None:
        raise NoSoulseekLoginError(no_soulseek_login_message())
    return check_soulseek_login(ctx, login)


def soulseek_login_problem(check: sockseek.LoginCheck, username: str) -> str:
    return _login_problem(check, new_account=False, username=username)


def no_soulseek_login_message() -> str:
    return tr("No Soulseek login is saved yet. Log in to Soulseek or create an account first.")


def soulseek_login(ctx: AppContext) -> secret_store.SoulseekLogin | None:
    return secret_store.get_soulseek_login()


def forget_soulseek_login(ctx: AppContext) -> None:
    secret_store.delete_soulseek_login()


# spotify account


def spotify_client_id(ctx: AppContext) -> str:
    return ctx.settings().spotify_client_id


def set_spotify_client_id(ctx: AppContext, client_id: str) -> None:
    settings = ctx.settings()
    new_id = client_id.strip()
    if new_id != settings.spotify_client_id:
        # a refresh token belongs to the old app, it will not work with the new one
        ctx.token_store.clear()
    settings.spotify_client_id = new_id
    ctx.save_settings(settings)
    ctx._spotify = None


def start_spotify_login(ctx: AppContext) -> spotify_auth.LoginFlow:
    """opens the local callback server. show flow.url in the browser, then finish the login."""
    flow = spotify_auth.LoginFlow(ctx.settings().spotify_client_id, ctx.http)
    flow.start()
    return flow


def finish_spotify_login(
    ctx: AppContext, flow: spotify_auth.LoginFlow, timeout: float = spotify_auth.LOGIN_TIMEOUT_S
) -> dict:
    tokens = flow.wait(timeout)
    auth = ctx.spotify_auth()
    auth.accept(tokens)
    ctx._spotify = SpotifyClient(auth, ctx.http)
    return ctx._spotify.get("/me")


def spotify_logout(ctx: AppContext) -> None:
    ctx.token_store.clear()
    ctx._spotify = None


def spotify_me(ctx: AppContext) -> dict:
    return ctx.spotify().get("/me")


def sync_spotify(
    ctx: AppContext,
    collection_id: int | None = None,
    progress: spotify_account.Progress | None = None,
    list_only: bool = False,
) -> spotify_account.SyncReport:
    """refreshes the account playlist list and the content of selected spotify collections.
    list_only reads just the list, like the window does at start (D-130)."""
    if list_only:
        return spotify_account.refresh_list(ctx.repo, ctx.spotify(), progress)
    if collection_id is not None:
        _require_collections(ctx, [collection_id])
    return spotify_account.sync_account(
        ctx.repo, ctx.spotify(), collection_id=collection_id, progress=progress
    )


def add_spotify_links(
    ctx: AppContext,
    texts: Iterable[str],
    progress: spotify_account.Progress | None = None,
) -> list[spotify_url.AddResult]:
    texts = [t for t in texts if t.strip()]
    client = ctx.spotify()
    return spotify_url.add_urls(ctx.repo, client, texts, progress or spotify_account.noop_progress)


def add_links(
    ctx: AppContext,
    texts: Iterable[str],
    progress: spotify_account.Progress | None = None,
) -> list[spotify_url.AddResult]:
    """links of spotify, apple music, youtube, soundcloud and tidal, each on its own. a spotify link
    without a spotify login fails alone, the other links are added (D-227)."""
    progress = progress or spotify_account.noop_progress
    texts = [t.strip() for t in texts if t.strip()]
    if texts and all(weblinks.service_of(t) == weblinks.SPOTIFY for t in texts):
        # only spotify links: no login is one error with its hint, as before
        ctx.spotify()
    results: list[spotify_url.AddResult] = []
    web: web_url.WebClients | None = None
    for text in texts:
        if not text:
            continue
        progress(tr("Adding {link}...").format(link=text))
        try:
            service = weblinks.service_of(text)
            if service is None:
                raise weblinks.UnsupportedWebLinkError(weblinks.not_a_link_message(text))
            if service == weblinks.SPOTIFY:
                results.append(spotify_url.add_url(ctx.repo, ctx.spotify(), text, progress))
                continue
            if web is None:
                web = ctx.web()
            results.append(web_url.add_web_url(ctx.repo, web, text, progress))
        except NeonSeekError as exc:
            results.append(
                spotify_url.AddResult(
                    text=text, outcome=spotify_account.FAILED, message=exc.message
                )
            )
        except Exception as exc:  # noqa: BLE001 - a source that changed must not stop the rest
            results.append(
                spotify_url.AddResult(
                    text=text,
                    outcome=spotify_account.FAILED,
                    message=web_url.unexpected_message(exc),
                )  # fmt: skip
            )
    return results


@dataclass
class SyncOutcome:
    """one press of sync: spotify (when logged in) and the apple music, youtube, soundcloud and
    tidal collections, which need no login."""

    spotify: spotify_account.SyncReport | None = None
    web: list[web_url.WebSync] = field(default_factory=list)
    spotify_skipped: bool = False  # selected spotify lists wait for a login


def _spotify_selected(ctx: AppContext) -> bool:
    return any(
        row["selected"]
        for kind in (KIND_SPOTIFY_LIKED, KIND_SPOTIFY_PLAYLIST, KIND_SPOTIFY_ALBUM)
        for row in ctx.repo.collections_of_kind(kind)
    )


def web_selected(ctx: AppContext) -> bool:
    return any(row["selected"] for kind in WEB_KINDS for row in ctx.repo.collections_of_kind(kind))


def has_web_collections(ctx: AppContext) -> bool:
    return any(ctx.repo.collections_of_kind(kind) for kind in WEB_KINDS)


def sync(
    ctx: AppContext,
    collection_id: int | None = None,
    progress: spotify_account.Progress | None = None,
) -> SyncOutcome:
    """reads selected collections again: spotify ones and apple music, youtube, soundcloud and
    tidal ones. with collection_id only that one. without a spotify login and without other selected
    collections it asks for the login, as before."""
    progress = progress or spotify_account.noop_progress
    outcome = SyncOutcome()
    if collection_id is not None:
        _require_collections(ctx, [collection_id])
        row = ctx.repo.get_collection(collection_id)
        if web_url.is_web_kind(row["kind"]):
            outcome.web = web_url.sync_web(
                ctx.repo, ctx.web, collection_id=collection_id, progress=progress
            )
        else:
            outcome.spotify = sync_spotify(ctx, collection_id, progress)
        return outcome
    if spotify_state(ctx).ready or not has_web_collections(ctx):
        outcome.spotify = sync_spotify(ctx, None, progress)
    else:
        outcome.spotify_skipped = _spotify_selected(ctx)
    outcome.web = web_url.sync_web(ctx.repo, ctx.web, progress=progress)
    return outcome


class TrackEditError(NeonSeekError):
    pass


def edit_track(ctx: AppContext, track_id: int, artist: str | None, title: str | None) -> TrackInfo:
    """the user's own artist and title for a track, before it is searched (D-229). a sync keeps
    them. a track that was not found gets searched again with the new names."""
    _require_tracks(ctx, [track_id])
    with _no_download(ctx):
        return _edit_track(ctx, track_id, artist, title)


def _edit_track(ctx: AppContext, track_id: int, artist: str | None, title: str | None) -> TrackInfo:
    row = ctx.repo.get_track(track_id)
    download = ctx.repo.get_download(track_id)
    if download is not None and download["status"] not in db.EXCLUDABLE:
        raise TrackEditError(
            tr(
                "Only tracks without a file can be corrected, and not while they are searched. "
                "Delete the file first if this track was downloaded wrong."
            )
        )
    new_title = " ".join((title if title is not None else row["title"]).split())
    if not new_title:
        raise TrackEditError(tr("The title cannot be empty."))
    old_artists = json.loads(row["artists_json"]) if row["artists_json"] else []
    if artist is None:
        artists = old_artists
    else:
        typed = " ".join(artist.split())
        # the same text as shown keeps the list as it was
        artists = old_artists if typed == ", ".join(old_artists) else ([typed] if typed else [])
    values = ingest.track_values(TrackRecord(title=new_title, artists=tuple(artists)))
    changed = new_title != row["title"] or artists != old_artists
    with ctx.repo.transaction():
        ctx.repo.update_track(
            track_id, {k: values[k] for k in ("title", "artists_json", "match_key")}
        )
        if changed and download is not None and download["status"] in (db.NOT_FOUND, db.FAILED):
            ctx.repo.retry_with_new_names(track_id)
    return track_info(ctx, track_id)


# window helpers, each one also has a cli command


@dataclass(frozen=True)
class SpotifyState:
    client_id: bool
    logged_in: bool

    @property
    def ready(self) -> bool:
        return self.client_id and self.logged_in


def spotify_state(ctx: AppContext) -> SpotifyState:
    """whether spotify can be used, without asking spotify."""
    client_id = bool(ctx.settings().spotify_client_id)
    return SpotifyState(client_id, client_id and ctx.token_store.load() is not None)


def list_at_start(ctx: AppContext) -> bool:
    """the window reads the list of spotify playlists at start (D-130): only logged in."""
    return ctx.settings().sync_on_start and spotify_state(ctx).ready


def spotify_needed_message(state: SpotifyState) -> str:
    if not state.client_id:
        return tr(
            "Spotify is not set up yet. Add your Spotify Client ID in Settings and log in. "
            "Without Spotify you can still import CSV files."
        )
    return tr("Log in to Spotify first. Without Spotify you can still import CSV files.")


def needs_setup(ctx: AppContext) -> bool:
    """the first run wizard shows until a library folder and a soulseek login are saved."""
    return not ctx.settings().library_dir or secret_store.get_soulseek_login() is None


@dataclass(frozen=True)
class Place:
    path: Path
    is_file: bool  # a file is shown selected in its folder


def collection_folder(ctx: AppContext, collection_id: int) -> Place:
    """where a collection lives: its playlist if written, else the folder of the tracks."""
    _require_collections(ctx, [collection_id])
    library = library_dir(ctx.settings())
    playlist = playlists.playlist_path(ctx.repo, collection_id, library)
    if playlist.is_file():
        return Place(playlist, True)
    tracks = library / "tracks"
    return Place(tracks if tracks.is_dir() else library, False)


SPOTIFY_WEB = "https://open.spotify.com"


@dataclass(frozen=True)
class Source:
    url: str | None = None  # opened in the browser
    path: str | None = None  # a csv file shown in its folder


def collection_source(ctx: AppContext, collection_id: int) -> Source:
    _require_collections(ctx, [collection_id])
    row = ctx.repo.get_collection(collection_id)
    kind, ref, ext = row["kind"], row["source_ref"], row["external_id"]
    if kind == KIND_CSV:
        return Source(path=ref)
    if kind == KIND_SPOTIFY_LIKED:
        return Source(url=f"{SPOTIFY_WEB}/collection/tracks")
    if kind == KIND_SPOTIFY_PLAYLIST and ext:
        return Source(url=ref or f"{SPOTIFY_WEB}/playlist/{ext}")
    if kind == KIND_SPOTIFY_ALBUM and ext:
        return Source(url=ref or f"{SPOTIFY_WEB}/album/{ext}")
    if kind == KIND_SINGLES:
        return Source()
    return Source(url=ref)


@dataclass(frozen=True)
class SockseekInfo:
    path: str
    version: str
    source: str


def sockseek_info(ctx: AppContext) -> SockseekInfo:
    """presence and version of the bundled sockseek, without logging in."""
    command = ctx.sockseek_command
    path = " ".join(command) if command else str(sockseek.find_binary())
    return SockseekInfo(path, sockseek.binary_version(command), sockseek.source_note())


# what ships with neonseek, for about and later the installer notices
_PACKAGES = (
    "PySide6-Essentials",
    "shiboken6",
    "httpx",
    "keyring",
    "mutagen",
    "platformdirs",
    "yt-dlp",
)


def app_license() -> str:
    """neonseek's own license, for about in the window and the cli."""
    return tr(
        "NeonSeek is free software under the GNU General Public License, version 3 or later "
        "(GPL-3.0-or-later), and comes without any warranty. The full text is in the LICENSE file."
    )


def source_code() -> str:
    """where the source code of exactly this version is (gpl-3.0, D-214)."""
    return tr("Source code of this version: {url}").format(
        url=project.source_url(neonseek.__version__)
    )


def project_links() -> list[tuple[str, str]]:
    """the project page and the donation page, for about in the window and the cli (D-265)."""
    return [
        (tr("Project page"), project.REPO_URL),
        (tr("Support NeonSeek"), project.DONATION_LINK),
    ]


def qt_license() -> str:
    """the lgpl notice for qt and pyside6, which ship as separate libraries (D-216)."""
    return tr(
        "Qt and PySide6 are used under the GNU Lesser General Public License, version 3 "
        "(LGPL-3.0). Their libraries are separate files in the program folder, so you can "
        "replace them with your own build."
    )


def license_texts() -> Path:
    """the full texts of all licenses: next to the installed program, or in the repository."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "THIRD_PARTY_LICENSES.txt"
    return Path(__file__).resolve().parents[2] / "THIRD_PARTY_LICENSES"


def third_party() -> list[tuple[str, str, str]]:
    """name, version and license of every part of the app."""
    found = [
        ("Sockseek", "3.0.5", "AGPL-3.0"),
        ("JetBrains Mono", "2.304", "OFL-1.1"),
    ]
    for name in _PACKAGES:
        try:
            meta = metadata.metadata(name)
        except metadata.PackageNotFoundError:
            continue
        license_name = meta.get("License-Expression") or meta.get("License") or ""
        if not license_name or len(license_name) > 60:
            classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License")]
            license_name = classifiers[0].split("::")[-1].strip() if classifiers else "see package"
        found.append((name, meta.get("Version") or "", license_name))
    return found


def diagnostics(ctx: AppContext, extra: dict[str, str] | None = None) -> str:
    """versions, system, setup and the end of the logs, for someone helping remotely.
    passwords, tokens, the soulseek name and the windows user folder are cut."""
    settings = ctx.settings()
    login = None
    with contextlib.suppress(NeonSeekError):
        login = secret_store.get_soulseek_login()
    lines = [
        f"NeonSeek {neonseek.__version__}",
        f"Python {sys.version.split()[0]}",
        f"System {platform.platform()}",
    ]
    lines += [f"{key} {value}" for key, value in (extra or {}).items()]
    try:
        lines.append(f"Sockseek {sockseek_info(ctx).version}")
    except NeonSeekError as exc:
        lines.append(f"Sockseek: {exc.message}")
    state = spotify_state(ctx)
    lines += [
        f"Data folder: {ctx.data_dir}",
        f"Library folder: {settings.library_dir or '(not set)'}",
        f"Soulseek login saved: {'yes' if login else 'no'}",
        f"Spotify client id set: {'yes' if state.client_id else 'no'}, "
        f"logged in: {'yes' if state.logged_in else 'no'}",
        f"on_missing_flac={settings.on_missing_flac} max_attempts={settings.max_attempts} "
        f"idle_timeout_minutes={settings.idle_timeout_minutes} "
        f"tags_from_source={settings.tags_from_source} theme={settings.theme}",
    ]
    try:
        counts = ctx.repo.status_counts()
        lines.append(f"Collections: {len(ctx.repo.list_collections())}, tracks: {counts}")
    except NeonSeekError as exc:
        lines.append(f"Database: {exc.message}")
    logs = config.logs_dir(ctx.data_dir)
    lines += ["", f"--- {applog.FILE_NAME} (end) ---", *applog.tail(logs / applog.FILE_NAME)]
    runs = sorted(logs.glob("run-*.log"), key=lambda p: p.stat().st_mtime) if logs.is_dir() else []
    if runs:
        lines += ["", f"--- {runs[-1].name} (end) ---", *applog.tail(runs[-1], 40)]
    hidden = applog.known_secrets() + ([login.username] if login else [])
    text = Redactor(hidden)("\n".join(lines))
    home = os.path.expanduser("~")
    if home and home != "~":
        text = text.replace(home, "%USERPROFILE%")
    return text


@dataclass(frozen=True)
class CsvDefaults:
    """what the import dialog starts with: a file imported before keeps its name and mapping."""

    name: str
    overrides: dict[str, str | None]
    length_unit: str | None
    collection_id: int | None


def csv_import_defaults(
    ctx: AppContext, path: Path, collection_id: int | None = None
) -> CsvDefaults:
    path = Path(path).resolve()
    if collection_id is not None:
        row = _csv_collection(ctx, collection_id)
    else:
        row = ctx.repo.find_collection_by_path(KIND_CSV, str(path))
    if row is None:
        return CsvDefaults(path.stem, {}, None, None)
    stored = _stored_map(row)
    return CsvDefaults(
        row["name"] or path.stem,
        dict(stored.get("overrides") or {}),
        stored.get("length_unit_override"),
        int(row["id"]),
    )
