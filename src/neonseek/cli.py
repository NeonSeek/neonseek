"""command line interface. every gui action has a command here."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import webbrowser
from collections.abc import Sequence
from pathlib import Path

import neonseek
from neonseek import actions, config, i18n, manual, outcomes, recycle, views
from neonseek.errors import NeonSeekError
from neonseek.i18n import N_, ntr, tr
from neonseek.pipeline import runner
from neonseek.sources import csv_file, spotify_url
from neonseek.sources.base import row_name
from neonseek.spotify import auth as spotify_auth

EXIT_OK = 0
EXIT_ERROR = 1


def _parse_map(values: Sequence[str]) -> dict[str, str | None]:
    overrides: dict[str, str | None] = {}
    for value in values:
        for part in value.split(","):
            if not part.strip():
                continue
            if "=" not in part:
                raise NeonSeekError(
                    tr("Write --map as field=column, for example --map title=Song.")
                )
            name, column = part.split("=", 1)
            overrides[name.strip().lower()] = column.strip() or None
    return overrides


def _print_lines(lines) -> None:
    for line in lines:
        print(line.text, file=sys.stderr if line.err else sys.stdout, flush=True)


def _print_preview(parsed: csv_file.CsvParse) -> None:
    table = parsed.table
    print(
        tr("File: {path} ({encoding}, delimiter {delimiter!r})").format(
            path=table.path, encoding=table.encoding, delimiter=table.delimiter
        )
    )
    _print_lines(outcomes.mapping_lines(parsed))
    print()
    for index, track in enumerate(parsed.tracks[: csv_file.PREVIEW_ROWS], start=1):
        artists = "; ".join(track.artists) or "-"
        extra = []
        if track.album:
            extra.append(track.album)
        if track.isrc:
            extra.append(f"isrc {track.isrc}")
        if track.spotify_id:
            extra.append(f"spotify {track.spotify_id}")
        suffix = f"  [{' | '.join(extra)}]" if extra else ""
        length = views.length_label(track.duration_ms)
        print(f"{index:>3}. {artists} - {track.title} ({length}){suffix}")
    print()
    _print_counts(parsed)


def _print_counts(parsed: csv_file.CsvParse) -> None:
    print(tr("Tracks in file: {count}").format(count=len(parsed.tracks)))
    _print_lines(outcomes.count_lines(parsed))


def cmd_import_csv(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    overrides = _parse_map(args.map) or None
    if args.preview:
        _print_preview(actions.preview_csv(args.file, overrides, args.length_unit))
        return EXIT_OK
    outcome = actions.import_csv(
        ctx,
        args.file,
        name=args.name,
        overrides=overrides,
        length_unit=args.length_unit,
        collection_id=args.collection,
    )
    _print_import(outcome)
    return EXIT_OK


def _print_import(outcome: actions.CsvImportOutcome) -> None:
    _print_lines(outcomes.import_lines(outcome))


def cmd_reimport(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    _print_import(actions.reimport_csv(ctx, args.collection))
    return EXIT_OK


def _collection_json(row, entries: dict[int, int]) -> dict:
    """one collection for scripts: the keys stay english and never change with the language.
    tracks are different tracks, entries the places in the list with repeats (D-259)."""
    read = views.content_read(row)
    return {
        "id": row.id,
        "kind": row.kind,
        "origin": row.origin,
        "name": row.name,
        "selected": bool(row.selected),
        "readable": bool(row.is_readable),
        "read": read,
        "tracks": views.wanted_count(row) if read else row.track_count,
        "entries": entries.get(row.id, 0),
        "done": row.done,
        "source": row.source_ref,
    }


def cmd_collections(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    rows = actions.list_collections(ctx)
    if getattr(args, "json", False):
        # a hidden diagnostic option for the smoke test (D-254), ascii so any console reads it
        entries = actions.collection_entries(ctx)
        print(json.dumps([_collection_json(row, entries) for row in rows]))
        return EXIT_OK
    if not rows:
        print(tr("No collections yet. Import a CSV file or add a Spotify link."))
        return EXIT_OK
    print(
        f"{'ID':>4}  {tr('SEL'):3}  {tr('KIND'):16}  {tr('TRACKS'):>11}  {tr('STATE'):10}  "
        f"{tr('NAME')}"
    )
    for row in rows:
        if not row.is_readable:
            state = tr("locked")
        elif row.last_synced_at is None:
            state = tr("not synced")
        else:
            state = tr("ok")
        tracks = views.collection_counter(row)
        mark = "[x]" if row.selected else "[ ]"
        print(f"{row.id:>4}  {mark}  {row.kind:16}  {tracks:>11}  {state:10}  {row.name}")
        tags = _tags_line(row.genre, row.comment)
        if tags:
            print(f"{'':>4}  {'':3}  {'':16}  {'':>11}  {'':10}  {tags}")
    return EXIT_OK


def cmd_tracks(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    if args.all:
        print(tr("Whole library"))
        everything = views.library_tracks(ctx)
        hint = tr("No tracks yet. Import a CSV file or add a Spotify link.")
    else:
        if args.collection is None:
            raise NeonSeekError(tr("Name a collection with --collection ID, or use --all."))
        summary = next((c for c in actions.list_collections(ctx) if c.id == args.collection), None)
        if summary is None:
            raise NeonSeekError(
                tr("There is no collection with id {id}. Check the list of collections.").format(
                    id=args.collection
                )
            )
        print(f"[{summary.id}] {summary.name}")
        everything = views.collection_tracks(ctx, args.collection)
        hint = views.empty_hint(summary)
    print(views.stats_text(everything))
    rows = [
        r
        for r in everything
        if views.state_matches(r.state, args.status) and views.track_matches(r, args.search or "")
    ]
    if not rows:
        print(hint if not everything else tr("No tracks."))
        return EXIT_OK
    # the id is what neonseek track, download --track and retry take
    print(f"{'#':>4}  {'ID':>6}  {tr('STATUS'):22}  {tr('LENGTH'):>7}  {tr('TRACK')}")
    for row in rows:
        print(
            f"{row.number:>4}  {row.track_id:>6}  {row.state_label:22}  {row.length_label:>7}  "
            f"{row.label}" + (f"  [{row.album}]" if row.album else "")
        )
        details = "; ".join(part for part in (row.file_label, row.reason) if part)
        if details:
            print(f"{'':>4}  {'':>6}  {'':22}  {'':>7}  {details}")
    _print_legend(rows, None if args.all else args.collection)
    return EXIT_OK


def _print_legend(rows: list[views.TrackRow], collection_id: int | None) -> None:
    """what each state in the list means and what to do next, once per state."""
    seen: dict[str, views.TrackRow] = {}
    for row in rows:
        seen.setdefault(row.state_label, row)
    if not seen:
        return
    print()
    for label, row in seen.items():
        hint = views.state_hint(row.state, row.stage, gui=False, attempts=row.attempts,
                                max_attempts=row.max_attempts,
                                collection_id=collection_id)  # fmt: skip
        if hint:
            print(f"{label}: {hint}")


def cmd_themes(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    current = ctx.settings().theme
    for key, theme in actions.available_themes(ctx, _progress).items():
        mark = "*" if key == current else " "
        own = "" if theme.builtin else "  " + tr("(your own)")
        print(f"{mark} {key:12} {theme.name}{own}")
    print(tr("Change it with: neonseek settings theme <name>"))
    return EXIT_OK


def cmd_folder(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    if args.collection is None:
        print(actions.library_dir(ctx.settings()))
        return EXIT_OK
    print(actions.collection_folder(ctx, args.collection).path)
    return EXIT_OK


def cmd_collection_info(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    summary = next((c for c in actions.list_collections(ctx) if c.id == args.collection), None)
    if summary is None:
        raise NeonSeekError(
            tr("There is no collection with id {id}. Check the list of collections.").format(
                id=args.collection
            )
        )
    source = actions.collection_source(ctx, args.collection)
    print(f"[{summary.id}] {summary.name}")
    print(views.collection_kind_label(summary.kind, summary.origin))
    print(tr("Source: {source}").format(source=source.url or source.path or tr("(none)")))
    print(tr("Tracks: {counter} ({state})").format(
        counter=views.collection_counter(summary), state=views.synced_label(summary)
    ))  # fmt: skip
    print(tr("Selected: {value}").format(value=tr("yes") if summary.selected else tr("no")))
    hint = views.locked_hint(summary)
    if hint:
        print(hint)
    tags = _tags_line(summary.genre, summary.comment)
    if tags:
        print(tags)
    return EXIT_OK


def cmd_help(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    """the manual of the window in the terminal: contents, one chapter, or the pdf (D-164)."""
    language = i18n.language()
    source = manual.source_path(language)
    if args.pdf:
        pdf = manual.built_pdf(language)
        if not pdf.exists():
            raise NeonSeekError(
                tr(
                    "The PDF of the manual is not built yet. In the window, press HELP and then "
                    "OPEN PDF, it builds the PDF. The manual itself is here: {path}"
                ).format(path=source)
            )
        print(pdf)
        return EXIT_OK
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise NeonSeekError(
            tr("The manual is missing ({error}). Install NeonSeek again.").format(error=exc)
        ) from exc
    if args.chapter is not None:
        try:
            print(manual.chapter_text(text, args.chapter))
        except IndexError:
            raise NeonSeekError(
                tr("There is no chapter {number}. Run neonseek help for the list.").format(
                    number=args.chapter
                )
            ) from None
        return EXIT_OK
    print(tr("NeonSeek manual: {path}").format(path=source))
    print()
    number = 0
    for chapter in manual.chapters(text):
        if chapter.level == 2:
            number += 1
            print(f"{number:>3}. {chapter.title}")
        else:
            print(f"       {chapter.title}")
    print()
    print(tr("Show a chapter with: neonseek help <number>"))
    return EXIT_OK


def cmd_about(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    if args.diagnostics:
        print(actions.diagnostics(ctx))
        return EXIT_OK
    print(f"NeonSeek {neonseek.__version__}")
    print(actions.app_license())
    print(actions.source_code())
    for caption, url in actions.project_links():
        print(f"{caption}: {url}")
    print(actions.qt_license())
    print(tr("Full texts of all licenses: {path}").format(path=actions.license_texts()))
    try:
        info = actions.sockseek_info(ctx)
        print(tr("Sockseek {version}: {path}").format(version=info.version, path=info.path))
        print(info.source)
    except NeonSeekError as exc:
        print(tr("Sockseek: {message}").format(message=exc.message), file=sys.stderr)
    print()
    print(tr("Parts of NeonSeek and their licenses:"))
    for name, version, license_name in actions.third_party():
        print(f"  {name} {version}: {license_name}")
    return EXIT_OK


def _tags_line(genre: str | None, comment: str | None) -> str:
    parts = []
    if genre:
        parts.append(tr("genre: {value}").format(value=genre))
    if comment:
        parts.append(tr("comment: {value}").format(value=comment))
    return " | ".join(parts)


def cmd_select(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    ids = actions.set_selected(ctx, args.ids, selected=args.command == "select")
    word = tr("Selected") if args.command == "select" else tr("Deselected")
    print(tr("{word}: {ids}").format(word=word, ids=", ".join(map(str, ids))))
    return EXIT_OK


def cmd_remove(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    if args.delete_files:
        plan = actions.remove_plan(ctx, args.ids)
        _print_lines(outcomes.remove_details(plan))
        if plan.count and plan.problem:
            raise recycle.RecycleError(plan.problem)
        question = tr("Remove {names} and move {files} to the Recycle Bin?").format(
            names=", ".join(f"[{cid}] {name}" for cid, name in plan.collections),
            files=outcomes.file_count_text(plan.count),
        )
        if not _allowed(args, question, tr("Nothing was removed.")):
            return EXIT_ERROR
        _print_lines(outcomes.remove_lines(actions.remove_collections_and_files(ctx, args.ids)))
        return EXIT_OK
    ids = actions.remove_collections(ctx, args.ids)
    print(
        tr("Removed collections {ids}. Tracks, download state and files stay.").format(
            ids=", ".join(map(str, ids))
        )
    )
    return EXIT_OK


def cmd_spotify_client_id(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    if args.client_id is None:
        current = actions.spotify_client_id(ctx)
        print(current or tr("No Spotify Client ID set."))
        return EXIT_OK
    actions.set_spotify_client_id(ctx, args.client_id)
    print(tr("Spotify Client ID saved."))
    print(
        tr("In your Spotify app, the Redirect URI must be exactly: {uri}").format(
            uri=spotify_auth.REDIRECT_URI
        )
    )
    return EXIT_OK


def cmd_spotify_login(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    flow = actions.start_spotify_login(ctx)
    print(tr("Log in to Spotify in your browser. If it does not open, go to:"))
    print(flow.url, flush=True)
    if not args.no_browser:
        webbrowser.open(flow.url)
    me = actions.finish_spotify_login(ctx, flow)
    print(tr("Logged in to Spotify as {name}.").format(name=me.get("display_name") or me["id"]))
    return EXIT_OK


def cmd_spotify_logout(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    actions.spotify_logout(ctx)
    print(tr("Logged out of Spotify. The saved login was removed from this computer."))
    return EXIT_OK


def cmd_spotify_status(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    me = actions.spotify_me(ctx)
    print(tr("Logged in to Spotify as {name}.").format(name=me.get("display_name") or me["id"]))
    return EXIT_OK


def _confirm(question: str) -> bool:
    # english yes always works, a translation adds its own words (t, tak)
    answers = {"y", "yes", tr("y").lower(), tr("yes").lower()}
    try:
        return input(f"{question} {tr('[y/N]')} ").strip().lower() in answers
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def _allowed(args: argparse.Namespace, question: str, nothing: str) -> bool:
    """--yes, or a yes typed in a terminal. without a terminal nothing happens."""
    if args.yes:
        return True
    if not sys.stdin or not sys.stdin.isatty():
        print(
            tr("{nothing} To go ahead without a question, add --yes.").format(nothing=nothing),
            file=sys.stderr,
        )
        return False
    if not _confirm(question):
        print(tr("Cancelled. {nothing}").format(nothing=nothing), file=sys.stderr)
        return False
    return True


# one track (PLAN.md section 9, the track menu)


def cmd_track_info(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    settings = ctx.settings()
    for index, track_id in enumerate(dict.fromkeys(args.ids)):
        info = actions.track_info(ctx, track_id)
        if index:
            print()
        sections = views.track_info_sections(
            info.row, info.collections, info.file_exists, settings, gui=False
        )
        for title, rows in sections:
            print(title)
            for label, value in rows:
                print(f"  {label + ':':24} {value}")
    return EXIT_OK


def cmd_track_folder(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    info = actions.track_info(ctx, args.id)
    if info.file_exists is None:
        raise NeonSeekError(
            tr("Track {id} has no downloaded file. Only downloaded tracks have one.").format(
                id=args.id
            )
        )
    print(info.row["file_path"])
    if not info.file_exists:
        print(tr("The file is no longer there."), file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def cmd_track_edit(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    if args.artist is None and args.title is None:
        raise NeonSeekError(tr("Give --artist, --title or both."))
    info = actions.edit_track(ctx, args.id, args.artist, args.title)
    print(outcomes.edited_line(info).text)
    return EXIT_OK


def cmd_track_exclude(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    changed = actions.exclude_tracks(ctx, args.ids)
    _print_lines(outcomes.exclude_lines(changed, args.ids, gui=False))
    return EXIT_OK


def cmd_track_include(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    changed = actions.include_tracks(ctx, args.ids)
    _print_lines(outcomes.include_lines(changed, args.ids))
    return EXIT_OK


def cmd_track_delete_file(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    plan = actions.delete_plan(ctx, args.ids)
    _print_lines(outcomes.delete_plan_lines(plan))
    if not plan.files and not plan.gone:
        print(tr("Nothing to delete."))
        return EXIT_OK
    if plan.files and plan.problem:
        raise recycle.RecycleError(plan.problem)
    nothing = tr("Nothing was deleted.")
    if plan.files and not _allowed(args, outcomes.delete_question(plan), nothing):
        return EXIT_ERROR
    outcome = actions.delete_track_files(ctx, args.ids)
    _print_lines(outcomes.delete_lines(outcome))
    return EXIT_ERROR if outcome.failed or outcome.unconfirmed else EXIT_OK


def cmd_library_move(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    plan = actions.library_move_plan(ctx, args.folder)
    _print_lines(outcomes.move_plan_lines(plan))
    if not _allowed(args, outcomes.move_question(plan), tr("Nothing was moved.")):
        return EXIT_ERROR

    def progress(done: int, total: int, name: str) -> None:
        if name:
            print(tr("  {done}/{total}  {name}").format(done=done + 1, total=total, name=name),
                  flush=True)  # fmt: skip

    outcome = actions.move_library(ctx, args.folder, progress)
    _print_lines(outcomes.move_lines(outcome, gui=False))
    return EXIT_OK if outcome.complete else EXIT_ERROR


def cmd_soulseek_login(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    print(tr("Your Soulseek login is kept in Windows Credential Manager, never in a file."))
    saved = actions.soulseek_login(ctx)
    if saved is not None and not args.yes:
        replace = tr("This replaces the saved Soulseek login for {user}. Continue?").format(
            user=saved.username
        )
        if not _confirm(replace):
            print(tr("Cancelled, nothing was saved."), file=sys.stderr)
            return EXIT_ERROR
    try:
        if args.new:
            print(
                tr(
                    "Choose a name for your new Soulseek account. NeonSeek makes a strong "
                    "password and keeps it for you, you never need to type it."
                )
            )
            username = args.username or input(tr("New Soulseek username: "))
        else:
            username = args.username or input(tr("Soulseek username: "))
            # getpass shows nothing while typing, the password never reaches the screen
            password = getpass.getpass(tr("Soulseek password (hidden): "))
            again = getpass.getpass(tr("Repeat the password (hidden): "))
    except (EOFError, KeyboardInterrupt):
        print()
        print(tr("Cancelled, nothing was saved."), file=sys.stderr)
        return EXIT_ERROR
    print(actions.soulseek_check_started_message(), flush=True)
    if args.new:
        outcome = actions.create_soulseek_account(ctx, username)
        _print_lines(outcomes.soulseek_saved_lines(outcome, new_account=True))
        return EXIT_OK
    if password != again:
        raise actions.SoulseekLoginError(
            tr("The two passwords are different. Nothing was saved, run the command again.")
        )
    if password and password.casefold() == username.strip().casefold():
        print(tr("Warning: {text}").format(text=actions.same_as_name_warning()), file=sys.stderr)
    outcome = actions.use_own_soulseek_account(ctx, username, password)
    # the same-as-name warning was printed before connecting
    outcome.warnings = []
    _print_lines(outcomes.soulseek_saved_lines(outcome, new_account=False))
    return EXIT_OK


def cmd_soulseek_status(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    login = actions.soulseek_login(ctx)
    if login is None:
        print(tr("No Soulseek login saved. Run: neonseek soulseek login --new"))
        return EXIT_ERROR
    print(tr('Soulseek login saved for "{user}".').format(user=login.username))
    return EXIT_OK


def cmd_soulseek_test(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    login = actions.soulseek_login(ctx)
    if login is None:
        raise actions.NoSoulseekLoginError(actions.no_soulseek_login_message())
    print(actions.soulseek_check_started_message(), flush=True)
    check = actions.verify_soulseek_login(ctx)
    _print_lines([outcomes.soulseek_check_line(check, login.username)])
    return EXIT_OK if check.ok else EXIT_ERROR


def cmd_soulseek_logout(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    actions.forget_soulseek_login(ctx)
    print(tr("Soulseek login removed from this computer."))
    return EXIT_OK


def _progress(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def cmd_sync(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    if args.list_only:
        report = actions.sync_spotify(ctx, progress=_progress, list_only=True)
        result = outcomes.sync_lines(report)
        _print_lines(result.lines)
        return EXIT_ERROR if result.failed else EXIT_OK
    outcome = actions.sync(ctx, args.collection, progress=_progress)
    result = outcomes.all_sync_lines(outcome)
    _print_lines(result.lines)
    if outcomes.nothing_synced(outcome) and args.collection is None:
        only_spotify = not actions.has_web_collections(ctx)
        _print_lines(
            [outcomes.no_selected_spotify_line() if only_spotify else outcomes.no_selected_line()]
        )
    return EXIT_ERROR if result.failed else EXIT_OK


def cmd_add(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    results = actions.add_links(ctx, args.urls, progress=_progress)
    _print_lines(outcomes.add_lines(results))
    failed = any(r.outcome not in (spotify_url.ADDED, spotify_url.EXISTS) for r in results)
    return EXIT_ERROR if failed else EXIT_OK


def cmd_settings(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    if args.key is None:
        settings = ctx.settings()
        for key in config.EDITABLE:
            value = _setting_text(getattr(settings, key))
            print(f"{key} = {value if value != '' else tr('(not set)')}")
        return EXIT_OK
    if args.value is None:
        print(_setting_text(getattr(ctx.settings(), config.check_key(args.key))))
        return EXIT_OK
    old_library = ctx.settings().library_dir
    stored = actions.change_setting(ctx, args.key, args.value)
    print(tr("{key} = {value}").format(key=args.key, value=_setting_text(stored)))
    if args.key == "library_dir" and old_library and old_library != stored:
        files = actions.library_files_in(ctx, old_library)
        if files:
            print(
                ntr(
                    files,
                    "{count} downloaded file stays in {old}. To move it too, set the old folder "
                    'back and run: neonseek library move "{new}"',
                    "{count} downloaded files stay in {old}. To move them too, set the old "
                    'folder back and run: neonseek library move "{new}"',
                ).format(count=files, old=old_library, new=stored)
            )
    return EXIT_OK


def _setting_text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def cmd_collection_tags(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    row = actions.set_collection_tags(ctx, args.collection, args.genre, args.comment)
    label = f"[{row['id']}] {row_name(row)}"
    _print_lines([outcomes.tags_saved_line(row["genre"], row["comment"], label)])
    if args.genre is not None or args.comment is not None:
        _print_lines([outcomes.retag_hint(row["id"], gui=False)])
    return EXIT_OK


def _chosen_collections(
    ctx: actions.AppContext, args: argparse.Namespace, none_selected: str, choose: str
):
    """the --collection / --selected choice of download and retag. none means stop. the two
    texts are whole sentences, each language says them its own way."""
    if args.selected:
        ids = actions.selected_collection_ids(ctx)
        if not ids:
            print(none_selected, file=sys.stderr)
            cmd_collections(ctx, args)
            return None
        return ids
    if args.collection:
        return args.collection
    print(choose, file=sys.stderr)
    cmd_collections(ctx, args)
    return None


def cmd_retag(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    ids = _chosen_collections(
        ctx,
        args,
        tr(
            "No collection is selected, nothing to retag. Select some with: neonseek select "
            "<ID>, or name one with --collection ID."
        ),
        tr(
            "Choose what to retag: --collection ID (can be given more than once) or --selected "
            "for the selected collections."
        ),
    )
    if ids is None:
        return EXIT_ERROR
    outcome = actions.retag(ctx, ids)
    _print_lines(outcomes.retag_lines(outcome))
    return EXIT_ERROR if outcome.failed else EXIT_OK


def _print_plan(plan: actions.DownloadPlan) -> None:
    _print_lines(outcomes.plan_lines(plan))


class _DownloadPrinter:
    def __init__(self) -> None:
        self.monitor = outcomes.RunMonitor()

    def __call__(self, event) -> None:
        _print_lines(self.monitor.feed(event))


def _print_outcome(ctx: actions.AppContext, outcome: runner.RunOutcome) -> None:
    _print_lines(outcomes.run_end_lines(ctx, outcome))


def cmd_download(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    if args.track:
        if args.collection or args.selected:
            raise NeonSeekError(tr("Use --track, or --collection / --selected, not both."))
        return _download(ctx, args, [], args.track)
    ids = _chosen_collections(
        ctx,
        args,
        tr(
            "No collection is selected, nothing to download. Select some with: neonseek select "
            "<ID>, or name one with --collection ID."
        ),
        tr(
            "Choose what to download: --collection ID (can be given more than once) or "
            "--selected for the selected collections."
        ),
    )
    if ids is None:
        return EXIT_ERROR
    return _download(ctx, args, ids, None)


def _download(
    ctx: actions.AppContext,
    args: argparse.Namespace,
    ids: list[int],
    track_ids: list[int] | None,
) -> int:
    """collections, or only the chosen tracks (download --track, retry --download)."""
    limit = getattr(args, "limit", None)
    if limit is not None and limit < 1:
        raise NeonSeekError(tr("--limit must be 1 or more."))
    with actions.DownloadJob(ctx, ids, limit, track_ids=track_ids) as job:
        _print_plan(job.plan)
        if job.plan.nothing_to_do:
            written = actions.write_playlists(ctx, [cid for cid, _ in job.plan.collections])
            _print_lines(outcomes.nothing_to_download_lines(written))
            return EXIT_OK
        if not args.yes:
            if not sys.stdin or not sys.stdin.isatty():
                print(
                    tr("Nothing was downloaded. To start without a question, add --yes."),
                    file=sys.stderr,
                )
                return EXIT_ERROR
            if not _confirm(tr("Start the download?")):
                print(tr("Cancelled, nothing was downloaded."), file=sys.stderr)
                return EXIT_ERROR
        outcome = job.run(_DownloadPrinter())
    _print_outcome(ctx, outcome)
    return EXIT_OK if outcome.ended == runner.COMPLETED else EXIT_ERROR


def cmd_report(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    rows = actions.report(ctx, args.collection or None)
    if args.csv:
        actions.export_report(rows, args.csv)
        _print_lines([outcomes.saved_report_line(len(rows), args.csv)])
        return EXIT_OK
    if not rows:
        print(tr("Nothing to report: no track is marked not found or failed."))
        return EXIT_OK
    for row in rows:
        if row.waiting_fallback:
            status = tr("waiting for WAV/AIFF")
        elif row.status == "not_found":
            status = tr("not found")
        else:
            status = tr("failed {attempts}/{stage}").format(attempts=row.attempts, stage=row.stage)
        names = ", ".join(name for _, name in row.collections)
        print(f"{row.track_id:>6}  {status:22}  {row.label}")
        details = "; ".join(part for part in (row.reason, names) if part)
        if details:
            print(f"{'':>6}  {'':22}  {details}")
    print(
        tr(
            "Download them again now with: neonseek retry <TRACK ID> ... --download (starts "
            "again from FLAC)"
        )
    )
    return EXIT_OK


def cmd_retry(ctx: actions.AppContext, args: argparse.Namespace) -> int:
    if not args.tracks and not args.collection:
        raise NeonSeekError(
            tr("Name the tracks to retry (IDs from neonseek report) or use --collection ID.")
        )
    if args.download:
        # retry and download at once, like retry selected in the report (D-131)
        ids = list(args.tracks)
        if args.collection:
            ids += [r.track_id for r in actions.report(ctx, args.collection)]
        if not ids:
            print(tr("Nothing to retry: no track of these collections is in the report."))
            return EXIT_OK
        return _download(ctx, args, [], list(dict.fromkeys(ids)))
    changed = actions.retry_tracks(ctx, args.tracks, args.collection or ())
    skipped = [t for t in args.tracks if t not in changed]
    _print_lines([outcomes.retry_line(len(changed))])
    if skipped:
        print(tr("Not changed (not in the report): {ids}").format(ids=", ".join(map(str, skipped))))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """every help text goes through tr(), so set the language first (D-163)."""
    parser = argparse.ArgumentParser(
        prog="neonseek",
        description=tr(
            "Download track lists from Spotify or CSV from Soulseek in lossless quality."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=neonseek.__version__,
        help=tr("show the version number and exit"),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        metavar=tr("FOLDER"),
        help=tr("use this folder instead of %%APPDATA%%\\NeonSeek (for testing)"),
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar=tr("COMMAND"))
    track_id = tr("TRACK_ID")

    imp = sub.add_parser("import", help=tr("import a track list"))
    imp_sub = imp.add_subparsers(dest="import_kind", required=True, metavar=tr("KIND"))
    imp_csv = imp_sub.add_parser("csv", help=tr("import a csv file, no spotify needed"))
    imp_csv.add_argument("file", type=Path, metavar=tr("FILE"))
    imp_csv.add_argument("--name", help=tr("collection name (default: file name)"))
    imp_csv.add_argument(
        "--map",
        action="append",
        default=[],
        metavar=tr("FIELD=COLUMN[,...]"),
        help=tr("pick columns by hand, fields: {fields}. an empty column unmaps the field").format(
            fields=", ".join(csv_file.FIELDS)
        ),
    )
    imp_csv.add_argument(
        "--length-unit",
        choices=[actions.AUTO_UNIT, *csv_file.LENGTH_UNITS],
        help=tr("unit of the length column (clock means m:ss or h:mm:ss)"),
    )
    imp_csv.add_argument(
        "--collection",
        type=int,
        metavar="ID",
        help=tr("point an existing csv collection at this file (when the file was moved)"),
    )
    imp_csv.add_argument(
        "--preview", action="store_true", help=tr("show the mapping and first rows, import nothing")
    )
    imp_csv.set_defaults(func=cmd_import_csv)

    reimport = sub.add_parser("reimport", help=tr("read a csv collection again from its file"))
    reimport.add_argument("collection", type=int, metavar="ID")
    reimport.set_defaults(func=cmd_reimport)

    collections = sub.add_parser("collections", help=tr("list collections"))
    # for scripts only, not in --help or the manual (D-254)
    collections.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    collections.set_defaults(func=cmd_collections)

    tracks = sub.add_parser("tracks", help=tr("list the tracks of a collection with their state"))
    tracks_scope = tracks.add_mutually_exclusive_group()
    tracks_scope.add_argument(
        "--collection", type=int, metavar="ID", help=tr("the tracks of this collection")
    )
    tracks_scope.add_argument("--all", action="store_true", help=tr("every track of the library"))
    tracks.add_argument(
        "--search",
        metavar=tr("TEXT"),
        help=tr("only tracks with every word in title, artist or album"),
    )
    tracks.add_argument(
        "--status", choices=views.FILTERS, default=views.FILTER_ALL,
        help=tr("only tracks to download, downloaded ones, or not found and failed ones"),
    )  # fmt: skip
    tracks.set_defaults(func=cmd_tracks)

    for name, text in (
        ("select", tr("mark collections for sync and download")),
        ("deselect", tr("unmark collections")),
    ):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("ids", type=int, nargs="+", metavar="ID")
        cmd.set_defaults(func=cmd_select)

    remove = sub.add_parser("remove", help=tr("remove collections, files stay in the library"))
    remove.add_argument("ids", type=int, nargs="+", metavar="ID")
    remove.add_argument(
        "--delete-files",
        action="store_true",
        help=tr(
            "also move downloaded files to the recycle bin (only tracks in no other "
            "collection, and the playlist file)"
        ),
    )
    remove.add_argument("--yes", action="store_true", help=tr("delete without asking"))
    remove.set_defaults(func=cmd_remove)

    track = sub.add_parser(
        "track", help=tr("one or more tracks: info, file, edit, exclude, delete")
    )
    track_sub = track.add_subparsers(dest="track_command", required=True, metavar=tr("ACTION"))
    info_cmd = track_sub.add_parser("info", help=tr("everything known about tracks (properties)"))
    info_cmd.add_argument("ids", type=int, nargs="+", metavar=track_id)
    info_cmd.set_defaults(func=cmd_track_info)
    folder_cmd = track_sub.add_parser("folder", help=tr("where the file of a track is"))
    folder_cmd.add_argument("id", type=int, metavar=track_id)
    folder_cmd.set_defaults(func=cmd_track_folder)
    edit_cmd = track_sub.add_parser(
        "edit", help=tr("correct the artist or title of a track before it is searched")
    )
    edit_cmd.add_argument("id", type=int, metavar=track_id)
    edit_cmd.add_argument("--artist", metavar=tr("TEXT"), help=tr("the artist to search for"))
    edit_cmd.add_argument("--title", metavar=tr("TEXT"), help=tr("the title to search for"))
    edit_cmd.set_defaults(func=cmd_track_edit)
    exclude_cmd = track_sub.add_parser("exclude", help=tr("no download takes these tracks"))
    exclude_cmd.add_argument("ids", type=int, nargs="+", metavar=track_id)
    exclude_cmd.set_defaults(func=cmd_track_exclude)
    include_cmd = track_sub.add_parser("include", help=tr("excluded tracks come back as before"))
    include_cmd.add_argument("ids", type=int, nargs="+", metavar=track_id)
    include_cmd.set_defaults(func=cmd_track_include)
    delete_cmd = track_sub.add_parser(
        "delete-file",
        help=tr("move the files of tracks to the recycle bin and exclude the tracks"),
    )
    delete_cmd.add_argument("ids", type=int, nargs="+", metavar=track_id)
    delete_cmd.add_argument("--yes", action="store_true", help=tr("delete without asking"))
    delete_cmd.set_defaults(func=cmd_track_delete_file)

    library = sub.add_parser("library", help=tr("the music library folder"))
    library_sub = library.add_subparsers(
        dest="library_command", required=True, metavar=tr("ACTION")
    )
    move = library_sub.add_parser(
        "move", help=tr("move the downloaded files and playlists to another folder")
    )
    move.add_argument("folder", metavar=tr("FOLDER"))
    move.add_argument("--yes", action="store_true", help=tr("move without asking"))
    move.set_defaults(func=cmd_library_move)

    spotify = sub.add_parser("spotify", help=tr("spotify login and settings"))
    spotify_sub = spotify.add_subparsers(
        dest="spotify_command", required=True, metavar=tr("ACTION")
    )
    client_id = spotify_sub.add_parser("client-id", help=tr("show or set your spotify client id"))
    client_id.add_argument("client_id", nargs="?", metavar="CLIENT_ID")
    client_id.set_defaults(func=cmd_spotify_client_id)
    login = spotify_sub.add_parser("login", help=tr("log in to spotify in the browser"))
    login.add_argument("--no-browser", action="store_true", help=tr("only print the login link"))
    login.set_defaults(func=cmd_spotify_login)
    logout = spotify_sub.add_parser("logout", help=tr("forget the spotify login"))
    logout.set_defaults(func=cmd_spotify_logout)
    status = spotify_sub.add_parser("status", help=tr("check the spotify login"))
    status.set_defaults(func=cmd_spotify_status)

    soulseek = sub.add_parser("soulseek", help=tr("soulseek login"))
    soulseek_sub = soulseek.add_subparsers(
        dest="soulseek_command", required=True, metavar=tr("ACTION")
    )
    slsk_login = soulseek_sub.add_parser(
        "login",
        help=tr("log in with your soulseek account (password hidden), or create one with --new"),
    )
    slsk_login.add_argument(
        "--new",
        action="store_true",
        help=tr("create a new account: you only choose the name, neonseek makes the password"),
    )
    slsk_login.add_argument("--username", help=tr("skip the username question"))
    slsk_login.add_argument(
        "--yes", action="store_true", help=tr("replace a saved login without asking")
    )
    slsk_login.set_defaults(func=cmd_soulseek_login)
    slsk_status = soulseek_sub.add_parser("status", help=tr("show which soulseek login is saved"))
    slsk_status.set_defaults(func=cmd_soulseek_status)
    slsk_test = soulseek_sub.add_parser(
        "test", help=tr("log in once with the saved login, without any search")
    )
    slsk_test.set_defaults(func=cmd_soulseek_test)
    slsk_logout = soulseek_sub.add_parser("logout", help=tr("forget the soulseek login"))
    slsk_logout.set_defaults(func=cmd_soulseek_logout)

    sync = sub.add_parser(
        "sync",
        help=tr(
            "read selected collections again: spotify, apple music, youtube, soundcloud and tidal"
        ),
    )
    sync_scope = sync.add_mutually_exclusive_group()
    sync_scope.add_argument(
        "--collection", type=int, metavar="ID", help=tr("only this collection, selected or not")
    )
    sync_scope.add_argument(
        "--list-only",
        action="store_true",
        help=tr("only the list of playlists, no content (what the window does at start)"),
    )
    sync.set_defaults(func=cmd_sync)

    add = sub.add_parser(
        "add",
        help=tr(
            "add playlist, album or track links of spotify, apple music, youtube, soundcloud "
            "or tidal"
        ),
    )
    add.add_argument("urls", nargs="+", metavar="URL")
    add.set_defaults(func=cmd_add)

    def choose_collections(cmd: argparse.ArgumentParser, one: str, selected: str) -> None:
        group = cmd.add_mutually_exclusive_group()
        group.add_argument(
            "--collection", type=int, action="append", default=[], metavar="ID", help=one
        )
        group.add_argument("--selected", action="store_true", help=selected)

    download = sub.add_parser("download", help=tr("download missing tracks of collections"))
    choose_collections(
        download,
        tr("download this collection, selected or not (can be given more than once)"),
        tr("download the selected collections"),
    )
    download.add_argument(
        "--track",
        type=int,
        action="append",
        default=[],
        metavar=track_id,
        help=tr("only this track, whatever its collection (can be given more than once)"),
    )
    download.add_argument(
        "--limit", type=int, metavar="N", help=tr("only the first N tracks to do")
    )
    download.add_argument("--yes", action="store_true", help=tr("start without asking"))
    download.set_defaults(func=cmd_download)

    report = sub.add_parser("report", help=tr("tracks not found or failed"))
    report.add_argument(
        "--collection", type=int, action="append", default=[], metavar="ID",
        help=tr("only tracks of this collection"),
    )  # fmt: skip
    report.add_argument("--csv", type=Path, metavar=tr("FILE"), help=tr("save the list as csv"))
    report.set_defaults(func=cmd_report)

    retry = sub.add_parser("retry", help=tr("search tracks from the report again, from flac"))
    retry.add_argument("tracks", type=int, nargs="*", metavar=track_id)
    retry.add_argument(
        "--collection", type=int, action="append", default=[], metavar="ID",
        help=tr("every track of the report in this collection"),
    )  # fmt: skip
    retry.add_argument(
        "--download",
        action="store_true",
        help=tr("download these tracks now, after the summary"),
    )
    retry.add_argument(
        "--yes", action="store_true", help=tr("with --download: start without asking")
    )
    retry.set_defaults(func=cmd_retry)

    collection = sub.add_parser("collection", help=tr("settings of one collection"))
    collection_sub = collection.add_subparsers(
        dest="collection_command", required=True, metavar=tr("ACTION")
    )
    tags = collection_sub.add_parser(
        "tags", help=tr("genre and comment written into the files of a collection")
    )
    tags.add_argument("collection", type=int, metavar="ID")
    tags.add_argument("--genre", metavar=tr("TEXT"), help=tr('genre, "" clears it'))
    tags.add_argument("--comment", metavar=tr("TEXT"), help=tr('comment, "" clears it'))
    tags.set_defaults(func=cmd_collection_tags)

    info = collection_sub.add_parser("info", help=tr("kind, source, counts and tags"))
    info.add_argument("collection", type=int, metavar="ID")
    info.set_defaults(func=cmd_collection_info)

    folder = sub.add_parser(
        "folder", help=tr("where the library is, or the playlist file of a collection")
    )
    folder.add_argument(
        "--collection", type=int, metavar="ID", help=tr("the playlist file of this collection")
    )
    folder.set_defaults(func=cmd_folder)

    themes = sub.add_parser("themes", help=tr("list color themes, * marks the current one"))
    themes.set_defaults(func=cmd_themes)

    about = sub.add_parser("about", help=tr("versions and licenses"))
    about.add_argument(
        "--diagnostics",
        action="store_true",
        help=tr("details for someone helping you, with passwords and names cut"),
    )
    about.set_defaults(func=cmd_about)

    help_cmd = sub.add_parser(
        "help", help=tr("the user manual in the language of neonseek, chapter by chapter")
    )
    help_cmd.add_argument(
        "chapter", type=int, nargs="?", metavar="N", help=tr("show this chapter of the manual")
    )
    help_cmd.add_argument("--pdf", action="store_true", help=tr("where the pdf of the manual is"))
    help_cmd.set_defaults(func=cmd_help)

    retag = sub.add_parser("retag", help=tr("write tags again into downloaded files"))
    choose_collections(
        retag,
        tr("retag this collection, selected or not (can be given more than once)"),
        tr("retag the selected collections"),
    )
    retag.set_defaults(func=cmd_retag)

    settings = sub.add_parser("settings", help=tr("show or change settings"))
    settings.add_argument("key", nargs="?", metavar=tr("KEY"), help=", ".join(config.EDITABLE))
    settings.add_argument("value", nargs="?", metavar=tr("VALUE"))
    settings.set_defaults(func=cmd_settings)
    return parser


def _hint(exc: NeonSeekError) -> str | None:
    if isinstance(exc, csv_file.CsvMappingError) and exc.columns:
        return tr('Use --map, for example: --map "title=<column with titles>"')
    if isinstance(exc, spotify_auth.NoClientIdError):
        return tr("Run: neonseek spotify client-id <your client id>")
    if isinstance(exc, spotify_auth.NotLoggedInError):
        return tr("Run: neonseek spotify login")
    if isinstance(exc, actions.NoSoulseekLoginError):
        return tr(
            "Run: neonseek soulseek login --new (new account) or neonseek soulseek login "
            "(your own account)"
        )
    if isinstance(exc, actions.NoLibraryError):
        return tr('Run: neonseek settings library_dir "<folder>"')
    if isinstance(exc, actions.LibraryBusyError):
        return tr("Cancel the running download, or wait until it ends.")
    if isinstance(exc, actions.MissingSourceFileError):
        return tr("Run: neonseek import csv <new file> --collection {id}").format(
            id=exc.collection_id
        )
    return None


# argparse's own texts, translated through tr() (usage, errors, the help option)
ARGPARSE_TEXTS = (
    N_("usage: "),
    N_("positional arguments"),
    N_("options"),
    N_("show this help message and exit"),
    N_("%(prog)s: error: %(message)s\n"),
    N_("the following arguments are required: %s"),
    N_("unrecognized arguments: %s"),
    N_("invalid choice: %(value)r (choose from %(choices)s)"),
    N_("invalid %(type)s value: %(value)r"),
    N_("argument %(argument_name)s: %(message)s"),
    N_("not allowed with argument %s"),
    N_("one of the arguments %s is required"),
    N_("expected one argument"),
    N_("expected at least one argument"),
    N_("ambiguous option: %(option)s could match %(matches)s"),
)


def _translate_argparse() -> None:
    """argparse asks gettext for its texts. tr() answers instead, english stays english."""
    argparse._ = tr  # type: ignore[attr-defined]


def _data_dir_arg(argv: Sequence[str]) -> Path | None:
    """--data-dir before the parser exists: the settings there decide the language."""
    for index, value in enumerate(argv):
        if value == "--data-dir" and index + 1 < len(argv):
            return Path(argv[index + 1])
        if value.startswith("--data-dir="):
            return Path(value.split("=", 1)[1])
    return None


def utf8_when_redirected(*streams) -> None:
    """a console shows every character by itself. output that goes to a pipe or a file is
    utf-8 on every windows, not the ansi code page of this one, so a script reading it gets
    the polish letters and quotes right (D-260). never crash on a character either way."""
    for stream in streams:
        if not hasattr(stream, "reconfigure"):
            continue
        if stream.isatty():
            stream.reconfigure(errors="replace")
        else:
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    utf8_when_redirected(sys.stdout, sys.stderr)
    argv = list(sys.argv[1:] if argv is None else argv)
    first = actions.AppContext(_data_dir_arg(argv))
    try:
        actions.start_language(first, remember=False)
    finally:
        first.close()
    _translate_argparse()
    parser = build_parser()
    args = parser.parse_args(argv)
    ctx = actions.AppContext(args.data_dir)
    try:
        return args.func(ctx, args)
    except NeonSeekError as exc:
        print(tr("Error: {message}").format(message=exc.message), file=sys.stderr)
        hint = _hint(exc)
        if hint:
            print(hint, file=sys.stderr)
        return EXIT_ERROR
    finally:
        ctx.close()
