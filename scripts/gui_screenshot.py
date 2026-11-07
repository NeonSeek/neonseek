"""renders the main window or a dialog offscreen and saves it as png. a developer tool for
checking themes and layout without a screen. never logs in anywhere, but covers may be
downloaded and dialogs that check sockseek start it with --version.

uv run python scripts/gui_screenshot.py --data-dir <dir> --out shot.png [--collection 12]
    [--theme neon] [--scale 100] [--lang pl] [--size 1320x840] [--wait 4] [--select-row 2]
    [--dialog settings|about|report|wizard|add|confirm|csv=<file>|properties=<id,id>|remove=<id>]
    [--menu 2,3]   (the track menu on these rows, drawn over the window)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def _platform() -> str:
    """offscreen with a 2560x1440 screen, so dialogs keep their size (they fit the screen). qt
    splits its options at colons, so the file goes in relative to the working folder."""
    try:
        config = os.path.relpath(Path(__file__).with_name("offscreen-screen.json"))
    except ValueError:  # another drive than the working folder
        return "offscreen"
    return f"offscreen:configfile={config.replace(os.sep, '/')}"


os.environ.setdefault("QT_QPA_PLATFORM", _platform())

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from neonseek import actions  # noqa: E402
from neonseek.ui.app import create_app  # noqa: E402
from neonseek.ui.theme.loader import ThemeManager  # noqa: E402


def _dialog(name: str, window, ctx, theme):
    if name == "settings":
        from neonseek.ui.dialogs.settings import SettingsDialog

        return SettingsDialog(ctx, theme, window)
    if name == "about":
        from neonseek.ui.dialogs.about import AboutDialog

        return AboutDialog(ctx, theme, window)
    if name == "report":
        from neonseek.ui.dialogs.report import ReportDialog

        return ReportDialog(ctx, theme, window)
    if name == "wizard":
        from neonseek.ui.dialogs.wizard import SetupWizard

        return SetupWizard(ctx, theme, window)
    if name == "add":
        from neonseek.ui.dialogs.add_sources import AddSourcesDialog

        return AddSourcesDialog(ctx, theme, "", window)
    if name.startswith("remove="):
        return window.remove_dialog(actions.remove_plan(ctx, [int(name.split("=", 1)[1])]))
    if name.startswith("properties="):
        from neonseek.ui.dialogs.properties import PropertiesDialog

        ids = [int(part) for part in name.split("=", 1)[1].split(",")]
        return PropertiesDialog(ctx, theme, ids, window)
    if name.startswith("csv="):
        from neonseek.ui.dialogs.csv_import import CsvImportDialog

        return CsvImportDialog(ctx, theme, Path(name[4:]), None, window)
    if name == "confirm":
        from neonseek.actions import DownloadPlan
        from neonseek.ui.dialogs.download import ConfirmDownloadDialog

        plan = DownloadPlan([(12, "Late night techno")], [1, 2], 2, 0, 15, 21, [], 220)
        return ConfirmDownloadDialog(theme, plan, window)
    raise SystemExit(f"unknown dialog {name}")


def _wait(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.05)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--collection", type=int)
    parser.add_argument("--theme", default=None)
    parser.add_argument("--size", default="1320x840")
    parser.add_argument("--wait", type=float, default=4.0, help="seconds for covers to load")
    parser.add_argument("--dialog", help="render this dialog instead of the window")
    parser.add_argument("--scale", type=int, default=None, help="text size in percent")
    parser.add_argument("--lang", help="language of the window, without saving it")
    parser.add_argument("--select-row", type=int, help="select this row of the track table")
    parser.add_argument("--menu", help="rows of the track table for the track menu, like 2,3")
    args = parser.parse_args()

    create_app(sys.argv[:1])
    ctx = actions.AppContext(args.data_dir)
    theme = ThemeManager(ctx.data_dir)
    theme.apply(args.theme, args.scale)
    theme.set_language(args.lang or actions.start_language(ctx, remember=False))
    from neonseek.ui.main_window import MainWindow

    # no spotify at start: the tool never asks anything on the internet itself
    window = MainWindow(ctx, theme, list_at_start=False)
    width, height = (int(v) for v in args.size.lower().split("x"))
    window.resize(width, height)
    window.show()
    if args.collection is not None:
        window.panel.open_collection(args.collection)
    _wait(args.wait)
    if args.select_row is not None:
        window.table.selectRow(args.select_row)
        _wait(0.3)
    target = window
    if args.menu:
        rows = [int(part) for part in args.menu.split(",")]
        window.table.clearSelection()
        for row in rows:
            window.table.selectionModel().select(
                window.table.proxy.index(row, 0),
                window.table.selectionModel().SelectionFlag.Select
                | window.table.selectionModel().SelectionFlag.Rows,
            )
        menu = window.track_menu(window.table.selected_track_ids())
        rect = window.table.visualRect(window.table.proxy.index(rows[-1], 3))
        at = window.table.viewport().mapTo(window, rect.center() + QPoint(0, rect.height() // 2))
        menu.popup(window.mapToGlobal(at))
        _wait(0.5)
        shot = window.grab()
        painter = QPainter(shot)
        painter.drawPixmap(window.mapFromGlobal(menu.pos()), menu.grab())
        painter.end()
        shot.save(str(args.out))
        print(args.out)
        menu.close()
        window.close()
        return 0
    if args.dialog:
        target = _dialog(args.dialog, window, ctx, theme)
        target.show()
        _wait(min(args.wait, 3.0))
    target.grab().save(str(args.out))
    print(args.out)
    if target is not window:
        target.done(0)
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
