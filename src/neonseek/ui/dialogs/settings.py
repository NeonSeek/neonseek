"""settings: accounts, library, format policy, attempts, idle limit, tags, theme, sockseek."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from neonseek import actions, config, i18n, manual, outcomes, views
from neonseek import library as library_files
from neonseek.errors import NeonSeekError
from neonseek.i18n import tr
from neonseek.ui.dialogs.accounts import SoulseekAccountForm, SpotifyForm
from neonseek.ui.dialogs.base import Dialog, LineView
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets import spacing
from neonseek.ui.workers import Worker

# answers of the library question
MOVE_FILES = "move"
ONLY_FOLDER = "folder"
# the scrolling page with every section, tests and the screenshot tool find it by this name
PAGE_NAME = "settings-page"


class SettingsDialog(Dialog):
    def __init__(
        self, ctx: actions.AppContext, theme: ThemeManager, parent=None, downloading: bool = False
    ) -> None:
        super().__init__(theme, lambda: tr("Settings"), parent)
        self.ctx = ctx
        self.worker: Worker | None = None
        # a new library folder whose files the window moves after save (D-128)
        self.move_to: str | None = None
        self.resize_scaled(860, 820)
        settings = ctx.settings()
        texts = self.texts

        sizes = self.sizes

        def section(text, chapter: str | None = None) -> spacing.Section:
            """a section with a HELP button that opens its chapter of the manual (D-195)."""
            on_help = (lambda: self.open_help(chapter)) if chapter else None
            part = spacing.Section(theme, text, on_help)
            layout.addWidget(part)
            return part

        def hint(text) -> QLabel:
            return texts.text(spacing.Hint(), text)

        def row_label(text) -> QLabel:
            return texts.text(QLabel(), text)

        scroll = QScrollArea()
        scroll.setObjectName(PAGE_NAME)
        scroll.setWidgetResizable(True)
        page = QWidget()
        page.setProperty("role", "surface")
        layout = QVBoxLayout(page)
        # room inside the frame and between the sections (D-194)
        sizes.layout(layout, margins=spacing.WINDOW, spacing=spacing.SECTION)
        scroll.setWidget(page)
        self.body.addWidget(scroll, 1)

        # first, so it is easy to find in a language one does not read (D-163)
        part = section(lambda: tr("LANGUAGE"), manual.CHAPTER_APPEARANCE)
        self.language_box = QComboBox()
        for code, name in i18n.LANGUAGES.items():
            # every language in its own name
            self.language_box.addItem(name, code)
        self.language_box.setCurrentIndex(max(0, self.language_box.findData(i18n.language())))
        self.language_box.currentIndexChanged.connect(self._language_changed)
        part.add(
            self.language_box,
            hint(lambda: tr("The window, messages and the manual. Saved at once.")),
        )

        part = section(lambda: tr("SOULSEEK ACCOUNT"), manual.CHAPTER_SOULSEEK)
        self.soulseek = SoulseekAccountForm(ctx, theme)
        part.add(self.soulseek)

        part = section(lambda: tr("MUSIC LIBRARY"), manual.CHAPTER_LIBRARY)
        row = QHBoxLayout()
        sizes.layout(row, margins=0, spacing=spacing.BUTTONS)
        self.library = QLineEdit(settings.library_dir)
        browse = texts.text(QPushButton(), lambda: tr("BROWSE"))
        browse.clicked.connect(self._browse)
        row.addWidget(self.library, 1)
        row.addWidget(browse)
        if downloading:
            # files must not move under a running download
            for widget in (self.library, browse):
                widget.setEnabled(False)
                texts.tip(widget, views.running_reason)
            part.add(
                row, hint(lambda: tr("The library folder cannot change while a download runs."))
            )
        else:
            part.add(
                row,
                hint(
                    lambda: tr(
                        "A new folder asks whether to move the downloaded files there. Files you "
                        "put into the library yourself are never moved or deleted."
                    )
                ),
            )

        part = section(lambda: tr("SPOTIFY"), manual.CHAPTER_SPOTIFY)
        self.spotify = SpotifyForm(ctx, theme)
        part.add(self.spotify)
        self.sync_on_start = texts.text(
            QCheckBox(), lambda: tr("Refresh the list of my playlists when NeonSeek starts")
        )
        self.sync_on_start.setChecked(settings.sync_on_start)
        part.add(self.sync_on_start, hint(lambda: views.setting_hint("sync_on_start")))

        part = section(lambda: tr("DOWNLOADS"), manual.CHAPTER_DOWNLOADING)
        form = spacing.FormGrid(sizes)
        self.on_missing = self._choices("on_missing_flac", config.ON_MISSING_FLAC,
                                        settings.on_missing_flac)  # fmt: skip
        form.add_row(row_label(lambda: tr("Without FLAC")), self.on_missing,
                     hint(lambda: views.setting_hint("on_missing_flac")))  # fmt: skip
        self.attempts = QSpinBox()
        self.attempts.setRange(1, 100)
        self.attempts.setValue(settings.max_attempts)
        form.add_row(row_label(lambda: tr("Attempts")), self.attempts,
                     hint(lambda: views.setting_hint("max_attempts")), width=140)  # fmt: skip
        self.idle = QSpinBox()
        self.idle.setRange(5, 24 * 60)
        texts.add(self.idle.setSuffix, lambda: tr(" min"))
        self.idle.setValue(settings.idle_timeout_minutes)
        form.add_row(
            row_label(lambda: tr("Silence limit")),
            self.idle,
            hint(lambda: views.setting_hint("idle_timeout_minutes")),
            width=140,
        )
        part.add(form)

        part = section(lambda: tr("TAGS"), manual.CHAPTER_TAGS)
        tags = spacing.FormGrid(sizes)
        self.tags = self._choices("tags_from_source", config.TAGS_FROM_SOURCE,
                                  settings.tags_from_source)  # fmt: skip
        tags.add_row(row_label(lambda: tr("Source fields")), self.tags,
                     hint(lambda: views.setting_hint("tags_from_source")))  # fmt: skip
        part.add(tags)
        # the fields of both forms start at the same place
        spacing.same_label_column(sizes, form, tags)

        part = section(lambda: tr("THEME"), manual.CHAPTER_APPEARANCE)
        self.theme_box = QComboBox()
        # theme names are not translated, only the mark of the user's own themes
        self.theme_names: dict[str, tuple[str, bool]] = {}
        for key, item in actions.available_themes(ctx).items():
            self.theme_box.addItem(item.name, key)
            self.theme_names[key] = (item.name, item.builtin)
        texts.call(self._theme_names)
        self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(settings.theme)))
        self.theme_box.currentIndexChanged.connect(self._theme_changed)
        part.add(
            self.theme_box,
            hint(
                lambda: tr("Your own themes: TOML files in the themes folder of {path}.").format(
                    path=ctx.data_dir
                )
            ),
        )

        part = section(lambda: tr("TEXT SIZE"), manual.CHAPTER_APPEARANCE)
        self.scale_box = QComboBox()
        for percent in config.UI_SCALES:
            self.scale_box.addItem(f"{percent}%", percent)
        self.scale_box.setCurrentIndex(self.scale_box.findData(settings.ui_scale))
        self.scale_box.currentIndexChanged.connect(self._scale_changed)
        part.add(self.scale_box, hint(lambda: views.setting_hint("ui_scale")))

        part = section(lambda: tr("SOCKSEEK"))
        check = texts.text(QPushButton(), lambda: tr("CHECK SOCKSEEK"))
        check.clicked.connect(self._check_sockseek)
        part.rows.addWidget(check, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

        self.lines = LineView(theme, max_lines=4)
        self.body.addWidget(self.lines)
        buttons = spacing.button_row(sizes)
        cancel = texts.text(QPushButton(), lambda: tr("CANCEL"))
        cancel.clicked.connect(self.reject)
        save = texts.text(QPushButton(), lambda: tr("SAVE"))
        save.setProperty("primary", True)
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        self.footer.addLayout(buttons)

    def _choices(self, key: str, values: tuple[str, ...], current: str) -> QComboBox:
        box = QComboBox()
        for value in values:
            box.addItem("", value)

        def names() -> None:
            for index in range(box.count()):
                box.setItemText(index, views.choice_label(key, box.itemData(index)))

        self.texts.call(names)
        box.setCurrentIndex(box.findData(current))
        return box

    def _theme_names(self) -> None:
        for index in range(self.theme_box.count()):
            name, builtin = self.theme_names[self.theme_box.itemData(index)]
            self.theme_box.setItemText(index, name if builtin else f"{name} {tr('(your own)')}")

    def _language_changed(self) -> None:
        # saved at once and shown at once, like the theme (D-120, D-163)
        code = self.language_box.currentData()
        try:
            actions.change_setting(self.ctx, "language", code)
        except NeonSeekError as exc:
            self.lines.add(exc.message, outcomes.ERROR)
            return
        self.theme.set_language(code)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Music library folder"),
                                                  self.library.text())  # fmt: skip
        if folder:
            self.library.setText(folder.replace("/", "\\"))

    # theme and text size apply and save at once, like the keyboard shortcuts (D-120)

    def _theme_changed(self) -> None:
        key = self.theme_box.currentData()
        try:
            actions.change_setting(self.ctx, "theme", key)
        except NeonSeekError as exc:
            self.lines.add(exc.message, outcomes.ERROR)
            return
        self.theme.apply(key)

    def _scale_changed(self) -> None:
        percent = self.scale_box.currentData()
        actions.change_setting(self.ctx, "ui_scale", str(percent))
        self.theme.apply(scale=percent)

    def _check_sockseek(self) -> None:
        self.lines.add(tr("Starting Sockseek to read its version (no login)..."))
        self.worker = Worker(self.ctx, lambda ctx, _: actions.sockseek_info(ctx), self)
        self.worker.succeeded.connect(
            lambda info: self.lines.add(
                tr("Sockseek {version} works: {path}").format(version=info.version, path=info.path),
                outcomes.DONE,
            )
        )
        self.worker.failed.connect(lambda message: self.lines.add(message, outcomes.ERROR))
        self.worker.start()

    def ask_move(self, plan) -> str | None:
        """move the files, only change the folder, or none to stay in settings."""
        box = QMessageBox(self)
        box.setWindowTitle("NeonSeek")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(outcomes.move_question(plan))
        box.setInformativeText(outcomes.texts(outcomes.move_plan_lines(plan, gui=True)))
        move = box.addButton(tr("MOVE FILES"), QMessageBox.ButtonRole.AcceptRole)
        only = box.addButton(tr("ONLY CHANGE THE FOLDER"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(tr("CANCEL"), QMessageBox.ButtonRole.RejectRole)
        # help closes the question and opens the chapter, settings stay open (D-182)
        help_button = box.addButton(tr("HELP"), QMessageBox.ButtonRole.HelpRole)
        box.setDefaultButton(move)
        box.exec()
        clicked = box.clickedButton()
        box.deleteLater()
        if clicked is move:
            return MOVE_FILES
        if clicked is only:
            return ONLY_FOLDER
        if clicked is help_button:
            self.open_help(manual.CHAPTER_LIBRARY)
        return None

    def open_help(self, chapter: str) -> None:
        """the manual next to settings: settings block the main window, so they get their own."""
        from neonseek.ui.dialogs.manual import ManualWindow

        ManualWindow.open_for(self.ctx, self.theme, self, chapter)

    def _library_choice(self, wanted: str) -> str | None:
        """none: just set the folder. the move answer, or "cancel" to stay here."""
        if not self.ctx.settings().library_dir:
            return None
        try:
            plan = actions.library_move_plan(self.ctx, wanted)
        except NeonSeekError as exc:
            self.lines.add(exc.message, outcomes.ERROR)
            return "cancel"
        if plan.nothing:
            return None
        return self.ask_move(plan) or "cancel"

    def _save(self) -> None:
        values = {
            "on_missing_flac": self.on_missing.currentData(),
            "max_attempts": str(self.attempts.value()),
            "idle_timeout_minutes": str(self.idle.value()),
            "tags_from_source": self.tags.currentData(),
            "sync_on_start": "true" if self.sync_on_start.isChecked() else "false",
        }
        wanted = self.library.text().strip()
        old = self.ctx.settings().library_dir
        # the same folder written another way is no change
        changed = wanted != old and not (wanted and old and library_files.norm(wanted)
                                         == library_files.norm(old))  # fmt: skip
        if self.library.isEnabled() and changed:
            choice = self._library_choice(wanted)
            if choice == "cancel":
                return
            if choice == MOVE_FILES:
                # the window moves the files, the folder changes when they all arrived
                self.move_to = wanted
            else:
                values["library_dir"] = self.library.text()
        problems = []
        for key, value in values.items():
            try:
                actions.change_setting(self.ctx, key, value)
            except NeonSeekError as exc:
                problems.append(exc.message)
        self.spotify.save_if_changed()
        if problems:
            for message in problems:
                self.lines.add(message, outcomes.ERROR)
            return
        self.accept()
