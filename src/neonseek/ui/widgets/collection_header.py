"""top of the main panel: cover and name of the open collection, stats, actions and tags.

on a low window the header shrinks to one line: name, stats and buttons, without the cover
and the tags (D-258). the chevron on the right switches between the two by hand.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QWidget,
)

from neonseek import actions, views
from neonseek.db.repo import CollectionSummary
from neonseek.i18n import tr
from neonseek.ui.covers import CoverLoader
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets import spacing
from neonseek.ui.widgets.collections_panel import kind_icon
from neonseek.ui.widgets.common import (
    ElidedLabel,
    ElidedSectionLabel,
    LiveTexts,
    SectionLabel,
    Thumb,
)

# pixel sizes at 100 %, scaled with the text size setting
COVER = 96
# genre and comment stay readable on a wide window instead of filling it (D-118)
TAG_FIELD_MAX = 460
# narrow enough that the room around them (D-194) still fits the window at 150 %
TAG_FIELD_MIN = 140
# room between the lines of the header, and between the parts of the one line header
BODY_GAP = 6
BODY_GAP_LINE = 14
# fixed room above the buttons, not scaled
BUTTONS_GAP = 4


class CollectionHeader(QFrame):
    tags_edited = Signal(str, str)  # genre, comment
    # the chevron was clicked: true for the one line header
    compact_chosen = Signal(bool)

    def __init__(self, theme: ThemeManager, covers: CoverLoader, parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("role", "panel")
        self.theme = theme
        self.summary: CollectionSummary | None = None
        self.library_rows: list[views.TrackRow] | None = None
        self.compact = False
        # what is shown: a collection or the library, and its buttons and tags
        self._has = False
        self._actions = False
        texts = LiveTexts(theme, self)
        layout = QHBoxLayout(self)
        self.outer = layout
        self.cover = Thumb(COVER, theme, covers)
        layout.addWidget(self.cover)

        # top to bottom, or one line in the compact header
        body = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.body = body
        self.kind = ElidedSectionLabel("")
        self.name = ElidedLabel()
        self.name.setProperty("role", "title")
        self.stats = ElidedLabel()
        self.stats.setProperty("role", "muted")
        body.addWidget(self.kind)
        body.addWidget(self.name)
        body.addWidget(self.stats)

        buttons = QHBoxLayout()
        self.buttons = buttons
        self.sync = texts.text(QPushButton(), lambda: tr("SYNC"))
        texts.tip(
            self.sync, lambda: tr("Read the tracks of this collection again from its source.")
        )
        self.download = texts.text(QPushButton(), lambda: tr("DOWNLOAD"))
        self.download.setProperty("primary", True)
        texts.tip(
            self.download,
            lambda: tr("Downloads the missing tracks of this collection only, checkbox or not."),
        )
        self.open_folder = texts.text(QPushButton(), lambda: tr("OPEN FOLDER"))
        texts.tip(
            self.open_folder,
            lambda: tr("Shows the playlist file of this collection in Explorer."),
        )
        for button, icon in ((self.sync, "sync"), (self.download, "download"),
                             (self.open_folder, "folder")):  # fmt: skip
            button.setProperty("icon_name", icon)
            buttons.addWidget(button)
        buttons.addStretch(1)
        body.addSpacing(BUTTONS_GAP)
        body.addLayout(buttons)

        self.tags_box = QWidget()
        # inside the panel, so it paints the panel color
        self.tags_box.setProperty("role", "surface")
        tags = QGridLayout(self.tags_box)
        self.tags = tags
        self.genre = texts.placeholder(QLineEdit(), lambda: tr("empty: nothing is written"))
        self.comment = texts.placeholder(QLineEdit(), lambda: tr("empty: nothing is written"))
        for field in (self.genre, self.comment):
            field.editingFinished.connect(self._edited)
        self.write_tags = texts.text(QPushButton(), lambda: tr("WRITE TAGS"))
        self.write_tags.setProperty("icon_name", "tag")
        texts.tip(
            self.write_tags,
            lambda: tr(
                "Writes the tags again into the downloaded files of this collection, without "
                "downloading anything."
            ),
        )
        # the labels in one column, in the middle of the height of their fields
        tags.addWidget(texts.text(SectionLabel(""), lambda: tr("GENRE")), 0, 0)
        tags.addWidget(self.genre, 0, 1)
        tags.addWidget(texts.text(SectionLabel(""), lambda: tr("COMMENT")), 1, 0)
        tags.addWidget(self.comment, 1, 1)
        # in the row of the comment and as tall as the fields, not between the two rows
        tags.addWidget(self.write_tags, 1, 2)
        # the fields grow first, up to their limit, then the empty column takes the rest
        tags.setColumnStretch(1, 4)
        tags.setColumnStretch(3, 1)
        body.addWidget(self.tags_box)
        layout.addLayout(body, 1)

        self.empty = texts.text(
            QLabel(), lambda: tr("Add a collection with + ADD, or choose one on the left.")
        )
        self.empty.setProperty("role", "muted")
        layout.addWidget(self.empty)
        self.toggle = QToolButton()
        self.toggle.setProperty("flat", True)
        texts.tip(self.toggle, lambda: tr("Show or hide the cover, genre and comment"))
        self.toggle.clicked.connect(self._toggle_clicked)
        layout.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignTop)
        theme.changed.connect(self._paint_icons)
        theme.changed.connect(self._rescale)
        self._paint_icons()
        self._rescale()
        self.show_collection(None, [])

    def _rescale(self) -> None:
        px = self.theme.px
        # room around and between the parts, labels apart from their fields (D-194)
        self.outer.setContentsMargins(*self._margins())
        self.outer.setSpacing(px(18))
        self.body.setSpacing(px(BODY_GAP_LINE if self.compact else BODY_GAP))
        self.buttons.setSpacing(px(spacing.BUTTONS))
        self.tags.setContentsMargins(0, px(8), 0, 0)
        self.tags.setHorizontalSpacing(px(spacing.LABEL))
        self.tags.setVerticalSpacing(px(8))
        for field in (self.genre, self.comment):
            field.setMaximumWidth(px(TAG_FIELD_MAX))
            field.setMinimumWidth(px(TAG_FIELD_MIN))
        self._match_heights()

    def _match_heights(self) -> None:
        self.write_tags.setFixedHeight(self.comment.sizeHint().height())

    def showEvent(self, event) -> None:  # noqa: N802 - qt name
        super().showEvent(event)
        # the style sheet sets the field height when the window is first shown
        self._match_heights()

    def _margins(self, compact: bool | None = None) -> tuple[int, int, int, int]:
        px = self.theme.px
        if self.compact if compact is None else compact:
            return px(16), px(8), px(12), px(8)
        return px(16), px(16), px(12), px(16)

    def _paint_icons(self) -> None:
        for button in (self.sync, self.download, self.open_folder, self.write_tags):
            token = "text.on_accent" if button.property("primary") else "text.primary"
            button.setIcon(self.theme.icon(button.property("icon_name"), token))
        # the header opens downwards
        name = "chevron-down" if self.compact else "chevron-up"
        self.toggle.setIcon(self.theme.icon(name, "text.muted"))

    def _visible(self, has: bool, actions: bool) -> None:
        self._has, self._actions = has, actions
        full = not self.compact
        for widget in (self.cover, self.kind):
            widget.setVisible(has and full)
        for widget in (self.name, self.stats, self.toggle):
            widget.setVisible(has)
        for widget in (self.download, self.open_folder):
            widget.setVisible(actions)
        self.tags_box.setVisible(actions and full)
        self.empty.setVisible(not has)

    # compact header (D-258)

    def _toggle_clicked(self) -> None:
        self.set_compact(not self.compact)
        self.compact_chosen.emit(self.compact)

    def set_compact(self, compact: bool) -> None:
        if compact == self.compact:
            return
        self.compact = compact
        body = self.body
        body.setDirection(
            QBoxLayout.Direction.LeftToRight if compact else QBoxLayout.Direction.TopToBottom
        )
        # in one line the name and the stats share the width left by the buttons
        for widget in (self.name, self.stats):
            body.setStretchFactor(widget, 1 if compact else 0)
        self.outer.setAlignment(
            self.toggle,
            Qt.AlignmentFlag.AlignVCenter if compact else Qt.AlignmentFlag.AlignTop,
        )
        self._visible(self._has, self._actions)
        self._rescale()
        self._paint_icons()

    def height_in(self, compact: bool) -> int:
        """the height the header asks for in either form, without switching to it: the
        window chooses the form by the room left for the tracks."""
        _, top, _, bottom = self._margins(compact)
        frame = 2 * self.frameWidth()
        if not self._has:
            return top + bottom + frame + self.empty.sizeHint().height()
        buttons = self.buttons.sizeHint().height() if self._actions else 0
        if compact:
            parts = [self.name, self.stats, self.toggle]
            line = max([buttons] + [widget.sizeHint().height() for widget in parts])
            return top + bottom + frame + line
        lines = [self.kind, self.name, self.stats]
        body = sum(widget.sizeHint().height() for widget in lines)
        # the fixed gap before the buttons takes no spacing of its own
        body += BUTTONS_GAP
        gaps = len(lines) - 1
        if self._actions:
            body += buttons + self.tags_box.sizeHint().height()
            gaps += 2
        body += gaps * self.theme.px(BODY_GAP)
        # the cover has a fixed size and no size hint
        side = max(self.cover.minimumHeight(), self.toggle.sizeHint().height())
        return top + bottom + frame + max(body, side)

    def show_collection(self, summary: CollectionSummary | None, rows: list[views.TrackRow]):
        self.summary = summary
        self._visible(summary is not None, summary is not None)
        if summary is None:
            self.sync.setVisible(False)
            return
        self.cover.set_cover(summary.image_url, kind_icon(summary.kind))
        self.kind.setText(
            f"{views.collection_kind_label(summary.kind, summary.origin).upper()}"
            f"  /  {views.synced_label(summary).upper()}"
        )
        self.name.setText(summary.name)
        self.stats.setText(views.stats_text(rows) if rows else views.empty_hint(summary))
        self.sync.setVisible(views.is_spotify(summary.kind) or views.is_web(summary.kind))
        for field, value in ((self.genre, summary.genre), (self.comment, summary.comment)):
            if not field.hasFocus():
                field.setText(value or "")

    def show_library(self, rows: list[views.TrackRow]) -> None:
        self.summary = None
        self._visible(True, False)
        self.sync.setVisible(False)
        self.cover.set_cover(None, "search")
        self.kind.setText(tr("SEARCH"))
        self.name.setText(tr("Whole library"))
        self.stats.setText(views.stats_text(rows))

    def set_tags_pending(self, pending: bool) -> None:
        # changed tags are only in neonseek until WRITE TAGS (PLAN.md section 9)
        self.write_tags.setProperty("primary", pending)
        self.write_tags.style().unpolish(self.write_tags)
        self.write_tags.style().polish(self.write_tags)
        self._paint_icons()

    def _edited(self) -> None:
        if self.summary is None:
            return
        genre, comment = self.genre.text(), self.comment.text()
        if (actions.clean_tag(genre), actions.clean_tag(comment)) != (
            self.summary.genre,
            self.summary.comment,
        ):
            self.tags_edited.emit(genre, comment)
