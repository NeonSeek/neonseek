"""about: versions of neonseek and sockseek, licenses, diagnostics to copy (PLAN.md 9)."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, QUrl, qVersion
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

import neonseek
from neonseek import actions, outcomes
from neonseek.i18n import tr
from neonseek.ui.dialogs.base import Dialog, LineView
from neonseek.ui.theme.loader import ThemeManager, logo_pixmap
from neonseek.ui.widgets import spacing
from neonseek.ui.workers import Worker

# the logo next to the name, in pixels at any text size, from the full svg
LOGO_PX = 56


def open_link(url: str) -> None:
    """the project page or the donation page in the browser. tests put a fake here."""
    QDesktopServices.openUrl(QUrl(url))


def gui_versions() -> dict[str, str]:
    import PySide6

    return {"Qt": qVersion(), "PySide6": PySide6.__version__,
            "Qt platform": QGuiApplication.platformName()}  # fmt: skip


class AboutDialog(Dialog):
    def __init__(self, ctx: actions.AppContext, theme: ThemeManager, parent=None) -> None:
        super().__init__(theme, lambda: tr("About"), parent)
        self.ctx = ctx
        self.resize_scaled(720, 560)
        self.logo = QLabel()
        self.logo.setPixmap(logo_pixmap(LOGO_PX, self.devicePixelRatioF()))
        self.logo.setFixedSize(LOGO_PX, LOGO_PX)
        title = QLabel(f"NeonSeek {neonseek.__version__}")
        title.setProperty("role", "title")
        header = QHBoxLayout()
        self.sizes.layout(header, margins=0, spacing=14)
        header.addWidget(self.logo)
        header.addWidget(title, 1)
        self.body.addLayout(header)
        text = QLabel(
            tr(
                "Downloads your track lists from Soulseek in lossless quality for rekordbox. "
                "NeonSeek runs Sockseek as a separate program and contains none of its code."
            )
        )
        text.setWordWrap(True)
        text.setProperty("role", "muted")
        self.body.addWidget(text)
        for line in (actions.app_license(), actions.source_code(), actions.qt_license()):
            label = QLabel(line)
            label.setWordWrap(True)
            label.setProperty("role", "muted")
            # the address can be selected and copied
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.body.addWidget(label)
        # the project page and the donation page open in the browser (D-265)
        self.links: dict[str, QLabel] = {}
        color = theme.theme.colors["accent.secondary"]
        for caption, url in actions.project_links():
            address = html.escape(url)
            label = QLabel(
                f'{html.escape(caption)}: <a href="{address}" style="color: {color}">{address}</a>'
            )
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setWordWrap(True)
            label.setProperty("role", "muted")
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.LinksAccessibleByMouse
                | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
            )
            label.setOpenExternalLinks(False)
            label.linkActivated.connect(lambda link: open_link(link))
            label.setToolTip(url)
            self.body.addWidget(label)
            self.links[url] = label
        self.lines = LineView(theme)
        self.body.addWidget(self.lines, 1)
        self.lines.add(tr("Parts of NeonSeek and their licenses:"))
        for name, version, license_name in actions.third_party():
            self.lines.add(f"  {name} {version}: {license_name}")
        self.lines.add(tr("Checking Sockseek..."))

        row = spacing.button_row(self.sizes)
        self.copy = QPushButton(tr("COPY DIAGNOSTICS"))
        self.copy.setToolTip(
            tr(
                "Versions, system and the end of the logs, without passwords and names, for "
                "someone helping you."
            )  # fmt: skip
        )
        self.copy.clicked.connect(self._copy)
        self.licenses = QPushButton(tr("LICENSES"))
        self.licenses.setToolTip(tr("Opens the full texts of all licenses."))
        self.licenses.clicked.connect(self._open_licenses)
        close = QPushButton(tr("CLOSE"))
        close.clicked.connect(self.accept)
        row.addWidget(self.copy)
        row.addWidget(self.licenses)
        row.addWidget(close)
        self.footer.addLayout(row)

        self.info_worker = Worker(ctx, lambda c, _: actions.sockseek_info(c), self)
        self.info_worker.succeeded.connect(self._sockseek)
        self.info_worker.failed.connect(lambda message: self.lines.add(message, outcomes.ERROR))
        self.info_worker.start()

    def _sockseek(self, info: actions.SockseekInfo) -> None:
        self.lines.add(tr("Sockseek {version}: {path}").format(version=info.version,
                                                               path=info.path))  # fmt: skip
        for line in info.source.splitlines():
            self.lines.add(line)

    def _open_licenses(self) -> None:
        path = actions.license_texts()
        if not path.is_file() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.lines.add(
                tr("Could not open {path}. Install NeonSeek again.").format(path=path),
                outcomes.ERROR,
            )

    def _copy(self) -> None:
        self.copy.setEnabled(False)
        extra = gui_versions()
        worker = Worker(self.ctx, lambda c, _: actions.diagnostics(c, extra), self)
        worker.succeeded.connect(self._copied)
        worker.failed.connect(lambda message: self.lines.add(message, outcomes.ERROR))
        worker.finished.connect(lambda: self.copy.setEnabled(True))
        worker.start()

    def _copied(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.lines.add(
            tr("Diagnostics copied. Paste them into a message to whoever helps you."),
            outcomes.DONE,
        )
