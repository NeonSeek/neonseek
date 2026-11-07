"""screenshots of the look at windows scale 100, 125 and 150 %: every step of the first run
wizard, the main window, settings, about and the license list of about. jpeg only, never
png, into a folder outside the repository (D-253).

uv run python scripts/ui_screenshots.py [--out <folder>] [--scales 100,125,150] [--lang en,pl]
    [--theme neon]

each scale runs offscreen in its own process, on a screen like the one users have at that
scale (100 and 125 % on 1366x768, 150 % on 1920x1080, without a 48 px taskbar). report.txt
next to the pictures says for every window whether it fits that screen. made up example data
in a temporary folder and the login in a keyring in memory: nothing connects to soulseek or
spotify, nothing reads the real credential manager or the data in %APPDATA%.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCREENS = {100: (1366, 768), 125: (1366, 768), 150: (1920, 1080)}
TASKBAR_PX = 48
# the frame of a windows 11 window around the part qt draws, in logical pixels
FRAME_W, FRAME_H = 2, 32
SHOTS = ("wizard-1", "wizard-1-saved", "wizard-2", "wizard-3", "wizard-4", "main", "settings",
         "about", "licenses")  # fmt: skip


def _outside_repo(path: Path) -> Path:
    path = path.resolve()
    if path == ROOT or ROOT in path.parents:
        raise SystemExit(f"the screenshots go outside the repository, not to {path}")
    return path


def _screen_config(folder: Path, scale: int) -> str:
    """an offscreen screen with the size and dpi of a real one: qt then scales like windows.
    qt splits platform options at colons, so the file goes in relative to the working
    folder."""
    width, height = SCREENS[scale]
    config = folder / f"screen-{scale}.json"
    screen = {"name": f"screen-{scale}", "x": 0, "y": 0, "width": width, "height": height,
              "logicalDpi": 96 * scale / 100, "logicalBaseDpi": 96, "dpr": 1}  # fmt: skip
    config.write_text(json.dumps({"screens": [screen]}), encoding="utf-8")
    return os.path.relpath(config).replace(os.sep, "/")


def parent(args) -> int:
    out = _outside_repo(args.out or Path(tempfile.gettempdir()) /
                        f"neonseek-ui-shots-{time.strftime('%Y%m%d-%H%M%S')}")  # fmt: skip
    out.mkdir(parents=True, exist_ok=True)
    report = []
    with tempfile.TemporaryDirectory(prefix="neonseek-screens-") as tmp:
        for scale in (int(s) for s in args.scales.split(",")):
            for language in args.lang.split(","):
                env = dict(os.environ)
                env["QT_QPA_PLATFORM"] = f"offscreen:configfile={_screen_config(Path(tmp), scale)}"
                folder = out / f"{scale}" / language
                child = [sys.executable, __file__, "--child", "--scale", str(scale), "--lang",
                         language, "--theme", args.theme, "--out", str(folder)]  # fmt: skip
                done = subprocess.run(child, env=env, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace")  # fmt: skip
                if done.returncode != 0:
                    sys.stderr.write(done.stdout + done.stderr)
                    raise SystemExit(f"scale {scale} {language} failed")
                for line in done.stdout.splitlines():
                    if line.startswith("{"):
                        report.append(json.loads(line))
    lines = ["window          scale  lang  screen     logical area  window size  minimum    "
             "fits"]  # fmt: skip
    bad = 0
    for row in report:
        bad += not row["fits"]
        lines.append(
            f"{row['shot']:15} {row['scale']:>4} %  {row['lang']:4}  {row['screen']:10} "
            f"{row['area']:13} {row['size']:12} {row['minimum']:10} "
            f"{'yes' if row['fits'] else 'NO'}"
        )
    lines.append("")
    lines.append(f"{len(report)} windows, {bad} do not fit")
    (out / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(out)
    return 1 if bad else 0


def child(args) -> int:
    sys.path.insert(0, str(ROOT / "scripts"))
    import keyring
    from manual_screenshots import (
        DEMO_USER,
        MemoryKeyring,
        _save,
        _wait,
        make_demo,
        neutralize,
        real_paths,
    )
    from PySide6.QtGui import QGuiApplication

    from neonseek import actions, i18n, outcomes
    from neonseek import secrets as secret_store
    from neonseek.ui.app import create_app
    from neonseek.ui.dialogs import base
    from neonseek.ui.theme.loader import ThemeManager

    create_app(sys.argv[:1])
    keyring.set_keyring(MemoryKeyring())
    i18n.set_language(args.lang)
    dpr = QGuiApplication.primaryScreen().devicePixelRatio()

    def area(_window=None):
        # what windows gives a window: the screen without the taskbar
        rect = QGuiApplication.primaryScreen().availableGeometry()
        rect.setHeight(rect.height() - round(TASKBAR_PX / dpr))
        return rect

    def area_without_frame(_window=None):
        # offscreen windows have no frame: fit them as if windows had added its frame
        return area().adjusted(0, 0, -FRAME_W, -FRAME_H)

    base.available_area = area_without_frame
    screen = "x".join(str(v) for v in SCREENS[args.scale])
    real_info = actions.sockseek_info

    def sockseek_info(ctx):
        # the path of an installed copy, not the folder of this checkout
        info = real_info(ctx)
        return actions.SockseekInfo(r"C:\Program Files\NeonSeek\vendor\sockseek\sockseek.exe",
                                    info.version, info.source)  # fmt: skip

    actions.sockseek_info = sockseek_info

    def record(name: str, window) -> None:
        free = area()
        fits = (window.minimumWidth() + FRAME_W <= free.width()
                and window.minimumHeight() + FRAME_H <= free.height()
                and window.width() + FRAME_W <= free.width()
                and window.height() + FRAME_H <= free.height())  # fmt: skip
        row = {"shot": name, "scale": args.scale, "lang": args.lang, "screen": screen,
               "area": f"{free.width()}x{free.height()}",
               "size": f"{window.width()}x{window.height()}",
               "minimum": f"{window.minimumWidth()}x{window.minimumHeight()}",
               "fits": fits}  # fmt: skip
        print(json.dumps(row), flush=True)

    def shot(name: str, window, check: bool = True) -> None:
        window.show()
        _wait(0.5)
        if check:
            base.fit_to_screen(window)
            _wait(0.3)
            record(name, window)
        neutralize(window, real)
        _wait(0.2)
        _save(window.grab(), args.out / f"{name}.jpg")

    with tempfile.TemporaryDirectory(prefix="neonseek-ui-") as tmp:
        folder = Path(tmp)
        ctx, collection_id, _ = make_demo(folder, args.lang)
        real = real_paths(folder, ctx.data_dir)
        theme = ThemeManager(ctx.data_dir)
        theme.apply(args.theme, 100)
        theme.set_language(args.lang)
        from neonseek.ui.dialogs.wizard import SetupWizard
        from neonseek.ui.main_window import MainWindow

        # the first start: no account saved yet
        secret_store.delete_soulseek_login()
        wizard = SetupWizard(ctx, theme)
        wizard.show()
        for index, page in enumerate(wizard.pageIds(), start=1):
            wizard.setCurrentId(page)
            shot(f"wizard-{index}", wizard)
            if index == 1:
                # after a new account was made: the log and an active NEXT
                login = secret_store.SoulseekLogin(DEMO_USER, "not-a-real-password")
                secret_store.set_soulseek_login(login)
                form = wizard.soulseek.form
                form.result.add(actions.soulseek_check_started_message())
                form.result.add_lines(
                    outcomes.soulseek_saved_lines(actions.SoulseekLoginOutcome(login), True)
                )
                form.refresh()
                form.state_changed.emit()
                shot("wizard-1-saved", wizard)
        wizard.done(0)

        window = MainWindow(ctx, theme, list_at_start=False)
        window.resize(1320, 840)
        window.show()
        window.panel.open_collection(collection_id)
        _wait(0.6)
        window.log._lines.clear()
        window.log.console.clear()
        window.log.add(f"NeonSeek {__import__('neonseek').__version__}")
        window.table.selectRow(1)
        shot("main", window)

        from neonseek.ui.dialogs.about import AboutDialog
        from neonseek.ui.dialogs.settings import SettingsDialog

        dialog = SettingsDialog(ctx, theme, window)
        shot("settings", dialog)
        dialog.done(0)
        about = AboutDialog(ctx, theme, window)
        about.show()
        end = time.monotonic() + 10
        while about.info_worker.isRunning() and time.monotonic() < end:
            _wait(0.1)
        shot("about", about)
        # the whole license list at once: taller than the screen, so not checked for fitting
        bar = about.lines.verticalScrollBar()
        for _ in range(40):
            if bar.maximum() == 0:
                break
            about.resize(about.width(), about.height() + 80)
            _wait(0.05)
        shot("licenses", about, check=False)
        about.done(0)
        window.close()
        _wait(0.2)
        ctx.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, help="default: a new folder in %%TEMP%%")
    parser.add_argument("--scales", default="100,125,150")
    parser.add_argument("--scale", type=int, default=100, help=argparse.SUPPRESS)
    parser.add_argument("--lang", default="en,pl")
    parser.add_argument("--theme", default="neon")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return child(args)
    return parent(args)


if __name__ == "__main__":
    raise SystemExit(main())
