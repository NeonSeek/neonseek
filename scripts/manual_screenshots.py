"""screenshots for the user manual, in every language, as jpeg (D-164).

uv run python scripts/manual_screenshots.py [--lang pl] [--scale 150] [--out <folder>]
    [--only main,wizard,settings]

builds made up example data in a temporary folder (no real account, no data of the owner) and
renders the window and dialogs offscreen. nothing connects to soulseek or spotify, the login
lives in a keyring in memory. the default output is docs/manual/images/<language>/.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gui_screenshot import _platform  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", _platform())

import keyring  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QLabel,
    QLineEdit,
    QWidget,
)

from neonseek import actions, i18n, manual, secrets  # noqa: E402
from neonseek.ui.app import create_app  # noqa: E402
from neonseek.ui.theme.loader import ThemeManager  # noqa: E402

JPEG_QUALITY = 86
SHOTS = ("main", "menu", "wizard", "wizard-spotify", "settings", "add", "csv", "confirm", "report")
# made up paths, the same in every language: no real folder of anyone shows (D-181)
LIBRARY = r"D:\Muzyka\Biblioteka"
SHOWN_LISTS = r"D:\Muzyka\Listy"
SHOWN_DATA = r"C:\Users\Ty\AppData\Roaming\NeonSeek"
SHOWN_HOME = r"C:\Users\Ty"
DEMO_USER = "dj_example"

# made up artists and titles, length in m:ss
WAREHOUSE = [
    ("Nightline", "Concrete Pulse", "Concrete Pulse EP", "6:12"),
    ("Mara Velde", "Low Orbit", "Low Orbit", "7:03"),
    ("Kestrel & Fade", "Static Bloom", "Bloom", "5:48"),
    ("Nightline", "Afterhours Signal", "Concrete Pulse EP", "6:40"),
    ("Juno Rask", "Tunnel Vision (Extended Mix)", "Tunnel Vision", "7:21"),
    ("Polar Unit", "Glass Floor", "Glass Floor", "6:05"),
    ("Iris Kwan", "Northbound", "Northbound EP", "5:57"),
    ("Mara Velde", "Second Sun", "Low Orbit", "6:33"),
    ("Dovetail", "Rewind Theory", "Theory", "4:52"),
    ("Kestrel & Fade", "Hollow Lights", "Bloom", "6:18"),
    ("Polar Unit", "Signal Lost", "Glass Floor", "7:10"),
    ("Juno Rask", "Red Line", "Tunnel Vision", "5:44"),
]
# state of each row above: status, stage, format, attempts
STATES = [
    ("done", "flac", ("flac", 44100, 16), 0),
    ("done", "flac", ("flac", 96000, 24), 0),
    ("done", "fallback", ("aiff", 44100, 16), 0),
    ("done", "flac", ("flac", 44100, 24), 0),
    ("pending", "flac", None, 0),
    ("not_found", "flac", None, 0),
    ("not_found", "fallback", None, 0),
    ("failed", "flac", None, 1),
    ("excluded", "flac", None, 0),
    ("done", "fallback", ("wav", 44100, 16), 0),
    ("pending", "flac", None, 0),
    ("failed", "fallback", None, 3),
]
OTHER = {
    "Late night techno": [
        ("Iris Kwan", "Deep Water", "", "6:44"),
        ("Dovetail", "Slow Engine", "", "7:30"),
    ],
    "Summer 2026": [("Nightline", "Coastline", "", "5:20")],
}


class MemoryKeyring(KeyringBackend):
    """the screenshots never read the real credential manager."""

    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[(service, username)] = password

    def delete_password(self, service, username):
        self.values.pop((service, username), None)


class MemoryTokenStore:
    def load(self):
        return None

    def save(self, refresh_token: str) -> None:
        pass

    def clear(self) -> None:
        pass


def _csv(path: Path, rows) -> Path:
    lines = ["Artist,Title,Album,Length"]
    lines += [",".join(f'"{v}"' for v in row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def make_demo(folder: Path, language: str) -> tuple[actions.AppContext, int, Path]:
    """example data: three csv collections, the first with every state."""
    ctx = actions.AppContext(folder / "data")
    ctx.token_store = MemoryTokenStore()
    for key, value in (("library_dir", LIBRARY), ("language", language)):
        actions.change_setting(ctx, key, value)
    main_csv = _csv(folder / "Warehouse set.csv", WAREHOUSE)
    outcome = actions.import_csv(ctx, main_csv)
    for name, rows in OTHER.items():
        actions.import_csv(ctx, _csv(folder / f"{name}.csv", rows))
    db = ctx.repo.conn
    track_ids = [
        row[0]
        for row in db.execute(
            "SELECT track_id FROM collection_tracks WHERE collection_id = ? ORDER BY position",
            (outcome.collection_id,),
        )
    ]
    for track_id, (artist, title, _, _), (status, stage, fmt, attempts) in zip(
        track_ids, WAREHOUSE, STATES, strict=True
    ):
        values = {"status": status, "stage": stage, "attempts": attempts}
        if status == "excluded":
            values["excluded_from"] = "pending"
        if fmt is not None:
            kind, rate, depth = fmt
            values.update(
                format=kind,
                sample_rate=rate,
                bit_depth=depth,
                file_path=rf"{LIBRARY}\tracks\{artist} - {title}.{kind}",
                length_verified=1,
                source_user="someone",
            )
        if status in ("not_found", "failed"):
            reason = "NoSearchResults" if status == "not_found" else "AllDownloadsFailed"
            values["last_error"] = reason
        sets = ", ".join(f"{key} = ?" for key in values)
        db.execute(f"UPDATE downloads SET {sets} WHERE track_id = ?", (*values.values(), track_id))
    db.execute("UPDATE collections SET selected = 1 WHERE id = ?", (outcome.collection_id,))
    db.commit()
    return ctx, outcome.collection_id, main_csv


def neutral_text(text: str, real: list[tuple[str, str]]) -> str:
    """the text with every real path replaced by its made up one, in both slash styles."""
    for path, shown in real:
        for form in {path, path.replace("\\", "/")}:
            text = text.replace(form, shown)
    return text


def real_paths(folder: Path, data_dir: Path) -> list[tuple[str, str]]:
    """the temporary folders of this run and the home folder, longest first."""
    return [
        (str(data_dir), SHOWN_DATA),
        (str(folder), SHOWN_LISTS),
        (str(Path.home()), SHOWN_HOME),
    ]


def neutralize(widget: QWidget, real: list[tuple[str, str]]) -> None:
    """every visible text of the widget without a real path, before it is captured."""
    for child in [widget, *widget.findChildren(QWidget)]:
        for kind in (QLabel, QLineEdit, QAbstractButton):
            if isinstance(child, kind) and child.text():
                fixed = neutral_text(child.text(), real)
                if fixed != child.text():
                    child.setText(fixed)


def _wait(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.03)


def _save(pixmap: QPixmap, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not pixmap.save(str(path), "JPG", JPEG_QUALITY):
        raise SystemExit(f"could not save {path}")
    print(path)


REAL: list[tuple[str, str]] = []


def _shot(widget: QWidget, path: Path) -> None:
    widget.show()
    _wait(0.6)
    neutralize(widget, REAL)
    # a shorter made up path may take fewer lines, the layout follows before the capture
    _wait(0.2)
    _save(widget.grab(), path)


def take(language: str, scale: int, out: Path, only: set[str], theme_key: str) -> None:
    i18n.set_language(language)
    with tempfile.TemporaryDirectory(prefix="neonseek-manual-") as tmp:
        folder = Path(tmp)
        ctx, collection_id, main_csv = make_demo(folder, language)
        REAL[:] = real_paths(folder, ctx.data_dir)
        theme = ThemeManager(ctx.data_dir)
        theme.apply(theme_key, scale)
        theme.set_language(language)
        from neonseek.ui.main_window import MainWindow

        if only & {"wizard", "wizard-spotify"}:
            # the first start: nothing saved yet, also after the other language
            from neonseek.ui.dialogs.wizard import SetupWizard

            secrets.delete_soulseek_login()

            wizard = SetupWizard(ctx, theme)
            if "wizard" in only:
                _shot(wizard, out / "wizard.jpg")
            if "wizard-spotify" in only:
                wizard.show()
                wizard.setCurrentId(wizard.pageIds()[2])
                _shot(wizard, out / "wizard-spotify.jpg")
            wizard.done(0)

        secrets.set_soulseek_login(secrets.SoulseekLogin(DEMO_USER, "not-a-real-password"))
        window = MainWindow(ctx, theme, list_at_start=False)
        window.resize(theme.px(1320), theme.px(840))
        window.show()
        window.panel.open_collection(collection_id)
        _wait(0.8)
        # the log shows what a user sees after the import, not the temporary folder
        window.log._lines.clear()
        window.log.console.clear()
        window.log.add(f"NeonSeek {__import__('neonseek').__version__}")
        window.log.add(i18n.tr("Music library: {path}").format(path=LIBRARY))
        window.table.selectRow(1)
        _wait(0.3)
        neutralize(window, REAL)
        if "main" in only:
            _save(window.grab(), out / "main.jpg")
        if "menu" in only:
            table = window.table
            table.clearSelection()
            model = table.selectionModel()
            for row in (5, 7):
                model.select(table.proxy.index(row, 0),
                             model.SelectionFlag.Select | model.SelectionFlag.Rows)  # fmt: skip
            menu = window.track_menu(table.selected_track_ids())
            rect = table.visualRect(table.proxy.index(7, 3))
            at = table.viewport().mapTo(window, rect.center() + QPoint(0, rect.height() // 2))
            menu.popup(window.mapToGlobal(at))
            _wait(0.5)
            shot = window.grab()
            painter = QPainter(shot)
            painter.drawPixmap(window.mapFromGlobal(menu.pos()), menu.grab())
            painter.end()
            menu.close()
            _save(shot, out / "menu.jpg")
        if "settings" in only:
            from neonseek.ui.dialogs.settings import SettingsDialog

            dialog = SettingsDialog(ctx, theme, window)
            _shot(dialog, out / "settings.jpg")
            dialog.done(0)
        if "settings-page" in only:
            # for checking the layout, not in the manual: every section of settings at once
            from PySide6.QtWidgets import QScrollArea

            from neonseek.ui.dialogs.settings import PAGE_NAME, SettingsDialog

            dialog = SettingsDialog(ctx, theme, window)
            dialog.show()
            _wait(0.5)
            page = dialog.findChild(QScrollArea, PAGE_NAME).widget()
            neutralize(page, REAL)
            _wait(0.2)
            _save(page.grab(), out / "settings-page.jpg")
            dialog.done(0)
        if "add" in only:
            from neonseek.ui.dialogs.add_sources import AddSourcesDialog

            dialog = AddSourcesDialog(ctx, theme, "", window)
            _shot(dialog, out / "add.jpg")
            dialog.done(0)
        if "csv" in only:
            from neonseek.ui.dialogs.csv_import import CsvImportDialog

            dialog = CsvImportDialog(ctx, theme, main_csv, None, window)
            dialog.show()
            _wait(1.5)
            _shot(dialog, out / "csv.jpg")
            dialog.done(0)
        if "confirm" in only:
            from neonseek.ui.dialogs.download import ConfirmDownloadDialog

            with actions.DownloadJob(ctx, [collection_id]) as job:
                dialog = ConfirmDownloadDialog(theme, job.plan, window)
                _shot(dialog, out / "confirm.jpg")
                dialog.done(0)
        if "report" in only:
            window.open_report()
            _wait(0.4)
            _shot(window.report_dialog, out / "report.jpg")
            window.report_dialog.done(0)
        window.close()
        _wait(0.2)
        ctx.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", action="append", choices=list(i18n.LANGUAGES))
    parser.add_argument("--scale", type=int, default=100, help="text size in percent")
    parser.add_argument("--theme", default="neon")
    parser.add_argument("--out", type=Path, help="default: docs/manual/images/<language>")
    parser.add_argument(
        "--only", help=f"some of {','.join(SHOTS)}, or settings-page to check the layout"
    )
    args = parser.parse_args()
    only = set(args.only.split(",")) if args.only else set(SHOTS)
    create_app(sys.argv[:1])
    keyring.set_keyring(MemoryKeyring())
    for language in args.lang or list(i18n.LANGUAGES):
        out = args.out or manual.manual_dir() / "images" / language
        take(language, args.scale, out, only, args.theme)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
