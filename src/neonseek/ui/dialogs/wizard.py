"""first run wizard: language and soulseek account, library folder, spotify (optional), theme
(PLAN.md 9)."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from neonseek import actions, config, i18n, manual
from neonseek.errors import NeonSeekError
from neonseek.i18n import N_, tr
from neonseek.ui.dialogs.accounts import SoulseekAccountForm, SpotifyForm
from neonseek.ui.dialogs.base import (
    MIN_SHARE,
    FitContent,
    FreeScroll,
    close_when_idle,
    fit_to_screen,
)
from neonseek.ui.theme.loader import ThemeManager
from neonseek.ui.widgets import spacing
from neonseek.ui.widgets.common import LiveTexts, SectionLabel


def _text() -> QLabel:
    return spacing.Hint()


# the size the wizard opens with, at 100 % text size
SIZE = (720, 620)


def _page_layout(page: QWizardPage, theme: ThemeManager) -> QVBoxLayout:
    """the steps of a page with room between them (D-194). the steps scroll when the window is
    smaller than them, so the wizard resizes freely in every direction (D-252)."""
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    content = FitContent()
    layout = QVBoxLayout(content)
    spacing.LiveSizes(theme, page).layout(layout, margins=(0, 8, 0, 0), spacing=spacing.SECTION)
    outer.addWidget(FreeScroll(content))
    return layout


class SoulseekPage(QWizardPage):
    def __init__(self, ctx: actions.AppContext, theme: ThemeManager) -> None:
        super().__init__()
        self.ctx = ctx
        self.theme = theme
        texts = LiveTexts(theme, self)
        texts.add(self.setTitle, lambda: tr("1. Language and Soulseek account"))
        layout = _page_layout(self, theme)
        sizes = spacing.LiveSizes(theme, self)
        row = QHBoxLayout()
        sizes.layout(row, margins=0, spacing=spacing.LABEL)
        # in every language at once, so anyone finds theirs (D-163)
        row.addWidget(SectionLabel(i18n.in_every_language(N_("LANGUAGE"))))
        self.language = QComboBox()
        for code, name in i18n.LANGUAGES.items():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(i18n.language())))
        self.language.currentIndexChanged.connect(self._language_changed)
        row.addWidget(self.language)
        row.addStretch(1)
        layout.addLayout(row)
        account = QVBoxLayout()
        sizes.layout(account, margins=0, spacing=spacing.ROW)
        account.addWidget(
            texts.text(
                _text(),
                lambda: tr(
                    "NeonSeek downloads from the Soulseek network, so it needs a Soulseek "
                    "account. The login is kept in Windows Credential Manager, never in a file."
                ),
            )
        )
        self.form = SoulseekAccountForm(ctx, theme)
        # asked again once the form is idle: the login is saved while its worker still runs, so
        # the saved signal alone left NEXT grey (D-250)
        self.form.state_changed.connect(self.completeChanged.emit)
        account.addWidget(self.form, 1)
        layout.addLayout(account, 1)

    def _language_changed(self) -> None:
        # saved and shown at once: this wizard and the window behind it (D-163)
        code = self.language.currentData()
        actions.change_setting(self.ctx, "language", code)
        self.theme.set_language(code)

    def isComplete(self) -> bool:  # noqa: N802 - qt name
        return actions.soulseek_login(self.ctx) is not None and not self.form.busy()


class LibraryPage(QWizardPage):
    def __init__(self, ctx: actions.AppContext, theme: ThemeManager) -> None:
        super().__init__()
        self.ctx = ctx
        texts = LiveTexts(theme, self)
        texts.add(self.setTitle, lambda: tr("2. Music library folder"))
        layout = _page_layout(self, theme)
        sizes = spacing.LiveSizes(theme, self)
        layout.addWidget(
            texts.text(
                _text(),
                lambda: tr(
                    "Downloaded tracks go to the tracks folder inside it, playlists for rekordbox "
                    "to the playlists folder. Choose a folder on an internal drive, for example "
                    "D:\\Music\\Library."
                ),
            )
        )
        row = QHBoxLayout()
        sizes.layout(row, margins=0, spacing=spacing.BUTTONS)
        self.path = QLineEdit(ctx.settings().library_dir)
        self.path.textChanged.connect(lambda _: self.completeChanged.emit())
        browse = texts.text(QPushButton(), lambda: tr("BROWSE"))
        browse.clicked.connect(self._browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        self.problem.setProperty("role", "waiting")
        self.problem.setVisible(False)
        layout.addWidget(self.problem)
        layout.addStretch(1)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Music library folder"),
                                                  self.path.text())  # fmt: skip
        if folder:
            self.path.setText(folder.replace("/", "\\"))

    def isComplete(self) -> bool:  # noqa: N802 - qt name
        return bool(self.path.text().strip())

    def validatePage(self) -> bool:  # noqa: N802 - qt name
        try:
            actions.change_setting(self.ctx, "library_dir", self.path.text())
        except NeonSeekError as exc:
            self.problem.setText(exc.message)
            self.problem.setVisible(True)
            return False
        return True


class SpotifyPage(QWizardPage):
    def __init__(self, ctx: actions.AppContext, theme: ThemeManager) -> None:
        super().__init__()
        texts = LiveTexts(theme, self)
        texts.add(self.setTitle, lambda: tr("3. Spotify (optional)"))
        layout = _page_layout(self, theme)
        layout.addWidget(
            texts.text(
                _text(),
                lambda: tr(
                    "No Spotify? Press NEXT. You can import CSV files and set Spotify up later "
                    "in Settings."
                ),
            )
        )
        self.form = SpotifyForm(ctx, theme)
        layout.addWidget(self.form, 1)

    def validatePage(self) -> bool:  # noqa: N802 - qt name
        self.form.save_if_changed()
        return True


class ThemePage(QWizardPage):
    def __init__(self, ctx: actions.AppContext, theme: ThemeManager) -> None:
        super().__init__()
        self.ctx = ctx
        self.theme = theme
        texts = LiveTexts(theme, self)
        texts.add(self.setTitle, lambda: tr("4. Colors"))
        layout = _page_layout(self, theme)
        layout.addWidget(
            texts.text(_text(), lambda: tr("Pick a theme. You can change it any time in Settings."))
        )
        self.list = QListWidget()
        self.list.setProperty("role", "roomy")
        current = ctx.settings().theme
        for key, item_theme in actions.available_themes(ctx).items():
            item = QListWidgetItem(item_theme.name)
            item.setData(256, key)
            self.list.addItem(item)
            if key == current:
                self.list.setCurrentItem(item)
        self.list.currentItemChanged.connect(self._preview)
        layout.addWidget(self.list)

    def _preview(self, item, _previous) -> None:
        if item is not None:
            self.theme.apply(item.data(256))

    def validatePage(self) -> bool:  # noqa: N802 - qt name
        item = self.list.currentItem()
        if item is not None:
            actions.change_setting(self.ctx, "theme", item.data(256))
        return True


class SetupWizard(QWizard):
    def __init__(self, ctx: actions.AppContext, theme: ThemeManager, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.theme = theme
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        # help opens the chapter of the step next to the wizard (D-182)
        self.setOption(QWizard.WizardOption.HaveHelpButton, True)
        self.helpRequested.connect(self.open_help)
        texts = LiveTexts(theme, self)
        texts.add(self.setWindowTitle, lambda: tr("NeonSeek - first start"))
        texts.call(self._button_texts)
        width, height = SIZE
        self.resize(theme.px(width), theme.px(height))
        self.setMinimumSize(theme.px(width * MIN_SHARE), theme.px(height * MIN_SHARE))
        theme.track_window(self)
        self.soulseek = SoulseekPage(ctx, theme)
        self.library = LibraryPage(ctx, theme)
        self.spotify = SpotifyPage(ctx, theme)
        self.colors = ThemePage(ctx, theme)
        for page in (self.soulseek, self.library, self.spotify, self.colors):
            self.addPage(page)
        help_button = self.button(QWizard.WizardButton.HelpButton)
        texts.tip(help_button, lambda: tr("Opens the chapter of the manual about this step"))
        theme.changed.connect(self._paint_help)
        self._paint_help()

    def showEvent(self, event) -> None:  # noqa: N802 - qt name
        super().showEvent(event)
        # a big windows scale on a small screen: the buttons stay on it (D-252)
        QTimer.singleShot(0, self, lambda: fit_to_screen(self))

    def _paint_help(self) -> None:
        self.button(QWizard.WizardButton.HelpButton).setIcon(self.theme.icon("manual"))

    def _button_texts(self) -> None:
        for button, text in (
            (QWizard.WizardButton.BackButton, tr("BACK")),
            (QWizard.WizardButton.NextButton, tr("NEXT")),
            (QWizard.WizardButton.FinishButton, tr("FINISH")),
            (QWizard.WizardButton.CancelButton, tr("LATER")),
            (QWizard.WizardButton.HelpButton, tr("HELP")),
        ):
            self.setButtonText(button, text)

    def open_help(self) -> None:
        from neonseek.ui.dialogs.manual import ManualWindow

        chapters = {
            self.soulseek: manual.CHAPTER_SOULSEEK,
            self.library: manual.CHAPTER_FIRST_START,
            self.spotify: manual.CHAPTER_SPOTIFY,
            self.colors: manual.CHAPTER_APPEARANCE,
        }
        chapter = chapters.get(self.currentPage(), manual.CHAPTER_FIRST_START)
        ManualWindow.open_for(self.ctx, self.theme, self, chapter)

    def done(self, result: int) -> None:
        if not close_when_idle(self):
            return
        if result != QWizard.DialogCode.Accepted:
            # closing early keeps what was saved, the wizard shows again at the next start
            self.theme.apply(self.ctx.settings().theme or config.DEFAULT_THEME)
        super().done(result)
