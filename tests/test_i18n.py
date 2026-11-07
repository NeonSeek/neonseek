"""the polish interface (D-163): every text translated with the same fields, plural rules,
the language setting, and no text in the window code outside tr()."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from neonseek import actions, config, i18n, i18n_catalog

UI = Path(i18n_catalog.PACKAGE) / "ui"

# qt calls whose text arguments the user reads
TEXT_CALLS = {
    "setText", "setToolTip", "setWindowTitle", "setPlaceholderText", "addItem", "setItemText",
    "setTitle", "setSubTitle", "setButtonText", "setInformativeText", "setStatusTip",
    "setSuffix", "setPrefix", "setSpecialValueText", "addTab", "setTabText", "addAction",
    "addButton", "addRow", "information", "warning", "question", "critical",
    "setHorizontalHeaderLabels", "QLabel", "QPushButton", "QCheckBox", "QRadioButton",
    "QGroupBox", "QAction", "SectionLabel", "ElidedLabel", "ElidedSectionLabel",
    "QListWidgetItem", "QTableWidgetItem", "getOpenFileName", "getSaveFileName",
    "getExistingDirectory",
}  # fmt: skip
# texts that stay as they are in every language: the name of the app, a file name
ALLOWED = {"CSV (*.csv)", "neonseek-report.csv"}
NAMES = re.compile(r"neonseek", re.IGNORECASE)
_WORDS = re.compile(r"[A-Za-z]{2,}")
# keyword arguments that carry text, others (role=, objectName=) are names in the code
TEXT_KEYWORDS = {"text", "title", "toolTip", "caption", "filter"}


def _strings(node: ast.AST):
    """string constants in a call argument, also inside f-strings and lists."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.JoinedStr):
        for value in node.values:
            yield from _strings(value)
    elif isinstance(node, ast.List | ast.Tuple):
        for item in node.elts:
            yield from _strings(item)
    elif isinstance(node, ast.BinOp):
        yield from _strings(node.left)
        yield from _strings(node.right)


def _user_text(value: str) -> bool:
    """words someone reads. the app name, links and html markup are not."""
    if value in ALLOWED or value.startswith(("http://", "https://")) or "<" in value:
        return False
    if '="' in value:
        return False
    return bool(_WORDS.search(NAMES.sub("", value)))


def ui_texts_outside_tr() -> list[str]:
    found = []
    for path in sorted(UI.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name not in TEXT_CALLS:
                continue
            keywords = [k.value for k in node.keywords if k.arg in TEXT_KEYWORDS]
            for arg in [*node.args, *keywords]:
                for value in _strings(arg):
                    if _user_text(value):
                        found.append(f"{path.relative_to(UI)}:{node.lineno} {value!r}")
    return found


def test_window_code_has_no_text_outside_tr():
    assert ui_texts_outside_tr() == []


def test_every_translated_text_is_written_in_place():
    # tr(variable) only works when the text is marked with N_() where it is defined
    assert i18n_catalog.dynamic_calls() == []


@pytest.mark.parametrize("language", [c for c in i18n.LANGUAGES if c != i18n.SOURCE_LANGUAGE])
def test_catalog_is_complete_with_the_same_fields(language):
    texts = i18n_catalog.extract()
    raw = i18n_catalog.load_raw(language)
    assert i18n_catalog.problems(raw, texts, language) == []


def test_catalog_file_is_in_code_order():
    # the script writes it, so a hand edit that breaks the order or json shows up here
    raw = i18n_catalog.load_raw(i18n.POLISH)
    texts = i18n_catalog.extract()
    expected, _ = i18n_catalog.updated(raw, texts, i18n.POLISH)
    assert i18n_catalog.dump(raw) == i18n_catalog.dump(expected)


def test_placeholders_cover_both_styles():
    assert i18n_catalog.placeholders("{count} of {total}, {path!r}") == [
        "{count}",
        "{path!r}",
        "{total}",
    ]
    assert i18n_catalog.placeholders("%(prog)s: error: %(message)s %s") == [
        "%(message)s",
        "%(prog)s",
        "%s",
    ]


@pytest.mark.parametrize(
    ("count", "index"),
    [(1, 0), (2, 1), (4, 1), (5, 2), (11, 2), (12, 2), (14, 2), (21, 2), (22, 1), (104, 1),
     (112, 2), (0, 2)],
)  # fmt: skip
def test_polish_plural_forms(count, index):
    assert i18n.plural_index(i18n.POLISH, count) == index


def test_english_plural_forms():
    assert i18n.plural_index(i18n.ENGLISH, 1) == 0
    assert i18n.plural_index(i18n.ENGLISH, 0) == 1
    assert i18n.plural_index(i18n.ENGLISH, 2) == 1


def test_tr_and_ntr_follow_the_language():
    assert i18n.tr("SETTINGS") == "SETTINGS"
    i18n.set_language(i18n.POLISH)
    assert i18n.tr("SETTINGS") != "SETTINGS"
    one = i18n.ntr(1, "{count} file", "{count} files").format(count=1)
    few = i18n.ntr(3, "{count} file", "{count} files").format(count=3)
    many = i18n.ntr(7, "{count} file", "{count} files").format(count=7)
    assert len({one, few, many}) == 3
    # a text the catalog does not know stays english instead of failing
    assert i18n.tr("not a text of neonseek") == "not a text of neonseek"
    i18n.set_language("xx")
    assert i18n.language() == i18n.ENGLISH


def test_translate_and_every_language_at_once():
    assert i18n.translate("LANGUAGE", i18n.ENGLISH) == "LANGUAGE"
    both = i18n.in_every_language("LANGUAGE")
    assert both.startswith("LANGUAGE / ") and len(both.split(" / ")) == len(i18n.LANGUAGES)


def test_language_setting(tmp_path):
    path = tmp_path / "settings.toml"
    settings = config.load_settings(path)
    assert settings.language == ""
    config.set_setting(settings, "language", "pl")
    config.save_settings(settings, path)
    assert config.load_settings(path).language == "pl"
    with pytest.raises(config.ConfigError):
        config.set_setting(settings, "language", "de")
    path.write_text('language = "klingon"\n', encoding="utf-8")
    assert config.load_settings(path).language == ""


@pytest.mark.parametrize("windows", [i18n.POLISH, i18n.ENGLISH])
def test_first_start_takes_the_language_of_windows(tmp_path, monkeypatch, windows):
    monkeypatch.setattr(i18n, "detect_language", lambda: windows)
    ctx = actions.AppContext(tmp_path)
    # the cli only follows windows, the window remembers the choice
    assert actions.start_language(ctx, remember=False) == windows
    assert ctx.settings().language == ""
    assert actions.start_language(ctx, remember=True) == windows
    assert ctx.settings().language == windows
    # later the setting wins, whatever windows says
    other = i18n.ENGLISH if windows == i18n.POLISH else i18n.POLISH
    monkeypatch.setattr(i18n, "detect_language", lambda: other)
    assert actions.start_language(ctx, remember=True) == windows


def test_change_setting_switches_the_language_at_once(tmp_path):
    ctx = actions.AppContext(tmp_path)
    actions.change_setting(ctx, "language", "pl")
    assert i18n.language() == i18n.POLISH
    assert ctx.settings().language == "pl"


def test_a_broken_catalog_speaks_english():
    catalog = i18n.parse_catalog("pl", '{"messages": {"A": ""}, "plurals": [{"en": ["a"]}]}')
    assert catalog.messages == {} and catalog.plurals == {}
