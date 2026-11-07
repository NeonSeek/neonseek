"""the window changes its language without restart (D-163) and shows the manual (D-164)."""

from __future__ import annotations

import pytest

from neonseek import actions, i18n
from neonseek.sources import ingest
from neonseek.sources.base import CollectionRecord, TrackRecord

pytest.importorskip("PySide6")

from neonseek.ui.dialogs.manual import ManualWindow  # noqa: E402
from neonseek.ui.dialogs.report import ReportDialog  # noqa: E402
from neonseek.ui.dialogs.settings import SettingsDialog  # noqa: E402
from neonseek.ui.dialogs.wizard import SetupWizard  # noqa: E402
from neonseek.ui.main_window import MainWindow  # noqa: E402
from neonseek.ui.theme.loader import ThemeManager  # noqa: E402


@pytest.fixture
def theme(ctx):
    theme = ThemeManager(ctx.data_dir)
    theme.apply()
    return theme


@pytest.fixture
def window(qtbot, ctx, theme):
    record = CollectionRecord(
        kind="csv", origin="file", name="Mix", source_ref="C:/mix.csv", snapshot_id="a",
        tracks=[TrackRecord(title="One", artists=("A",), duration_ms=60_000)],
    )  # fmt: skip
    ingest.ingest_collection(ctx.repo, record)
    win = MainWindow(ctx, theme, list_at_start=False)
    qtbot.addWidget(win)
    yield win
    win.covers.shutdown()


def _polish(text: str) -> str:
    return i18n.translate(text, i18n.POLISH)


def test_the_window_speaks_polish_at_once(window, theme):
    assert window.settings_button.text() == "SETTINGS"
    theme.set_language(i18n.POLISH)
    assert window.settings_button.text() == _polish("SETTINGS") != "SETTINGS"
    assert window.header.download.text() == _polish("DOWNLOAD")
    assert window.log.cancel.text() == _polish("CANCEL")
    assert window.panel.add_button.text() == _polish("+ ADD")
    assert window.filter.itemText(0) == _polish("All")
    # texts core makes from the data follow too: states, counts, column titles
    assert window.table.model_.rows[0].state_label == _polish("not downloaded")
    assert window.table.model_.headerData(2, window.table.horizontalHeader().orientation()) == (
        _polish("TITLE")
    )
    assert window.header.stats.full_text().startswith(
        i18n.ntr(1, "{count} track", "{count} tracks").format(count=1)
    )
    theme.set_language(i18n.ENGLISH)
    assert window.settings_button.text() == "SETTINGS"


def test_settings_change_the_language_and_save_it(window, ctx, theme, qtbot):
    dialog = SettingsDialog(ctx, theme, window)
    qtbot.addWidget(dialog)
    dialog.language_box.setCurrentIndex(dialog.language_box.findData(i18n.POLISH))
    assert ctx.settings().language == i18n.POLISH
    assert i18n.language() == i18n.POLISH
    # the open dialog and the window behind it both changed
    assert dialog.windowTitle() == "NeonSeek - " + _polish("Settings")
    assert window.report_button.text() == _polish("REPORT")


def test_the_first_step_of_the_wizard_changes_the_language(ctx, theme, qtbot):
    wizard = SetupWizard(ctx, theme)
    qtbot.addWidget(wizard)
    page = wizard.soulseek
    page.language.setCurrentIndex(page.language.findData(i18n.POLISH))
    assert ctx.settings().language == i18n.POLISH
    assert page.title() == _polish("1. Language and Soulseek account")
    assert wizard.windowTitle() == _polish("NeonSeek - first start")


def test_an_open_report_follows_the_language(window, ctx, theme, qtbot):
    report = ReportDialog(ctx, theme, window)
    qtbot.addWidget(report)
    theme.set_language(i18n.POLISH)
    assert report.retry.text() == _polish("RETRY SELECTED")
    assert report.table.horizontalHeaderItem(0).text() == _polish("TRACK")


def test_the_language_works_during_a_download(window, theme):
    # a running download keeps running, only texts change (no window rebuild)
    window.download = object()
    try:
        theme.set_language(i18n.POLISH)
        assert window.download_checked.text() == _polish("DOWNLOAD CHECKED")
    finally:
        window.download = None


def _titles(window) -> list[str]:
    tree = window.contents
    return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]


def test_help_opens_the_manual_in_the_language_of_the_window(window, theme, qtbot):
    window.open_manual()
    help_window = window.manual_window
    assert isinstance(help_window, ManualWindow)
    english = _titles(help_window)
    assert english and help_window.source.name == "en.md"
    help_window.show_chapter("statuses")
    theme.set_language(i18n.POLISH)
    # the same chapter, now in polish
    assert help_window.source.name == "pl.md" and help_window.chapter == "statuses"
    polish = _titles(help_window)
    assert len(polish) == len(english) and polish != english
    help_window.done(0)


def test_first_start_of_the_window_remembers_the_language(ctx, monkeypatch):
    monkeypatch.setattr(i18n, "detect_language", lambda: i18n.POLISH)
    assert actions.start_language(ctx, remember=True) == i18n.POLISH
    assert ctx.settings().language == i18n.POLISH
