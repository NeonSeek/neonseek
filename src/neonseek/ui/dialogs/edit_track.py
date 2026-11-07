"""artist and title of one track by hand, before it is searched on soulseek (D-229). mostly for
youtube and soundcloud uploads, whose names are guessed. the cli does the same with
neonseek track edit."""

from __future__ import annotations

import json

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QLineEdit, QPushButton

from neonseek import actions, outcomes
from neonseek.errors import NeonSeekError
from neonseek.i18n import tr
from neonseek.ui.dialogs.base import Dialog, LineView
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets import spacing
from neonseek.ui.widgets.common import SectionLabel


class EditTrackDialog(Dialog):
    saved = Signal(object)  # outcomes.Line for the log

    def __init__(
        self, ctx: actions.AppContext, theme: ThemeManager, track_id: int, parent=None
    ) -> None:
        super().__init__(theme, lambda: tr("Edit artist and title"), parent)
        self.ctx = ctx
        self.track_id = track_id
        self.resize_scaled(620, 300)
        row = ctx.repo.get_track(track_id)
        artists = json.loads(row["artists_json"]) if row and row["artists_json"] else []

        hint = QLabel(
            tr(
                "NeonSeek searches Soulseek for the artist and the title. Correct them when they "
                "were guessed wrong, for example from a YouTube title. A sync keeps your names. "
                "A track that was not found is searched again."
            )
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        self.body.addWidget(hint)

        grid = QGridLayout()
        self.sizes.layout(grid, margins=0, horizontal=spacing.LABEL, vertical=spacing.ROW)
        self.artist = QLineEdit(", ".join(artists))
        self.artist.setMaxLength(300)
        self.song = QLineEdit(row["title"] if row else "")
        self.song.setMaxLength(300)
        grid.addWidget(self.texts.text(SectionLabel(""), lambda: tr("ARTIST")), 0, 0)
        grid.addWidget(self.artist, 0, 1)
        grid.addWidget(self.texts.text(SectionLabel(""), lambda: tr("TITLE")), 1, 0)
        grid.addWidget(self.song, 1, 1)
        self.body.addLayout(grid)

        # the log takes the free room, the spacer only while it is empty (D-251)
        self.lines = LineView(theme)
        self.body.addWidget(self.lines, 1)
        self.body.addStretch(0)

        buttons = spacing.button_row(self.sizes)
        self.save = QPushButton(tr("SAVE"))
        self.save.setProperty("primary", True)
        self.save.setDefault(True)
        self.save.clicked.connect(self._save)
        cancel = QPushButton(tr("CANCEL"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.save)
        buttons.addWidget(cancel)
        self.footer.addLayout(buttons)
        self.song.returnPressed.connect(self._save)
        self.artist.returnPressed.connect(self._save)

    def _save(self) -> None:
        try:
            info = actions.edit_track(self.ctx, self.track_id, self.artist.text(), self.song.text())
        except NeonSeekError as exc:
            self.lines.add(exc.message, outcomes.ERROR)
            return
        self.saved.emit(outcomes.edited_line(info))
        self.accept()
