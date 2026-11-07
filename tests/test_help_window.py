"""the help window (D-182): one chapter at a time, contents, search, place and size, the text
size, help from other windows, and its colors in every theme."""

from __future__ import annotations

import pytest

from neonseek import config, i18n, manual, manual_html, themes

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from neonseek.ui.dialogs import manual as manual_dialog  # noqa: E402
from neonseek.ui.dialogs.manual import ManualWindow  # noqa: E402
from neonseek.ui.dialogs.report import ReportDialog  # noqa: E402
from neonseek.ui.dialogs.settings import SettingsDialog  # noqa: E402
from neonseek.ui.dialogs.wizard import SetupWizard  # noqa: E402
from neonseek.ui.theme.loader import ThemeManager  # noqa: E402


@pytest.fixture
def theme(ctx):
    theme = ThemeManager(ctx.data_dir)
    theme.apply()
    return theme


@pytest.fixture
def help_window(qtbot, ctx, theme):
    window = ManualWindow(ctx, theme)
    qtbot.addWidget(window)
    window.show()
    return window


def _plain(window) -> str:
    return window.text.document().toPlainText()


def test_one_chapter_at_a_time_from_its_top(help_window):
    first = help_window.manual.parts[0]
    assert help_window.chapter == first.id
    assert first.title in _plain(help_window)
    assert help_window.manual.parts[1].title not in _plain(help_window)
    help_window.show_chapter("statuses")
    assert help_window.text.verticalScrollBar().value() == 0
    assert "not downloaded" in _plain(help_window)
    assert "Installing" not in _plain(help_window)


def test_previous_and_next_name_the_chapters(help_window):
    parts = help_window.manual.parts
    assert not help_window.previous.isVisible()
    assert parts[1].title in help_window.next.text() or help_window.next.text().endswith("… ›")
    help_window.next.click()
    assert help_window.chapter == parts[1].id
    assert help_window.previous.isVisible() and parts[0].title[:20] in help_window.previous.text()
    help_window.show_chapter(parts[-1].id)
    assert not help_window.next.isVisible()


def test_contents_mark_the_open_chapter_and_fold_the_others(help_window):
    help_window.show_chapter("install")
    tree = help_window.contents
    items = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    install = next(i for i in items if i.data(0, manual_dialog.CHAPTER_ROLE) == "install")
    csv = next(i for i in items if i.data(0, manual_dialog.CHAPTER_ROLE) == "csv")
    assert tree.currentItem() is install and install.font(0).bold() and install.isExpanded()
    assert csv.childCount() == 2 and not csv.isExpanded()  # format, itunes .txt
    help_window._clicked(csv)
    assert help_window.chapter == "csv" and csv.isExpanded() and not install.isExpanded()


def test_a_section_scrolls_to_its_place_in_the_chapter(help_window):
    help_window.resize(900, 500)
    help_window.show_chapter("install")
    QApplication.processEvents()
    tree = help_window.contents
    install = tree.topLevelItem(0)
    last = install.child(install.childCount() - 1)
    help_window._clicked(last)
    assert help_window.chapter == "install"
    assert help_window.text.verticalScrollBar().value() > 0
    # a section of another chapter opens that chapter at the section
    help_window.show_chapter("csv-format")
    assert help_window.chapter == "csv"


def test_search_lists_chapters_with_hits_and_marks_them(help_window):
    help_window.search.setText("rekordbox")
    help_window._apply_search()
    tree = help_window.contents
    shown = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
             if not tree.topLevelItem(i).isHidden()]  # fmt: skip
    assert 0 < len(shown) < tree.topLevelItemCount()
    assert all("(" in item.text(0) for item in shown)
    assert help_window.chapter in {i.data(0, manual_dialog.CHAPTER_ROLE) for i in shown}
    assert help_window.hit_count() > 0
    # case and accents do not matter
    i18n.set_language(i18n.POLISH)
    help_window.load()
    help_window.search.setText("LODZ")
    help_window._apply_search()
    help_window.search.setText("usuwanie plikow")
    help_window._apply_search()
    assert help_window.chapter == "deleting" and help_window.hit_count() > 0
    help_window.search.clear()
    help_window._apply_search()
    assert help_window.hit_count() == 0
    assert all(not tree.topLevelItem(i).isHidden() for i in range(tree.topLevelItemCount()))


def test_search_without_hits_says_so(help_window):
    help_window.search.setText("zzzqqq")
    help_window._apply_search()
    assert help_window.hits.isVisible() and help_window.hits.text()


def test_reading_font_and_monospace_only_for_code(help_window):
    help_window.show_chapter("csv")
    html = help_window.text.document().toHtml()
    text_font, mono = help_window.html_style().font, help_window.html_style().mono
    assert text_font != mono
    assert help_window.text.document().defaultFont().family() == text_font
    assert mono in html  # the csv example and the column names


def test_images_fit_the_column_and_open_at_full_size(help_window, qtbot):
    help_window.resize(700, 600)
    help_window.show_chapter("first-start")
    QApplication.processEvents()
    html = help_window.text.document().toHtml()
    assert "image:images/en/wizard.jpg" in html
    widths = [int(w) for w in __import__("re").findall(r'width="(\d+)"', html)]
    assert widths and max(widths) <= help_window.column() + 1
    before = set(QApplication.topLevelWidgets())
    help_window._link(QUrl("image:images/en/wizard.jpg"))
    viewers = [w for w in QApplication.topLevelWidgets() if w not in before]
    assert any(isinstance(w, manual_dialog.ImageViewer) for w in viewers)
    for viewer in viewers:
        viewer.close()


def test_warning_boxes_have_the_theme_color(help_window, theme):
    help_window.show_chapter("deleting")
    warning = theme.theme.colors["state.warning"]
    assert warning.lower() in help_window.text.document().toHtml().lower()


def test_text_size_follows_the_setting_and_the_keys(help_window, ctx, theme):
    small = help_window.text.document().defaultFont().pixelSize()
    help_window.step_scale(1)
    assert ctx.settings().ui_scale > config.DEFAULT_UI_SCALE
    assert help_window.text.document().defaultFont().pixelSize() > small
    help_window.step_scale(0)
    assert ctx.settings().ui_scale == config.DEFAULT_UI_SCALE


def test_big_window_with_maximize_that_remembers_place_and_chapter(qtbot, ctx, theme):
    window = ManualWindow(ctx, theme)
    qtbot.addWidget(window)
    assert window.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
    assert not window.isModal()
    area = (window.screen() or QApplication.primaryScreen()).availableGeometry()
    assert window.width() >= area.width() * 0.8 and window.height() >= area.height() * 0.8
    window.show_chapter("report")
    window.resize(800, 600)
    window.done(0)
    state = config.load_ui_state(ctx.data_dir)[manual_dialog.STATE_KEY]
    assert state["chapter"] == "report" and state["geometry"]
    again = ManualWindow(ctx, theme)
    qtbot.addWidget(again)
    assert again.chapter == "report" and abs(again.width() - 800) <= 4


def test_help_from_other_windows_opens_their_chapter(qtbot, ctx, theme):
    settings = SettingsDialog(ctx, theme)
    qtbot.addWidget(settings)
    settings.open_help(manual.CHAPTER_LIBRARY)
    window = settings._manual_window
    assert window.chapter == manual.CHAPTER_LIBRARY and window.parentWidget() is settings
    window.done(0)
    wizard = SetupWizard(ctx, theme)
    qtbot.addWidget(wizard)
    wizard.show()
    wizard.open_help()
    assert wizard._manual_window.chapter == manual.CHAPTER_SOULSEEK
    wizard._manual_window.done(0)
    report = ReportDialog(ctx, theme)
    qtbot.addWidget(report)
    with qtbot.waitSignal(report.help_requested):
        next(b for b in report.findChildren(type(report.export)) if b.text() == "HELP").click()


def test_every_chapter_id_exists_in_both_languages():
    for language in i18n.LANGUAGES:
        book = manual.load(language)
        ids = {p.id for p in book.parts} | {s.id for p in book.parts for s in p.sections}
        for chapter in (manual.CHAPTER_FIRST_START, manual.CHAPTER_SOULSEEK,
                        manual.CHAPTER_SPOTIFY, manual.CHAPTER_DOWNLOADING,
                        manual.CHAPTER_REPORT, manual.CHAPTER_LIBRARY, manual.CHAPTER_TAGS,
                        manual.CHAPTER_APPEARANCE):  # fmt: skip
            assert chapter in ids, (language, chapter)


@pytest.mark.parametrize("key", themes.BUILTIN)
def test_help_colors_are_readable_in_every_theme(key):
    style = manual_html.window_style(themes.builtin_theme(key).colors, "Segoe UI", "Mono", 15)
    for name, fg, bg, minimum in manual_html.contrast_pairs(style):
        assert themes.contrast(fg, bg) >= minimum, (key, name, themes.contrast(fg, bg))


def test_paper_colors_are_readable():
    style = manual_html.print_style("Segoe UI", "Mono", 44)
    for name, fg, bg, minimum in manual_html.contrast_pairs(style):
        assert themes.contrast(fg, bg) >= minimum, (name, themes.contrast(fg, bg))


def test_a_long_chapter_opens_at_its_top_after_a_resize(qtbot, ctx, theme):
    window = ManualWindow(ctx, theme, None, "problems")
    qtbot.addWidget(window)
    window.resize(700, 500)
    window.show()
    qtbot.wait(300)
    window.resize(1400, 900)
    qtbot.wait(300)
    assert window.text.verticalScrollBar().value() == 0
