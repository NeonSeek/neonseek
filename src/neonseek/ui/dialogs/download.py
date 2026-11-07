"""the window before every download start (PLAN.md section 8): collections, tracks, time."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from neonseek import actions, outcomes
from neonseek.i18n import tr
from neonseek.ui.dialogs.base import Dialog, LineView
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets import spacing


class ConfirmDownloadDialog(Dialog):
    def __init__(self, theme: ThemeManager, plan: actions.DownloadPlan, parent=None) -> None:
        super().__init__(theme, lambda: tr("Start the download?"), parent)
        self.resize_scaled(640, 380)
        lines = LineView(theme)
        lines.add_lines(outcomes.plan_lines(plan, gui=True))
        self.body.addWidget(lines, 1)
        row = spacing.button_row(self.sizes)
        cancel = QPushButton(tr("CANCEL"))
        cancel.clicked.connect(self.reject)
        start = QPushButton(tr("START"))
        start.setProperty("primary", True)
        start.clicked.connect(self.accept)
        start.setDefault(True)
        row.addWidget(cancel)
        row.addWidget(start)
        self.footer.addLayout(row)
        theme.glow(start)
