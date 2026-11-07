"""remove a collection: files stay, unless the user ticks the recycle bin option (D-127)."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QPushButton

from neonseek import actions, outcomes, views
from neonseek.i18n import tr
from neonseek.ui.dialogs.base import Dialog, LineView
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets import spacing


class RemoveCollectionDialog(Dialog):
    def __init__(
        self, theme: ThemeManager, plan: actions.RemovePlan, downloading: bool, parent=None
    ) -> None:
        super().__init__(theme, lambda: tr("Remove collection"), parent)
        self.resize_scaled(640, 320)
        names = ", ".join(f'"{name}"' for _, name in plan.collections)
        text = spacing.Hint(
            tr(
                "Remove {names} from NeonSeek? Its tracks and their download state stay, files "
                "in your library stay too unless you tick the option below."
            ).format(names=names),
            role="",
        )
        self.body.addWidget(text)
        # off by default, deleting is always the user's own choice
        self.delete_files = QCheckBox(outcomes.remove_choice_text(plan))
        self.delete_files.setChecked(False)
        why = None
        if downloading:
            why = views.running_reason()
        elif not plan.count:
            why = tr("No downloaded file belongs only to this collection.")
        elif plan.problem:
            why = plan.problem
        self.delete_files.setEnabled(why is None)
        self.delete_files.setToolTip(why or "")
        self.body.addWidget(self.delete_files)
        self.lines = LineView(theme)
        self.lines.add_lines(outcomes.remove_details(plan))
        if downloading:
            self.lines.add(views.running_reason(), outcomes.WARN)
        # the details take the free room, the spacer only while there are none (D-251)
        self.body.addWidget(self.lines, 1)
        self.body.addStretch(0)
        row = spacing.button_row(self.sizes)
        cancel = QPushButton(tr("CANCEL"))
        cancel.clicked.connect(self.reject)
        self.remove = QPushButton(tr("REMOVE"))
        self.remove.setProperty("primary", True)
        self.remove.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self.remove)
        self.footer.addLayout(row)

    def with_files(self) -> bool:
        return self.delete_files.isEnabled() and self.delete_files.isChecked()
