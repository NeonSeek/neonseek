"""starts the window. the gui is a thin layer over neonseek.actions and neonseek.views."""

from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

import neonseek
from neonseek import actions, applog
from neonseek.errors import NeonSeekError
from neonseek.i18n import tr
from neonseek.ui import wheel
from neonseek.ui.theme.loader import FONT_FAMILY, FONT_PX, ThemeManager, app_icon, load_fonts

_LOG: applog.AppLog | None = None


def _excepthook(kind, value, tb) -> None:
    # a bug in a slot must not close the window without a word
    text = "".join(traceback.format_exception(kind, value, tb))
    sys.stderr.write(text)
    if _LOG is not None:
        _LOG.write(text, "error")
    if QApplication.instance() is not None:
        QMessageBox.critical(
            None,
            "NeonSeek",
            tr(
                "Something went wrong inside NeonSeek. Your data is safe. In About, press COPY "
                "DIAGNOSTICS and send them to whoever helps you.\n\n{error}"
            ).format(error=f"{kind.__name__}: {value}"),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="neonseek-gui")
    parser.add_argument("--version", action="version", version=neonseek.__version__)
    parser.add_argument(
        "--data-dir", type=Path, help="use this folder instead of %%APPDATA%%\\NeonSeek"
    )
    return parser


def create_app(argv: Sequence[str]) -> QApplication:
    app = QApplication.instance() or QApplication(list(argv))
    app.setApplicationName("NeonSeek")
    app.setApplicationVersion(neonseek.__version__)
    # every window and the taskbar button, the same logo as the exe (D-247)
    app.setWindowIcon(app_icon())
    load_fonts()
    wheel.install(app)
    font = QFont(FONT_FAMILY)
    font.setPixelSize(FONT_PX)
    app.setFont(font)
    return app


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    args = build_parser().parse_args(argv[1:])
    app = create_app(argv)
    global _LOG
    sys.excepthook = _excepthook
    ctx = actions.AppContext(args.data_dir)
    _LOG = applog.AppLog(ctx.data_dir)
    theme = ThemeManager(ctx.data_dir, lambda text: _LOG.write(text, "warn"))
    # the language of the settings, at the first start the one of windows (D-163)
    theme.set_language(actions.start_language(ctx, remember=True))
    try:
        settings = ctx.settings()
        theme.apply(settings.theme, settings.ui_scale)
        # opens and migrates the database before the window, a broken one stops here
        ctx.repo  # noqa: B018
        from neonseek.ui.main_window import MainWindow

        window = MainWindow(ctx, theme, _LOG)
    except NeonSeekError as exc:
        theme.apply()
        QMessageBox.critical(None, "NeonSeek", exc.message)
        ctx.close()
        return 1
    window.show()
    # a small screen or a big windows scale: the whole window starts on the screen (D-252)
    from neonseek.ui.dialogs.base import fit_to_screen

    QTimer.singleShot(0, window, lambda: fit_to_screen(window))
    if actions.needs_setup(ctx):
        from neonseek.ui.dialogs.wizard import SetupWizard

        # opened inside the one event loop: when windows asks the app to close (an installer
        # updating it, logging off), quit() ends the app also while the wizard is open (D-225)
        wizard = SetupWizard(ctx, theme, window)
        wizard.finished.connect(lambda _result: _after_wizard(window, wizard))
        wizard.open()
    return app.exec()


def _after_wizard(window, wizard) -> None:
    wizard.deleteLater()
    window.refresh_collections()
    window.reload_tracks()


if __name__ == "__main__":
    raise SystemExit(main())
