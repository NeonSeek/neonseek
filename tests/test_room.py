"""room around texts (D-194) and the help buttons of settings (D-195)."""

from __future__ import annotations

import re

import pytest

from neonseek import i18n, manual

pytest.importorskip("PySide6")

from PySide6.QtGui import QFont, QFontMetricsF  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QLabel,
    QMessageBox,
    QScrollArea,
    QStyle,
)

from neonseek.ui.dialogs.manual import READING_LINE, ManualWindow  # noqa: E402
from neonseek.ui.dialogs.settings import PAGE_NAME, SettingsDialog  # noqa: E402
from neonseek.ui.dialogs.wizard import SetupWizard  # noqa: E402
from neonseek.ui.theme.loader import ThemeManager  # noqa: E402
from neonseek.ui.widgets import spacing  # noqa: E402


@pytest.fixture
def theme(ctx):
    theme = ThemeManager(ctx.data_dir)
    theme.apply()
    return theme


def _page_layout(dialog):
    return dialog.findChild(QScrollArea, PAGE_NAME).widget().layout()


def test_settings_sections_have_room_that_grows_with_the_text(qtbot, ctx, theme):
    dialog = SettingsDialog(ctx, theme)
    qtbot.addWidget(dialog)
    layout = _page_layout(dialog)
    assert layout.spacing() == spacing.SECTION
    assert layout.contentsMargins().left() == spacing.WINDOW
    section = dialog.findChildren(spacing.Section)[0]
    assert section.layout().spacing() == spacing.HEADER
    theme.apply(scale=150)
    assert layout.spacing() == round(spacing.SECTION * 1.5)
    assert layout.contentsMargins().left() == round(spacing.WINDOW * 1.5)
    assert section.layout().spacing() == round(spacing.HEADER * 1.5)
    assert dialog.body.contentsMargins().left() == round(spacing.WINDOW * 1.5)


def test_forms_of_settings_share_one_label_column(qtbot, ctx, theme):
    dialog = SettingsDialog(ctx, theme)
    qtbot.addWidget(dialog)
    dialog.show()
    QApplication.processEvents()
    starts = {dialog.on_missing.mapTo(dialog, dialog.on_missing.rect().topLeft()).x(),
              dialog.attempts.mapTo(dialog, dialog.attempts.rect().topLeft()).x(),
              dialog.tags.mapTo(dialog, dialog.tags.rect().topLeft()).x()}  # fmt: skip
    assert len(starts) == 1


def test_descriptions_are_muted_with_a_line_of_1_4(qtbot, ctx, theme):
    dialog = SettingsDialog(ctx, theme)
    qtbot.addWidget(dialog)
    hints = dialog.findChildren(spacing.Hint)
    # muted descriptions, steps to follow in the text color, problems in their frame
    assert hints and all(h.property("role") in ("muted", None, "waiting") for h in hints)
    assert sum(h.property("role") == "muted" for h in hints) >= 10
    hint = next(h for h in hints if h.property("role") == "muted")
    # the plain text for whoever reads it, the line height in what qt shows
    assert "<" not in hint.text()
    shown = QLabel.text(hint)
    percent = int(re.search(r"line-height: (\d+)%", shown).group(1))
    metrics = QFontMetricsF(hint.font())
    line = percent / 100 * metrics.lineSpacing() / hint.font().pixelSize()
    assert 1.35 <= line <= 1.45


def test_help_window_lines_are_1_5_apart_not_more(qtbot, ctx, theme):
    window = ManualWindow(ctx, theme)
    qtbot.addWidget(window)
    style = window.html_style()
    font = QFont(style.font)
    font.setPixelSize(round(style.size))
    line = style.line / 100 * QFontMetricsF(font).lineSpacing() / style.size
    assert READING_LINE == 1.5 and 1.42 <= line <= 1.52
    window.done(0)


def test_sections_of_settings_have_a_help_button_not_a_question_mark(qtbot, ctx, theme):
    dialog = SettingsDialog(ctx, theme)
    qtbot.addWidget(dialog)
    buttons = [b for b in dialog.findChildren(QAbstractButton) if b.text() == "?"]
    assert buttons == []
    helps = dialog.findChildren(spacing.HelpButton)
    # every section with a chapter: language, soulseek, library, spotify, downloads, tags,
    # theme, text size
    assert len(helps) == 8
    for button in helps:
        assert button.text() == "HELP" and not button.icon().isNull()
        assert button.toolTip() == "Opens the chapter of the manual about this section"
    theme.set_language(i18n.POLISH)
    assert helps[0].text() == "POMOC"
    assert helps[0].toolTip() == "Otwiera rozdział instrukcji o tej sekcji"


def test_a_help_button_opens_the_chapter_of_its_section(qtbot, ctx, theme):
    dialog = SettingsDialog(ctx, theme)
    qtbot.addWidget(dialog)
    section = next(s for s in dialog.findChildren(spacing.Section)
                   if s.title.text() == "MUSIC LIBRARY")  # fmt: skip
    section.help.click()
    assert dialog._manual_window.chapter == manual.CHAPTER_LIBRARY
    dialog._manual_window.done(0)


def test_wizard_help_has_the_icon_and_a_tip(qtbot, ctx, theme):
    from PySide6.QtWidgets import QWizard

    wizard = SetupWizard(ctx, theme)
    qtbot.addWidget(wizard)
    button = wizard.button(QWizard.WizardButton.HelpButton)
    assert button.text() == "HELP" and not button.icon().isNull() and button.toolTip()


def test_question_boxes_get_room_that_grows_with_the_text(qtbot, theme):
    box = QMessageBox()
    qtbot.addWidget(box)
    style = box.style()
    margin = style.pixelMetric(QStyle.PixelMetric.PM_LayoutLeftMargin, None, box)
    assert margin == 20
    theme.apply(scale=150)
    assert style.pixelMetric(QStyle.PixelMetric.PM_LayoutLeftMargin, None, box) == 30
    theme.apply(scale=100)
