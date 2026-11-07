"""what the cli prints and the gui paints: collection groups, track rows, states and labels.

both layers take these values as they are, neither computes its own (PLAN.md section 0).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from neonseek import actions, config
from neonseek.db import repo as db
from neonseek.i18n import ntr, tr
from neonseek.pipeline import policy
from neonseek.sources import spotify_common
from neonseek.sources.base import (
    KIND_APPLE_ALBUM,
    KIND_APPLE_PLAYLIST,
    KIND_CSV,
    KIND_SINGLES,
    KIND_SOUNDCLOUD_SET,
    KIND_SPOTIFY_ALBUM,
    KIND_SPOTIFY_LIKED,
    KIND_SPOTIFY_PLAYLIST,
    KIND_TIDAL_ALBUM,
    KIND_TIDAL_PLAYLIST,
    KIND_YOUTUBE_PLAYLIST,
    ORIGIN_ACCOUNT,
    WEB_KINDS,
)
from neonseek.textfold import fold

# plain labels


def length_label(ms: int | None) -> str:
    if ms is None:
        return "-"
    seconds = round(ms / 1000)
    hours, rest = divmod(seconds, 3600)
    if hours:
        return f"{hours}:{rest // 60:02d}:{rest % 60:02d}"
    return f"{rest // 60}:{rest % 60:02d}"


def duration_label(seconds: float) -> str:
    seconds = int(round(seconds))
    minutes, rest = divmod(seconds, 60)
    if minutes and rest:
        return tr("{m} min {s} s").format(m=minutes, s=rest)
    if minutes:
        return tr("{m} min").format(m=minutes)
    return tr("{s} s").format(s=rest)


def file_label(
    fmt: str | None,
    sample_rate: int | None,
    bit_depth: int | None,
    length_verified: bool | None = None,
) -> str:
    """format, sample rate in khz and bit depth, like FLAC 44.1/16."""
    if not fmt:
        return ""
    rate = f"{sample_rate / 1000:g}" if sample_rate else "?"
    text = f"{fmt.upper()} {rate}/{bit_depth or '?'}"
    if length_verified is False:
        text += " " + tr("(length not checked, the source has none)")
    return text


def local_time(iso: str | None) -> str:
    """a utc timestamp from the database as local date and time, minutes only."""
    if not iso:
        return ""
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


# collections and their groups in the left panel (PLAN.md section 9)

GROUP_SPOTIFY = "spotify"
GROUP_LINKS = "links"
GROUP_CSV = "csv"
GROUPS = (GROUP_SPOTIFY, GROUP_LINKS, GROUP_CSV)


def group_title(group: str) -> str:
    return {GROUP_SPOTIFY: tr("SPOTIFY"), GROUP_LINKS: tr("LINKS"), GROUP_CSV: tr("CSV")}[group]


def collection_group(summary: db.CollectionSummary) -> str:
    if summary.kind == KIND_CSV:
        return GROUP_CSV
    if summary.kind == KIND_SPOTIFY_LIKED:
        return GROUP_SPOTIFY
    if summary.kind == KIND_SPOTIFY_PLAYLIST and summary.origin == ORIGIN_ACCOUNT:
        return GROUP_SPOTIFY
    # playlists and albums from links, and the built in single tracks
    return GROUP_LINKS


def _group_order(summary: db.CollectionSummary) -> tuple:
    # liked songs first, single tracks last, the rest by name
    first = summary.kind == KIND_SPOTIFY_LIKED
    last = summary.kind == KIND_SINGLES
    return (not first, last, summary.name.casefold(), summary.id)


@dataclass(frozen=True)
class CollectionGroup:
    key: str
    title: str
    collections: list[db.CollectionSummary]


def collection_groups(
    summaries: Iterable[db.CollectionSummary], keep_empty: bool = True
) -> list[CollectionGroup]:
    """the three groups in panel order. empty groups stay, so the panel keeps its shape."""
    buckets: dict[str, list[db.CollectionSummary]] = {key: [] for key in GROUPS}
    for summary in summaries:
        buckets[collection_group(summary)].append(summary)
    return [
        CollectionGroup(key, group_title(key), sorted(buckets[key], key=_group_order))
        for key in GROUPS
        if keep_empty or buckets[key]
    ]


def empty_group_hint(group: str) -> str:
    return {
        GROUP_SPOTIFY: tr("Log in to Spotify to see your playlists and liked songs here."),
        GROUP_LINKS: tr(
            "Playlists, albums and tracks of Spotify, Apple Music, YouTube, SoundCloud and TIDAL "
            "you add with + ADD."
        ),
        GROUP_CSV: tr("CSV files you import with + ADD or drop on the window."),
    }[group]


def first_collection(summaries: Iterable[db.CollectionSummary]) -> int | None:
    """the collection open at start: a selected one with tracks, then any with tracks, then the
    first in the panel."""
    ordered = [c for group in collection_groups(summaries) for c in group.collections]
    for wanted in (lambda c: c.selected and c.linked, lambda c: c.linked, lambda c: True):
        found = next((c for c in ordered if wanted(c)), None)
        if found is not None:
            return found.id
    return None


def content_read(summary: db.CollectionSummary) -> bool:
    return summary.last_synced_at is not None


def wanted_count(summary: db.CollectionSummary) -> int:
    """tracks of a collection the user wants: excluded ones do not count (D-126)."""
    return summary.linked - summary.excluded


def collection_counter(summary: db.CollectionSummary) -> str:
    """downloaded/all. before the content is read, what the source reports."""
    if content_read(summary):
        return f"{summary.done}/{wanted_count(summary)}"
    if summary.track_count is not None:
        return f"-/{summary.track_count}"
    return "-"


def counter_hint(summary: db.CollectionSummary) -> str:
    """what the counter means, also the dash of a collection not read yet."""
    if content_read(summary):
        total = wanted_count(summary)
        text = ntr(
            total, "{done} of {total} track downloaded", "{done} of {total} tracks downloaded"
        ).format(done=summary.done, total=total)
        if summary.excluded:
            text += ", " + ntr(
                summary.excluded, "{count} more is excluded", "{count} more are excluded"
            ).format(count=summary.excluded)
        return text
    if summary.track_count is not None:
        return tr(
            "Tracks not read yet (- downloaded). Spotify reports {count} tracks. Press SYNC to "
            "read them."
        ).format(count=summary.track_count)
    return tr("Tracks not read yet. Press SYNC to read them.")


def collection_kind_label(kind: str, origin: str) -> str:
    if kind == KIND_SPOTIFY_LIKED:
        return tr("Spotify liked songs")
    if kind == KIND_SPOTIFY_PLAYLIST:
        if origin == ORIGIN_ACCOUNT:
            return tr("Spotify playlist")
        return tr("Spotify playlist from a link")
    if kind == KIND_SPOTIFY_ALBUM:
        return tr("Spotify album")
    if kind == KIND_SINGLES:
        return tr("Single tracks from links")
    if kind == KIND_CSV:
        return tr("CSV file")
    if kind == KIND_APPLE_ALBUM:
        return tr("Apple Music album")
    if kind == KIND_APPLE_PLAYLIST:
        return tr("Apple Music playlist")
    if kind == KIND_YOUTUBE_PLAYLIST:
        return tr("YouTube playlist")
    if kind == KIND_SOUNDCLOUD_SET:
        return tr("SoundCloud set")
    if kind == KIND_TIDAL_ALBUM:
        return tr("TIDAL album")
    if kind == KIND_TIDAL_PLAYLIST:
        return tr("TIDAL playlist")
    return kind


def is_spotify(kind: str) -> bool:
    return kind in (KIND_SPOTIFY_PLAYLIST, KIND_SPOTIFY_LIKED, KIND_SPOTIFY_ALBUM)


def is_web(kind: str) -> bool:
    """apple music, youtube, soundcloud and tidal: read from a link, synced without a login."""
    return kind in WEB_KINDS


def sync_label(kind: str) -> str | None:
    """the collection menu item that reads a collection again, none when there is nothing."""
    if is_spotify(kind):
        return tr("Sync with Spotify")
    if kind in (KIND_APPLE_ALBUM, KIND_APPLE_PLAYLIST):
        return tr("Read again from Apple Music")
    if kind == KIND_YOUTUBE_PLAYLIST:
        return tr("Read again from YouTube")
    if kind == KIND_SOUNDCLOUD_SET:
        return tr("Read again from SoundCloud")
    if kind in (KIND_TIDAL_ALBUM, KIND_TIDAL_PLAYLIST):
        return tr("Read again from TIDAL")
    return None


def locked_hint(summary: db.CollectionSummary) -> str | None:
    """why a collection cannot be read, none when it can."""
    if summary.is_readable:
        return None
    return spotify_common.locked_playlist_message()


def empty_hint(summary: db.CollectionSummary) -> str:
    """what to do when a collection shows no tracks."""
    if not summary.is_readable:
        return spotify_common.locked_playlist_message()
    if not content_read(summary) and is_spotify(summary.kind):
        return tr(
            "The tracks of this playlist are not read yet. Press SYNC to read them from Spotify."
        )
    return tr("This collection has no tracks.")


def synced_label(summary: db.CollectionSummary) -> str:
    if summary.last_synced_at is None:
        return tr("not read yet")
    return tr("read {time}").format(time=local_time(summary.last_synced_at))


# track states (PLAN.md sections 7 and 9)

WAITING_FALLBACK = "waiting_fallback"
STATES = (
    db.PENDING,
    db.SEARCHING,
    db.DOWNLOADING,
    db.DONE,
    db.NOT_FOUND,
    db.FAILED,
    WAITING_FALLBACK,
    db.EXCLUDED,
)


def track_state(status: str, stage: str, attempts: int, settings: config.Settings) -> str:
    """the download status, except that a track waiting for the wav/aiff pass has its own
    state, so it does not look like finally not found (the same condition as pass 2)."""
    if policy.waiting_for_fallback(status, stage, attempts, settings):
        return WAITING_FALLBACK
    return status


def state_label(state: str, attempts: int = 0, max_attempts: int = 0) -> str:
    """the word for a state. pending reads "not downloaded", the word pending looks like
    something runs right now (D-129). the database keeps pending."""
    if state == db.FAILED and max_attempts:
        return tr("failed {attempts}/{max}").format(attempts=attempts, max=max_attempts)
    return {
        db.PENDING: tr("not downloaded"),
        db.SEARCHING: tr("searching"),
        db.DOWNLOADING: tr("downloading"),
        db.DONE: tr("done"),
        db.NOT_FOUND: tr("not found"),
        db.FAILED: tr("failed"),
        WAITING_FALLBACK: tr("waiting for WAV/AIFF"),
        db.EXCLUDED: tr("excluded"),
    }.get(state, state)


def state_hint(
    state: str,
    stage: str,
    *,
    gui: bool = True,
    attempts: int = 0,
    max_attempts: int = 0,
    track_id: int | None = None,
    collection_id: int | None = None,
) -> str:
    """what a state means and what to do next, for every chip (D-129). the cli gets the
    command instead of the button."""
    track = str(track_id) if track_id is not None else "<TRACK ID>"
    if collection_id is not None:
        download_cmd = f"neonseek download --collection {collection_id}"
    else:
        download_cmd = f"neonseek download --track {track}"
    if gui:
        again = tr("Right-click it and choose Download this track only to search again from FLAC.")
    else:
        again = tr("Search again from FLAC with: neonseek retry {track} --download").format(
            track=track
        )
    if state == WAITING_FALLBACK:
        if gui:
            return tr("No FLAC was found. The next DOWNLOAD looks for WAV or AIFF.")
        return tr("No FLAC was found. The next download looks for WAV or AIFF: {cmd}").format(
            cmd=download_cmd
        )
    if state == db.PENDING:
        if stage == db.STAGE_FALLBACK:
            text = tr(
                "Not downloaded yet. No FLAC was found before, so the next download looks for "
                "WAV or AIFF."
            )
        else:
            text = tr("Not downloaded yet.")
        if gui:
            return text + " " + tr("Press DOWNLOAD to download it.")
        return text + " " + tr("Download it with: {cmd}").format(cmd=download_cmd)
    if state == db.SEARCHING:
        return tr("Sockseek is searching for it on Soulseek right now.")
    if state == db.DOWNLOADING:
        if gui:
            return tr("Downloading right now. CANCEL below stops the download.")
        return tr("Downloading right now.")
    if state == db.DONE:
        if gui:
            return tr("Downloaded. Right-click it and choose Show in folder to see the file.")
        return tr("Downloaded. Show the file with: neonseek track folder {track}").format(
            track=track
        )
    if state == db.NOT_FOUND:
        return tr("Not found on Soulseek in any lossless format.") + " " + again
    if state == db.FAILED:
        if max_attempts and attempts >= max_attempts:
            done = tr("The download failed {count} times, NeonSeek stopped trying.").format(
                count=attempts
            )
            return done + " " + again
        if gui:
            return tr("The download failed. The next DOWNLOAD tries again.")
        return tr("The download failed. The next download tries again: {cmd}").format(
            cmd=download_cmd
        )
    if state == db.EXCLUDED:
        if gui:
            return tr(
                "Excluded: DOWNLOAD skips this track. Right-click it and choose Include again "
                "to download it later."
            )
        return tr(
            "Excluded: downloads skip this track. Include it again with: neonseek track "
            "include {track}"
        ).format(track=track)
    return ""


# filters of the track table
FILTER_ALL = "all"
FILTER_TODO = "todo"
FILTER_DONE = "done"
FILTER_PROBLEMS = "problems"
FILTER_EXCLUDED = "excluded"
FILTERS = (FILTER_ALL, FILTER_TODO, FILTER_DONE, FILTER_PROBLEMS, FILTER_EXCLUDED)


def filter_label(key: str) -> str:
    return {
        FILTER_ALL: tr("All"),
        FILTER_TODO: tr("To download"),
        FILTER_DONE: tr("Downloaded"),
        FILTER_PROBLEMS: tr("Not found or failed"),
        FILTER_EXCLUDED: tr("Excluded"),
    }[key]


def state_matches(state: str, key: str) -> bool:
    if key == FILTER_TODO:
        return state in (db.PENDING, db.SEARCHING, db.DOWNLOADING, WAITING_FALLBACK)
    if key == FILTER_DONE:
        return state == db.DONE
    if key == FILTER_PROBLEMS:
        return state in (db.NOT_FOUND, db.FAILED, WAITING_FALLBACK)
    if key == FILTER_EXCLUDED:
        return state == db.EXCLUDED
    return True


# track rows


@dataclass(frozen=True)
class TrackRow:
    track_id: int
    number: int  # position in the collection, from 1
    title: str
    artists: str
    album: str
    duration_ms: int | None
    cover_url: str | None
    status: str  # downloads.status
    stage: str
    attempts: int
    state: str  # what the row shows, see track_state
    state_label: str
    reason: str  # last error in words
    file_label: str  # only for downloaded tracks
    file_path: str | None
    max_attempts: int = 0

    @property
    def hint(self) -> str:
        return state_hint(
            self.state, self.stage, attempts=self.attempts, max_attempts=self.max_attempts
        )

    @property
    def length_label(self) -> str:
        return length_label(self.duration_ms)

    @property
    def label(self) -> str:
        first = self.artists.split(", ")[0] if self.artists else ""
        return f"{first} - {self.title}" if first else self.title


def _artists(raw: str | None) -> str:
    names = json.loads(raw) if raw else []
    return ", ".join(names)


def track_row(row, number: int, settings: config.Settings) -> TrackRow:
    """one row of the table from a track_states row."""
    state = track_state(row["status"], row["stage"], row["attempts"], settings)
    done = row["status"] == db.DONE
    verified = None if row["length_verified"] is None else bool(row["length_verified"])
    return TrackRow(
        track_id=row["id"],
        number=number,
        title=row["title"],
        artists=_artists(row["artists_json"]),
        album=row["album"] or "",
        duration_ms=row["duration_ms"],
        cover_url=row["cover_url"],
        status=row["status"],
        stage=row["stage"],
        attempts=row["attempts"],
        state=state,
        state_label=state_label(state, row["attempts"], settings.max_attempts),
        reason="" if done else actions.reason_text(row["last_error"]),
        file_label=file_label(row["format"], row["sample_rate"], row["bit_depth"], verified)
        if done
        else "",
        file_path=row["file_path"] if done else None,
        max_attempts=settings.max_attempts,
    )


def collection_tracks(ctx: actions.AppContext, collection_id: int) -> list[TrackRow]:
    """tracks of a collection in source order, a track listed twice comes once."""
    settings = ctx.settings()
    ids = ctx.repo.collection_track_ids(collection_id)
    states = ctx.repo.track_states(ids)
    return [
        track_row(states[tid], number, settings)
        for number, tid in enumerate(ids, start=1)
        if tid in states
    ]


def library_tracks(ctx: actions.AppContext) -> list[TrackRow]:
    """every track neonseek knows, also from removed collections, in the order they came."""
    settings = ctx.settings()
    ids = ctx.repo.all_track_ids()
    states = ctx.repo.track_states(ids)
    return [
        track_row(states[tid], number, settings)
        for number, tid in enumerate(ids, start=1)
        if tid in states
    ]


def tracks_for(ctx: actions.AppContext, track_ids: Iterable[int], numbers: dict[int, int]):
    """fresh rows of some tracks, for live updates during a download."""
    settings = ctx.settings()
    states = ctx.repo.track_states(track_ids)
    return {tid: track_row(row, numbers.get(tid, 0), settings) for tid, row in states.items()}


# the right-click menu of tracks (D-125): core decides what each item does and why one is off

MENU_DOWNLOAD = "download"
MENU_PROPERTIES = "properties"
MENU_EDIT = "edit"
MENU_FOLDER = "folder"
MENU_EXCLUDE = "exclude"
MENU_INCLUDE = "include"
MENU_DELETE = "delete"
MENU_ITEMS = (MENU_DOWNLOAD, MENU_PROPERTIES, MENU_EDIT, MENU_FOLDER, MENU_EXCLUDE, MENU_INCLUDE,
              MENU_DELETE)  # fmt: skip


@dataclass(frozen=True)
class TrackChoice:
    """the selected tracks and which of them each menu item works on."""

    ids: list[int]
    download: list[int]  # not downloaded yet, in any state
    files: list[int]  # downloaded with a file: show in folder, delete file
    exclude: list[int]  # without a file and nothing running
    include: list[int]  # excluded
    downloading: bool  # a download runs: nothing may change the tracks now
    # one track without a file: its artist and title can be corrected (D-229)
    edit: list[int] = field(default_factory=list)

    def targets(self, item: str) -> list[int]:
        return {
            MENU_DOWNLOAD: self.download,
            MENU_PROPERTIES: self.ids,
            MENU_EDIT: self.edit,
            MENU_FOLDER: self.files,
            MENU_EXCLUDE: self.exclude,
            MENU_INCLUDE: self.include,
            MENU_DELETE: self.files,
        }[item]


def track_choice(rows: Iterable[TrackRow], downloading: bool) -> TrackChoice:
    rows = list(rows)
    return TrackChoice(
        ids=[r.track_id for r in rows],
        download=[r.track_id for r in rows if r.status != db.DONE],
        files=[r.track_id for r in rows if r.status == db.DONE and r.file_path],
        exclude=[r.track_id for r in rows if r.status in db.EXCLUDABLE],
        include=[r.track_id for r in rows if r.status == db.EXCLUDED],
        downloading=downloading,
        edit=[r.track_id for r in rows if r.status in db.EXCLUDABLE] if len(rows) == 1 else [],
    )


def menu_label(item: str, choice: TrackChoice) -> str:
    count = len(choice.targets(item))
    many = count > 1
    if item == MENU_DOWNLOAD:
        if many:
            return tr("Download these {count} tracks only").format(count=count)
        return tr("Download this track only")
    if item == MENU_PROPERTIES:
        return tr("Properties")
    if item == MENU_EDIT:
        return tr("Edit artist and title...")
    if item == MENU_FOLDER:
        return tr("Show in folder")
    if item == MENU_EXCLUDE:
        if many:
            return tr("Exclude {count} tracks from downloads").format(count=count)
        return tr("Exclude from downloads")
    if item == MENU_INCLUDE:
        return (
            tr("Include {count} tracks again").format(count=count) if many else tr("Include again")
        )
    if many:
        return tr("Delete {count} files...").format(count=count)
    return tr("Delete file...")


def running_reason() -> str:
    return tr("A download or a library move is running. This works again when it ends.")


def menu_reason(item: str, choice: TrackChoice) -> str | None:
    """why a menu item is off, none when it works."""
    if item == MENU_PROPERTIES:
        return None if choice.ids else tr("Choose a track first.")
    if item == MENU_FOLDER:
        return None if choice.files else tr("Only downloaded tracks have a file.")
    if choice.downloading:
        return running_reason()
    if choice.targets(item):
        return None
    if item == MENU_DOWNLOAD:
        return tr("Already downloaded.")
    if item == MENU_EXCLUDE:
        if choice.files and len(choice.files) == len(choice.ids):
            return tr("A downloaded track is not excluded, use Delete file instead.")
        if choice.include and len(choice.include) == len(choice.ids):
            return tr("Already excluded.")
        return tr("Only tracks without a file can be excluded.")
    if item == MENU_INCLUDE:
        return tr("Only excluded tracks can be included again.")
    if item == MENU_EDIT:
        if len(choice.ids) != 1:
            return tr("Choose one track to correct.")
        if choice.files:
            return tr("A downloaded track keeps its names. Delete the file first to search again.")
        return tr("Only tracks without a file can be corrected.")
    return tr("Only downloaded tracks have a file to delete.")


# the properties window of a track (D-125), also neonseek track info


def _value(value) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def spotify_track_url(spotify_id: str | None) -> str | None:
    return f"https://open.spotify.com/track/{spotify_id}" if spotify_id else None


def stage_label(stage: str) -> str:
    return tr("WAV/AIFF pass") if stage == db.STAGE_FALLBACK else tr("FLAC pass")


def track_info_sections(
    row, collections: list[tuple[int, str]], file_exists: bool | None,
    settings: config.Settings, gui: bool = True,
) -> list[tuple[str, list[tuple[str, str]]]]:  # fmt: skip
    """every fact about a track, read only: (section, [(label, value)])."""
    state = track_state(row["status"], row["stage"], row["attempts"], settings)
    yes, no = tr("yes"), tr("no")
    explicit = None if row["explicit"] is None else (yes if row["explicit"] else no)
    source = [
        (tr("Title"), _value(row["title"])),
        (tr("Artists"), _value(_artists(row["artists_json"]))),
        (tr("Album"), _value(row["album"])),
        (tr("Album artists"), _value(_artists(row["album_artists_json"]))),
        (tr("Release date"), _value(row["release_date"])),
        (tr("Track number"), _value(row["track_number"])),
        (tr("Disc number"), _value(row["disc_number"])),
        (tr("Length"), length_label(row["duration_ms"])),
        (tr("Explicit"), _value(explicit)),
        (tr("ISRC"), _value(row["isrc"])),
        (tr("Spotify"), _value(spotify_track_url(row["spotify_id"]))),
        (tr("Cover"), _value(row["cover_url"])),
        (tr("NeonSeek ID"), str(row["id"])),
    ]
    in_collections = [(tr("Collection"), f"[{cid}] {name}") for cid, name in collections]
    if not in_collections:
        in_collections = [(tr("Collection"), tr("(none, its collections were removed)"))]
    reason = actions.reason_text(row["last_error"])
    if row["last_error"] and reason != row["last_error"]:
        reason = f"{reason} ({row['last_error']})"
    download = [
        (tr("Status"), state_label(state, row["attempts"], settings.max_attempts)),
        (
            tr("What next"),
            state_hint(
                state,
                row["stage"],
                gui=gui,
                attempts=row["attempts"],
                max_attempts=settings.max_attempts,
                track_id=row["id"],
            ),
        ),  # fmt: skip
    ]
    if row["status"] == db.EXCLUDED:
        download.append((tr("Before it was excluded"),
                         state_label(row["excluded_from"] or db.PENDING)))  # fmt: skip
    download += [
        (tr("Stage"), stage_label(row["stage"])),
        (
            tr("Attempts"),
            tr("{attempts} of {max} in this stage").format(
                attempts=row["attempts"], max=settings.max_attempts
            ),
        ),  # fmt: skip
        (tr("Last error"), _value(reason)),
        (tr("Last change"), _value(local_time(row["updated_at"]))),
    ]
    done = row["status"] == db.DONE
    verified = row["length_verified"]
    missing = tr("no, the file is missing")
    on_disk = "-" if file_exists is None else (yes if file_exists else missing)
    file = [
        (tr("Path"), _value(row["file_path"] if done else None)),
        (tr("File on disk"), on_disk if done else "-"),
        (
            tr("Format"),
            _value(
                file_label(row["format"], row["sample_rate"], row["bit_depth"]) if done else None
            ),
        ),  # fmt: skip
        (tr("File length"), length_label(row["file_duration_ms"]) if done else "-"),
        (
            tr("Length checked"),
            "-"
            if verified is None or not done
            else (yes if verified else tr("no, the source has no length")),
        ),  # fmt: skip
        (tr("Soulseek user"), _value(row["source_user"])),
        (tr("Soulseek path"), _value(row["source_path"])),
    ]
    return [
        (tr("FROM THE SOURCE"), source),
        (tr("COLLECTIONS"), in_collections),
        (tr("DOWNLOAD"), download),
        (tr("FILE"), file),
    ]


def all_state_labels() -> list[str]:
    """every label a status chip can show, for the width of the status column. the longest
    counts, whatever the language, so the column is measured, never fixed."""
    top = config.MAX_ATTEMPTS_LIMIT
    labels = [state_label(state) for state in STATES]
    labels.append(state_label(db.FAILED, top, top))
    labels.append(tr("downloading {percent}%").format(percent=100))
    return labels


def live_label(row: TrackRow, percent: int | None) -> str:
    """the state of a row while sockseek downloads it, with the percent when known."""
    if row.status == db.DOWNLOADING and percent is not None:
        return tr("downloading {percent}%").format(percent=percent)
    return row.state_label


def report_state(row: actions.ReportRow, settings: config.Settings) -> tuple[str, str]:
    """state and label of a report row, the same marks as in the track table."""
    state = WAITING_FALLBACK if row.waiting_fallback else row.status
    return state, state_label(state, row.attempts, settings.max_attempts)


def state_counts(rows: Iterable[TrackRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.state] = counts.get(row.state, 0) + 1
    return counts


# states in the order of the table, for counts by state
COUNT_ORDER = (db.DONE, WAITING_FALLBACK, db.NOT_FOUND, db.FAILED, db.DOWNLOADING,
               db.SEARCHING, db.PENDING, db.EXCLUDED)  # fmt: skip


def state_count_text(state: str, count: int) -> str:
    """a number of tracks in one state, like 5 done. a whole phrase per state, because polish
    puts the word in another form for 1, 2 and 5 (1 pobrany, 2 pobrane, 5 pobranych, D-163)."""
    texts = {
        db.DONE: ntr(count, "{count} done", "{count} done"),
        WAITING_FALLBACK: ntr(
            count, "{count} waiting for WAV/AIFF", "{count} waiting for WAV/AIFF"
        ),
        db.NOT_FOUND: ntr(count, "{count} not found", "{count} not found"),
        db.FAILED: ntr(count, "{count} failed", "{count} failed"),
        db.DOWNLOADING: ntr(count, "{count} downloading", "{count} downloading"),
        db.SEARCHING: ntr(count, "{count} searching", "{count} searching"),
        db.PENDING: ntr(count, "{count} not downloaded", "{count} not downloaded"),
        db.EXCLUDED: ntr(count, "{count} excluded", "{count} excluded"),
    }
    return texts.get(state, f"{{count}} {state}").format(count=count)


def stats_text(rows: list[TrackRow]) -> str:
    """tracks of a collection by state, in table order of states."""
    counts = state_counts(rows)
    parts = [ntr(len(rows), "{count} track", "{count} tracks").format(count=len(rows))]
    parts += [state_count_text(state, counts[state]) for state in COUNT_ORDER if counts.get(state)]
    return ", ".join(parts)


def track_matches(row: TrackRow, query: str) -> bool:
    """every word of the query is in the title, the artists or the album."""
    words = fold(query).split()
    if not words:
        return True
    haystack = fold(f"{row.title}\n{row.artists}\n{row.album}")
    return all(word in haystack for word in words)


# runs


def counts_text(counts: dict[str, int]) -> str:
    """the states of a finished run, the same words as the collection header."""
    order = [state for state in COUNT_ORDER if counts.get(state)]
    order += sorted(state for state in counts if state not in COUNT_ORDER and counts[state])
    return ", ".join(state_count_text(state, counts[state]) for state in order)


def last_run_text(ctx: actions.AppContext) -> str | None:
    """one line about the last finished download, none before the first one."""
    row = ctx.repo.last_finished_run()
    if row is None:
        return None
    stats = json.loads(row["stats_json"]) if row["stats_json"] else {}
    ended = {
        "completed": tr("completed"),
        "cancelled": tr("cancelled"),
        "error": tr("ended with an error"),
    }.get(stats.get("ended", ""), stats.get("ended", ""))
    text = tr("Last download {time}, {ended}. {counts}").format(
        time=local_time(row["finished_at"]),
        ended=ended,
        counts=counts_text(stats.get("counts", {})),
    )
    return text.strip()


# setting choices in words


def choice_label(key: str, value: str) -> str:
    labels = {
        ("on_missing_flac", config.LOSSLESS): tr("Take WAV or AIFF (AIFF first)"),
        ("on_missing_flac", config.NOT_FOUND): tr("Mark the track as not found"),
        ("tags_from_source", config.TAGS_OVERWRITE): tr("Replace with the source data"),
        ("tags_from_source", config.TAGS_FILL): tr("Only fill empty fields"),
        ("tags_from_source", config.TAGS_OFF): tr("Do not write source fields"),
    }
    return labels.get((key, value), value)


def setting_hint(key: str) -> str:
    return {
        "on_missing_flac": tr(
            "FLAC always comes first. This decides what happens when a track has no FLAC on "
            "Soulseek. MP3 and other lossy formats are never downloaded."
        ),
        "max_attempts": tr(
            "How often a failed download is tried again in later downloads before NeonSeek "
            "moves on to WAV/AIFF or gives up."
        ),
        "idle_timeout_minutes": tr(
            "Sockseek is stopped when it prints nothing for this long. Waiting for the search "
            "limit never counts."
        ),
        "ui_scale": tr(
            "Text and everything around it in the window. Also Ctrl and plus, Ctrl and minus, "
            "Ctrl+0 for 100%."
        ),
        "tags_from_source": tr(
            "Title, artists, album, date, track number and ISRC from Spotify or the CSV file. "
            "Genre and comment of a collection are always written when they are set."
        ),
        "sync_on_start": tr(
            "Only the list of playlists, in the background, so new playlists show up unticked. "
            "Their tracks are read with SYNC. Without internet nothing happens."
        ),
    }.get(key, "")
