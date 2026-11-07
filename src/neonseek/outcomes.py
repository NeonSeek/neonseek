"""lines that describe what an action did. the cli prints them, the gui shows them in its log
and dialogs, so both say exactly the same."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from neonseek import actions, config, views
from neonseek.engine import base as engine_base
from neonseek.i18n import ntr, tr
from neonseek.pipeline import runner, search_budget
from neonseek.sources import csv_file, spotify_account, spotify_common, spotify_url, web_url
from neonseek.sources import links as weblinks

# kinds of lines: the gui colors them, the cli sends err lines to stderr
INFO = "info"
DONE = "done"
WARN = "warn"
ERROR = "error"


@dataclass(frozen=True)
class Line:
    text: str
    kind: str = INFO
    err: bool = False  # the cli prints it to stderr


def texts(lines: Iterable[Line]) -> str:
    return "\n".join(line.text for line in lines)


# csv import


def mapping_lines(parsed: csv_file.CsvParse) -> list[Line]:
    columns = parsed.column_names()
    parts = [f"{name}={columns[name]!r}" for name in csv_file.FIELDS if name in columns]
    lines = [Line(tr("Columns: {columns}").format(columns=", ".join(parts)))]
    if parsed.length_unit:
        lines.append(Line(tr("Length unit: {unit}").format(unit=parsed.length_unit)))
    else:
        lines.append(Line(tr("No length column, tracks will be matched without length."), WARN))
    return lines


def count_lines(parsed: csv_file.CsvParse) -> list[Line]:
    lines = []
    if parsed.skipped_rows:
        lines.append(
            Line(tr("Rows skipped (no title): {count}").format(count=parsed.skipped_rows), WARN)
        )
    if parsed.bad_lengths:
        lines.append(
            Line(
                tr("Lengths that could not be read (imported without length): {count}").format(
                    count=parsed.bad_lengths
                ),
                WARN,
            )
        )
    return lines


def import_lines(outcome: actions.CsvImportOutcome) -> list[Line]:
    if outcome.unchanged:
        lines = [
            Line(
                tr('Collection {id} "{name}" is up to date, nothing changed.').format(
                    id=outcome.collection_id, name=outcome.name
                )
            )
        ]
        if outcome.renamed:
            lines.append(Line(tr('Renamed to "{name}".').format(name=outcome.name)))
        return lines
    result = outcome.result
    assert result is not None
    verb = tr("Imported") if outcome.created else tr("Updated")
    lines = [
        Line(
            tr(
                '{verb} "{name}" as collection {id}: {total} tracks '
                "({new} new, {existing} already in the library)."
            ).format(
                verb=verb,
                name=outcome.name,
                id=outcome.collection_id,
                total=result.tracks_total,
                new=result.tracks_new,
                existing=result.tracks_existing,
            ),
            DONE,
        )
    ]
    if result.links_removed:
        lines.append(
            Line(
                tr(
                    "{count} tracks are no longer in this list. Their files and download state "
                    "stay."
                ).format(count=result.links_removed)
            )
        )
    return lines + mapping_lines(outcome.parse) + count_lines(outcome.parse)


# spotify


def _ingest_text(result) -> str:
    return tr("{total} tracks ({new} new, {existing} already in the library)").format(
        total=result.tracks_total, new=result.tracks_new, existing=result.tracks_existing
    )


@dataclass
class SyncLines:
    lines: list[Line]
    failed: bool


def sync_lines(report: spotify_account.SyncReport) -> SyncLines:
    lines = [
        Line(
            tr("Spotify playlists on your account: {count} ({new} new).").format(
                count=report.playlists_listed, new=report.playlists_new
            )
        )
    ]
    if report.locked:
        lines.append(
            Line(
                tr("Spotify does not share the tracks of {count} of them: {names}").format(
                    count=len(report.locked), names=", ".join(report.locked)
                ),
                WARN,
            )
        )
    failed = False
    for sync in report.collections:
        label = f"[{sync.collection_id}] {sync.name}"
        if sync.outcome == spotify_common.UPDATED and sync.result is not None:
            text = tr("{label}: updated, {details}.").format(
                label=label, details=_ingest_text(sync.result)
            )
            if sync.result.links_removed:
                text += " " + tr("{count} removed from the list, files stay.").format(
                    count=sync.result.links_removed
                )
            if sync.skipped_entries:
                text += " " + tr("{count} podcasts or local files skipped.").format(
                    count=sync.skipped_entries
                )
            lines.append(Line(text, DONE))
        elif sync.outcome == spotify_common.UNCHANGED:
            lines.append(Line(tr("{label}: no changes on Spotify.").format(label=label)))
        else:
            bad = sync.outcome == spotify_common.FAILED
            failed = failed or bad
            lines.append(
                Line(
                    tr("{label}: {message}").format(label=label, message=sync.message),
                    ERROR if bad else WARN,
                )
            )
    return SyncLines(lines, failed)


def no_selected_spotify_line() -> Line:
    return Line(tr("No Spotify collections are selected. Select some with: neonseek select <ID>"))


def edited_line(info) -> Line:
    """after neonseek track edit and the edit dialog."""
    return Line(tr("Track {id} is now: {label}").format(
        id=info.row["id"], label=runner.track_label(info.row)), DONE)  # fmt: skip


def no_selected_line() -> Line:
    return Line(tr("No collections are selected. Select some with: neonseek select <ID>"))


def add_lines(results: list[spotify_url.AddResult]) -> list[Line]:
    lines = []
    for result in results:
        if result.outcome == spotify_url.ADDED:
            lines.append(
                Line(
                    tr("Added: {name} (collection {id}, {tracks} tracks)").format(
                        name=result.name, id=result.collection_id, tracks=result.tracks
                    ),
                    DONE,
                )
            )
        elif result.outcome == spotify_url.EXISTS:
            lines.append(
                Line(
                    tr("Already there: {name} (collection {id})").format(
                        name=result.name, id=result.collection_id
                    )
                )
            )
        else:
            lines.append(Line(tr("Not added: {link}").format(link=result.text), ERROR))
            lines.append(Line("  " + (result.message or ""), ERROR))
        for note in result.notes:
            lines.append(Line("  " + note, WARN))
    return lines


def web_sync_lines(results: list[web_url.WebSync]) -> SyncLines:
    """apple music, youtube, soundcloud and tidal collections read again."""
    lines: list[Line] = []
    failed = False
    for sync in results:
        label = f"[{sync.collection_id}] {sync.name}"
        service = weblinks.service_name(web_url.kind_service(sync.kind))
        if sync.outcome == web_url.UPDATED and sync.result is not None:
            text = tr("{label}: updated, {details}.").format(
                label=label, details=_ingest_text(sync.result)
            )
            if sync.result.links_removed:
                text += " " + tr("{count} removed from the list, files stay.").format(
                    count=sync.result.links_removed
                )
            lines.append(Line(text, DONE))
        elif sync.outcome == web_url.UNCHANGED:
            lines.append(
                Line(tr("{label}: no changes on {service}.").format(label=label, service=service))
            )
        else:
            failed = True
            lines.append(
                Line(tr("{label}: {message}").format(label=label, message=sync.message), ERROR)
            )
        for note in sync.notes:
            lines.append(Line("  " + note, WARN))
    return SyncLines(lines, failed)


def all_sync_lines(outcome: actions.SyncOutcome) -> SyncLines:
    """one sync: spotify first, then the other services."""
    lines: list[Line] = []
    failed = False
    if outcome.spotify is not None:
        spotify = sync_lines(outcome.spotify)
        lines += spotify.lines
        failed = spotify.failed
    if outcome.spotify_skipped:
        lines.append(
            Line(
                tr(
                    "Selected Spotify collections were not read: Spotify is not logged in. Log "
                    "in to Spotify to sync them too."
                ),
                WARN,
            )
        )
    web = web_sync_lines(outcome.web)
    return SyncLines(lines + web.lines, failed or web.failed)


def nothing_synced(outcome: actions.SyncOutcome) -> bool:
    spotify = outcome.spotify.collections if outcome.spotify is not None else []
    return not spotify and not outcome.web


# tags


def tags_saved_line(genre: str | None, comment: str | None, label: str) -> Line:
    return Line(
        tr("{label}: genre: {genre} | comment: {comment}").format(
            label=label, genre=genre or tr("(none)"), comment=comment or tr("(none)")
        )
    )


def retag_lines(outcome: actions.RetagOutcome) -> list[Line]:
    lines = [
        Line(
            tr("Tags written to {written} files, {same} already up to date.").format(
                written=outcome.written, same=outcome.unchanged
            ),
            DONE,
        )
    ]
    for label in outcome.missing:
        lines.append(Line(tr("File missing: {label}").format(label=label), WARN, err=True))
    for label, error in outcome.failed:
        lines.append(
            Line(
                tr("Could not write tags: {label}: {error}").format(label=label, error=error),
                ERROR,
                err=True,
            )
        )
    lines.append(Line(actions.reload_tags_hint()))
    return lines


# download


CHOSEN_SHOWN = 20


def plan_lines(plan: actions.DownloadPlan, gui: bool = False) -> list[Line]:
    """the summary shown before every start (PLAN.md section 8)."""
    if plan.chosen:
        lines = [Line(ntr(len(plan.chosen), "Track in this download:",
                          "Tracks in this download:"))]  # fmt: skip
        lines += [Line(f"  [{tid}] {label}") for tid, label in plan.chosen[:CHOSEN_SHOWN]]
        if len(plan.chosen) > CHOSEN_SHOWN:
            lines.append(
                Line(tr("  ... and {count} more").format(count=len(plan.chosen) - CHOSEN_SHOWN))
            )
        if plan.done:
            lines.append(
                Line(ntr(plan.done, "Already downloaded, skipped: {count} track.",
                         "Already downloaded, skipped: {count} tracks.").format(count=plan.done))
            )  # fmt: skip
        if plan.collections:
            names = ", ".join(f"[{cid}] {name}" for cid, name in plan.collections)
            lines.append(Line(tr("Playlists written again afterwards: {names}").format(
                names=names)))  # fmt: skip
    else:
        lines = [Line(tr("Collections in this download:"))]
        lines += [Line(f"  [{cid}] {name}") for cid, name in plan.collections]
        for cid, name in plan.empty:
            if gui:
                text = tr("  [{id}] {name} has no tracks yet. For Spotify press SYNC first.")
            else:
                text = tr(
                    "  [{id}] {name} has no tracks yet. For Spotify run: neonseek sync "
                    "--collection {id}"
                )
            lines.append(Line(text.format(id=cid, name=name), WARN))
        lines.append(
            Line(
                tr("Tracks in these collections: {total}, already downloaded: {done}.").format(
                    total=plan.total, done=plan.done
                )
            )
        )
    if plan.nothing_to_do:
        return lines
    searching = ntr(
        plan.first_pass,
        "FLAC pass: {count} track to search. Searching alone takes up to {time} "
        "(Soulseek allows {limit} searches per {window} s).",
        "FLAC pass: {count} tracks to search. Searching alone takes up to {time} "
        "(Soulseek allows {limit} searches per {window} s).",
    ).format(
        count=plan.first_pass,
        time=views.duration_label(plan.search_estimate_s),
        limit=search_budget.SEARCHES_PER_TIME,
        window=int(search_budget.RENEW_S),
    )
    if plan.fallback_pass:
        # only lossless has a wav/aiff pass at all (D-133)
        not_included = tr(
            "Download time and the WAV/AIFF pass for tracks without FLAC are not included, "
            "they cannot be known in advance."
        )
    else:
        not_included = tr("Download time is not included, it cannot be known in advance.")
    lines.append(Line(f"{searching} {not_included}"))
    if plan.fallback:
        lines.append(
            Line(
                ntr(
                    plan.fallback,
                    "Waiting for the WAV/AIFF pass already: {count} track.",
                    "Waiting for the WAV/AIFF pass already: {count} tracks.",
                ).format(count=plan.fallback)
            )
        )
    return lines


PASS_NAMES = {1: "FLAC", 2: "WAV/AIFF"}


@dataclass
class RunMonitor:
    """follows the events of one download: log lines, progress and the search limit wait."""

    pass_number: int = 0
    pass_total: int = 0
    pass_done: int = 0
    tracks_done: int = 0
    waiting: bool = False
    percent: dict[int, int] = field(default_factory=dict)
    # tracks whose state changed with the last event, the table reads them again
    changed: set[int] = field(default_factory=set)

    def feed(self, event) -> list[Line]:
        self.changed = set()
        if isinstance(event, runner.PassStarted):
            self.pass_number = event.number
            self.pass_total = len(event.track_ids)
            self.pass_done = 0
            self.waiting = False
            return [
                Line(
                    ntr(
                        len(event.track_ids),
                        "Pass {number} ({kind}): {count} track",
                        "Pass {number} ({kind}): {count} tracks",
                    ).format(
                        number=event.number,
                        kind=PASS_NAMES.get(event.number, ""),
                        count=len(event.track_ids),
                    )
                )
            ]
        if isinstance(event, runner.SearchLimitWait):
            self.waiting = True
            return [
                Line(
                    tr("Waiting {time} so the Soulseek search limit is not exceeded...").format(
                        time=views.duration_label(event.seconds)
                    ),
                    WARN,
                )
            ]
        if isinstance(event, engine_base.RateLimitWait):
            if self.waiting:
                return []
            self.waiting = True
            return [Line(tr("  Waiting for the Soulseek search limit..."), WARN)]
        if isinstance(event, engine_base.ItemSearching):
            self.waiting = False
            self.changed = {event.track_id}
            return []
        if isinstance(event, engine_base.ItemDownloading):
            self.percent[event.track_id] = event.percent or 0
            self.changed = {event.track_id}
            return []
        if isinstance(event, runner.TrackFinished):
            self.percent.pop(event.track_id, None)
            self.pass_done += 1
            self.tracks_done += 1
            self.changed = {event.track_id}
            return [self._finished(event)]
        if isinstance(event, runner.StepWarning):
            return [
                Line(
                    tr("  warning    {label}: {step}: {message}").format(
                        label=event.label, step=event.step, message=event.message
                    ),
                    WARN,
                )
            ]
        return []

    def _finished(self, event: runner.TrackFinished) -> Line:
        if event.status == "done":
            text = tr("  done       {label}  {file}").format(
                label=event.label, file=file_text(event.track_file)
            )
            return Line(text, DONE)
        if event.status == "not_found":
            return Line(
                tr("  not found  {label} ({reason})").format(
                    label=event.label, reason=actions.reason_text(event.detail)
                ),
                WARN,
            )
        return Line(
            tr("  failed     {label} ({reason})").format(
                label=event.label, reason=actions.reason_text(event.detail)
            ),
            ERROR,
        )

    def progress_text(self) -> str:
        if not self.pass_number:
            return tr("Starting...")
        return tr("Pass {number} ({kind}): {done} / {total}").format(
            number=self.pass_number,
            kind=PASS_NAMES.get(self.pass_number, ""),
            done=self.pass_done,
            total=self.pass_total,
        )


def file_text(track_file) -> str:
    if track_file is None:
        return ""
    return views.file_label(
        track_file.format, track_file.sample_rate, track_file.bit_depth, track_file.length_verified
    )


def run_end_lines(ctx: actions.AppContext, outcome: runner.RunOutcome) -> list[Line]:
    counts = views.counts_text(outcome.counts)
    lines = [Line(tr("Tracks of this run: {counts}").format(counts=counts or "0"))]
    lines += [Line(tr("Playlist: {path}").format(path=path)) for path in outcome.playlists]
    for info in outcome.passes:
        after = info.get("silence_after") or ""
        text = tr("Pass {number}: longest silence of Sockseek {seconds} s (after: {after})")
        lines.append(
            Line(
                text.format(
                    number=info["pass"], seconds=info["longest_silence_s"], after=after[:80]
                )
            )
        )
    log = config.logs_dir(ctx.data_dir) / f"run-{outcome.run_id}.log"
    lines.append(Line(tr("Log: {path}").format(path=log)))
    if outcome.ended == runner.CANCELLED:
        lines.append(
            Line(
                tr("Cancelled. Finished tracks are kept, start the download again to continue."),
                WARN,
                err=True,
            )
        )
    elif outcome.ended == runner.ERROR:
        lines.append(Line(tr("Error: {message}").format(message=outcome.message), ERROR, err=True))
    return lines


def nothing_to_download_lines(playlists: list[Path]) -> list[Line]:
    lines = [Line(tr("Playlist: {path}").format(path=path)) for path in playlists]
    return lines + [Line(tr("Nothing to download."))]


# soulseek account


def soulseek_saved_lines(outcome: actions.SoulseekLoginOutcome, new_account: bool) -> list[Line]:
    if new_account:
        text = tr(
            'Created the Soulseek account "{user}" and saved it. The password is kept in '
            "Windows Credential Manager."
        )
    else:
        text = tr('Logged in to Soulseek as "{user}" and saved the login.')
    lines = [Line(text.format(user=outcome.login.username), DONE)]
    return lines + [Line(tr("Warning: {text}").format(text=w), WARN) for w in outcome.warnings]


def soulseek_check_line(check, username: str) -> Line:
    if check.ok:
        return Line(tr('Soulseek login works for "{user}".').format(user=username), DONE)
    return Line(
        tr("Error: {message}").format(message=actions.soulseek_login_problem(check, username)),
        ERROR,
        err=True,
    )


def retag_hint(collection_id: int, gui: bool) -> Line:
    """changed tags do not touch files until retag (PLAN.md section 8)."""
    if gui:
        return Line(
            tr(
                "Files are not changed yet. Press WRITE TAGS to write the tags into the "
                "downloaded files."
            )
        )
    return Line(
        tr(
            "Files are not changed yet. To write the tags into downloaded files, run: "
            "neonseek retag --collection {id}"
        ).format(id=collection_id)
    )


# report and retry


def retry_line(count: int) -> Line:
    """the tracks of a retry without downloading, singular for one (D-133)."""
    return Line(
        ntr(
            count,
            "{count} track starts again from FLAC at the next download.",
            "{count} tracks start again from FLAC at the next download.",
        ).format(count=count),
        DONE,
    )


def report_count_text(count: int) -> str:
    if not count:
        return tr("Nothing to report: no track is marked not found or failed.")
    return ntr(count, "{count} track in the report", "{count} tracks in the report").format(
        count=count
    )


def saved_report_line(count: int, path) -> Line:
    return Line(
        ntr(count, "Saved {count} track to {path}", "Saved {count} tracks to {path}").format(
            count=count, path=path
        ),
        DONE,
    )


# excluding, deleting, removing, moving (PLAN.md sections 5 and 8)


def exclude_lines(changed: list[int], asked: list[int], gui: bool) -> list[Line]:
    lines = [
        Line(
            ntr(
                len(changed),
                "Excluded {count} track from downloads.",
                "Excluded {count} tracks from downloads.",
            ).format(count=len(changed)),
            DONE,
        )
    ]
    skipped = len(set(asked) - set(changed))
    if skipped:
        lines.append(
            Line(
                ntr(
                    skipped,
                    "{count} track was not changed: only tracks without a file can be excluded.",
                    "{count} tracks were not changed: only tracks without a file can be excluded.",
                ).format(count=skipped),
                WARN,
            )
        )
    if changed:
        if gui:
            how = tr("Right-click them and choose Include again to download them later.")
        else:
            how = tr("Include them again with: neonseek track include <TRACK ID> ...")
        lines.append(Line(how))
    return lines


def include_lines(changed: list[int], asked: list[int]) -> list[Line]:
    lines = [
        Line(
            ntr(
                len(changed),
                "Included {count} track again. The next download takes it as before.",
                "Included {count} tracks again. The next download takes them as before.",
            ).format(count=len(changed)),
            DONE,
        )
    ]
    skipped = len(set(asked) - set(changed))
    if skipped:
        lines.append(
            Line(
                ntr(skipped, "{count} track was not excluded.",
                    "{count} tracks were not excluded.").format(count=skipped),
                WARN,
            )
        )  # fmt: skip
    return lines


def delete_question(plan: actions.DeletePlan) -> str:
    """the confirmation of delete file, with the number of files (D-127)."""
    count = len(plan.files)
    text = ntr(
        count,
        "Move {count} file to the Windows Recycle Bin?",
        "Move {count} files to the Windows Recycle Bin?",
    ).format(count=count)
    text += " " + tr(
        "The tracks become excluded, so no download brings them back, and the playlists of "
        "their collections are written again. You can restore the files from the Recycle Bin."
    )
    if plan.gone:
        text += " " + ntr(
            len(plan.gone),
            "{count} more file is already gone, its track is excluded too.",
            "{count} more files are already gone, their tracks are excluded too.",
        ).format(count=len(plan.gone))
    return text


def delete_plan_lines(plan: actions.DeletePlan) -> list[Line]:
    """what delete file skips, before and after the question."""
    lines = []
    for ref in plan.outside:
        lines.append(
            Line(
                tr("Not deleted, the file is outside the library folder: {path}").format(
                    path=ref.path
                ),
                WARN,
                err=True,
            )
        )
    if plan.without_file:
        lines.append(
            Line(
                ntr(
                    len(plan.without_file),
                    "{count} track has no downloaded file, nothing to delete.",
                    "{count} tracks have no downloaded file, nothing to delete.",
                ).format(count=len(plan.without_file))
            )
        )
    return lines


def delete_lines(outcome: actions.DeleteOutcome) -> list[Line]:
    lines = []
    # "moved 0 files" says nothing when the files were already gone or failed
    if outcome.recycled or not (outcome.gone or outcome.unconfirmed or outcome.failed):
        lines.append(
            Line(
                ntr(
                    len(outcome.recycled),
                    "Moved {count} file to the Recycle Bin, its track is excluded now.",
                    "Moved {count} files to the Recycle Bin, their tracks are excluded now.",
                ).format(count=len(outcome.recycled)),
                DONE,
            )
        )
    if outcome.gone:
        lines.append(
            Line(
                ntr(
                    len(outcome.gone),
                    "{count} file was already gone, its track is excluded too.",
                    "{count} files were already gone, their tracks are excluded too.",
                ).format(count=len(outcome.gone))
            )
        )
    for ref, _ in outcome.unconfirmed:
        lines.append(
            Line(
                tr(
                    "{label}: the file left the library, but NeonSeek could not confirm it in "
                    "the Recycle Bin. The track is excluded now. Check the Recycle Bin, and if the "
                    "file is not there, include the track again and download it."
                ).format(label=ref.label),
                WARN,
                err=True,
            )
        )
    for ref, problem in outcome.failed:
        lines.append(
            Line(tr("Not deleted: {label}: {problem}").format(label=ref.label, problem=problem),
                 ERROR, err=True)
        )  # fmt: skip
    lines += [Line(tr("Playlist written again: {path}").format(path=p)) for p in outcome.playlists]
    return lines


def file_count_text(count: int) -> str:
    return ntr(count, "{count} file", "{count} files").format(count=count)


def _track_files(count: int) -> str:
    return ntr(count, "{count} track file", "{count} track files").format(count=count)


def _playlist_files(count: int) -> str:
    return ntr(count, "{count} playlist file", "{count} playlist files").format(count=count)


def remove_choice_text(plan: actions.RemovePlan) -> str:
    """the option of the remove window, with the number of files (D-127)."""
    return ntr(
        plan.count,
        "Also move downloaded files to the Recycle Bin ({count} file)",
        "Also move downloaded files to the Recycle Bin ({count} files)",
    ).format(count=plan.count)


def remove_details(plan: actions.RemovePlan) -> list[Line]:
    lines = []
    if plan.files or plan.playlists:
        lines.append(
            Line(
                tr("With this option, {files} and {playlists} go to the Recycle Bin.").format(
                    files=_track_files(len(plan.files)), playlists=_playlist_files(
                        len(plan.playlists))
                )
            )
        )  # fmt: skip
    if plan.shared:
        lines.append(
            Line(
                ntr(
                    plan.shared,
                    "{count} downloaded track is also in another collection, its file stays.",
                    "{count} downloaded tracks are also in other collections, their files stay.",
                ).format(count=plan.shared)
            )
        )
    if plan.outside:
        lines.append(
            Line(
                ntr(
                    plan.outside,
                    "{count} file is outside the library folder, it stays.",
                    "{count} files are outside the library folder, they stay.",
                ).format(count=plan.outside)
            )
        )
    if plan.problem and plan.count:
        lines.append(Line(plan.problem, WARN))
    return lines


def remove_lines(outcome: actions.RemoveOutcome) -> list[Line]:
    ids = ", ".join(map(str, outcome.ids))
    lines = [
        Line(tr("Removed collections {ids}. Tracks and their download state stay.").format(ids=ids),
             DONE)
    ]  # fmt: skip
    lines.append(
        Line(
            tr("Moved to the Recycle Bin: {files} and {playlists}.").format(
                files=_track_files(len(outcome.recycled)),
                playlists=_playlist_files(len(outcome.playlists)),
            )
        )
    )
    for path, _ in outcome.unconfirmed:
        lines.append(
            Line(
                tr(
                    "{path} left the library, but NeonSeek could not confirm it in the Recycle "
                    "Bin. Check the Recycle Bin."
                ).format(path=path),
                WARN,
                err=True,
            )
        )
    for path, problem in outcome.failed:
        lines.append(
            Line(tr("Not deleted: {path}: {problem}").format(path=path, problem=problem),
                 ERROR, err=True)
        )  # fmt: skip
    return lines


def move_plan_lines(plan, gui: bool = False) -> list[Line]:
    """what a library move will do, shown before the question."""
    lines = [
        Line(tr("From: {path}").format(path=plan.old)),
        Line(tr("To:   {path}").format(path=plan.new)),
        Line(
            ntr(
                len(plan.files),
                "{count} downloaded file ({size}) moves, then the playlists.",
                "{count} downloaded files ({size}) move, then the playlists.",
            ).format(count=len(plan.files), size=actions.size_label(plan.size))
        ),
    ]
    if plan.outside:
        lines.append(
            Line(
                ntr(
                    plan.outside,
                    "{count} file is outside the current library folder, it stays where it is.",
                    "{count} files are outside the current library folder, they stay where "
                    "they are.",
                ).format(count=plan.outside),
                WARN,
            )
        )
    if plan.missing:
        lines.append(
            Line(
                ntr(
                    plan.missing,
                    "{count} downloaded file is already missing, nothing to move for it.",
                    "{count} downloaded files are already missing, nothing to move for them.",
                ).format(count=plan.missing),
                WARN,
            )
        )
    if not plan.same_drive:
        # the one exception to "only into the recycle bin" (owner decision, D-153)
        lines.append(
            Line(
                tr(
                    "The new folder is on another drive: each file is copied, the copy is "
                    "checked, and only then the old file is deleted for good, not into the "
                    "Recycle Bin."
                ),
                WARN,
            )
        )
    lines.append(
        Line(
            tr(
                "Files you put into the library folder yourself are not moved. An interrupted "
                "move loses nothing, start it again to finish."
            )
        )
    )
    return lines


def move_question(plan) -> str:
    return ntr(
        len(plan.files),
        "Move {count} downloaded file to {path}?",
        "Move {count} downloaded files to {path}?",
    ).format(count=len(plan.files), path=plan.new)


def move_lines(outcome: actions.LibraryMoveOutcome, gui: bool) -> list[Line]:
    plan, result = outcome.plan, outcome.result
    lines = []
    if outcome.recovered:
        lines.append(
            Line(
                ntr(
                    outcome.recovered,
                    "Finished {count} file of an interrupted move first.",
                    "Finished {count} files of an interrupted move first.",
                ).format(count=outcome.recovered)
            )
        )
    lines.append(
        Line(
            tr("Moved {moved} of {total} files.").format(
                moved=len(result.moved), total=len(plan.files)
            ),
            DONE if outcome.complete else WARN,
        )
    )
    for path, problem in result.failed:
        lines.append(
            Line(tr("Not moved: {path}: {problem}").format(path=path, problem=problem),
                 ERROR, err=True)
        )  # fmt: skip
    if outcome.complete:
        lines.append(Line(tr("The library folder is now {path}.").format(path=plan.new), DONE))
        lines.append(
            Line(
                tr(
                    "rekordbox still knows the old file locations: import the playlists from the "
                    "new folder again, or use Relocate in rekordbox."
                )
            )
        )
    else:
        if result.cancelled:
            lines.append(Line(tr("The move was stopped."), WARN, err=True))
        if gui:
            again = tr("To finish, choose the same new folder in Settings again.")
        else:
            again = tr('To finish, run: neonseek library move "{path}"').format(path=plan.new)
        lines.append(
            Line(
                tr(
                    "The library folder stays {old}. Moved files are already in the new folder "
                    "and the playlists point to them."
                ).format(old=plan.old)
                + " "
                + again,
                WARN,
                err=True,
            )
        )
    lines += [Line(tr("Playlist: {path}").format(path=p)) for p in outcome.playlists]
    return lines


def list_refresh_failed_line(message: str) -> Line:
    """the playlist list at start: no internet is no error, only a line (D-130)."""
    return Line(
        tr("Could not refresh your Spotify playlists ({message}). The list from last time stays.")
        .format(message=message)
    )  # fmt: skip
