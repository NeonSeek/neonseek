"""tr() and ntr() for core, cli and gui. the translations are data in neonseek/locales, so the
core never needs qt. the english text in the code is the key (D-163)."""

from __future__ import annotations

import ctypes
import json
import locale
import os
import sys
import threading
from dataclasses import dataclass, field
from importlib import resources

ENGLISH = "en"
POLISH = "pl"
# every language in its own name, so anyone finds theirs (PLAN.md section 9)
LANGUAGES = {ENGLISH: "English", POLISH: "Polski"}
SOURCE_LANGUAGE = ENGLISH
# the polish primary language id of windows (LANG_POLISH)
_WINDOWS_POLISH = 0x15


@dataclass
class Catalog:
    """english text -> translation, and the plural forms of each ntr() pair."""

    language: str
    messages: dict[str, str] = field(default_factory=dict)
    plurals: dict[tuple[str, str], list[str]] = field(default_factory=dict)


def plural_index(language: str, count: int) -> int:
    """which plural form a count takes. polish has three: 1 utwór, 2 utwory, 5 utworów."""
    if language == POLISH:
        if count == 1:
            return 0
        if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
            return 1
        return 2
    return 0 if count == 1 else 1


def plural_count(language: str) -> int:
    return 3 if language == POLISH else 2


def catalog_text(language: str) -> str:
    return (
        resources.files("neonseek")
        .joinpath("locales", f"{language}.json")
        .read_text(encoding="utf-8")
    )


def parse_catalog(language: str, text: str) -> Catalog:
    raw = json.loads(text)
    catalog = Catalog(language)
    # an empty translation is a missing one, english shows instead
    catalog.messages = {k: v for k, v in raw.get("messages", {}).items() if v}
    for entry in raw.get("plurals", []):
        forms = entry.get(language) or []
        if len(entry.get(SOURCE_LANGUAGE, [])) == 2 and all(forms):
            catalog.plurals[tuple(entry[SOURCE_LANGUAGE])] = list(forms)
    return catalog


_lock = threading.Lock()
_catalogs: dict[str, Catalog] = {}
_language = ENGLISH


def _catalog(language: str) -> Catalog | None:
    if language == SOURCE_LANGUAGE:
        return None
    with _lock:
        if language not in _catalogs:
            try:
                _catalogs[language] = parse_catalog(language, catalog_text(language))
            except (OSError, ValueError):
                # a broken catalog never stops the app, it speaks english instead
                _catalogs[language] = Catalog(language)
        return _catalogs[language]


def language() -> str:
    return _language


def set_language(code: str | None) -> str:
    """switches every later tr() call. an unknown code is english."""
    global _language
    _language = code if code in LANGUAGES else ENGLISH
    return _language


def detect_language() -> str:
    """polish when windows speaks polish, else english (first start, D-163)."""
    if sys.platform == "win32":
        try:
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return POLISH if langid & 0x3FF == _WINDOWS_POLISH else ENGLISH
        except (AttributeError, OSError):
            pass
    for value in (os.environ.get("LC_ALL"), os.environ.get("LANG"), locale.getlocale()[0]):
        if value:
            return POLISH if value.lower().startswith(("pl", "polish")) else ENGLISH
    return ENGLISH


def translate(text: str, code: str) -> str:
    """the text in one given language, whatever the current one is."""
    catalog = _catalog(code)
    if catalog is None:
        return text
    return catalog.messages.get(text, text)


def tr(text: str) -> str:
    return translate(text, _language)


def in_every_language(text: str) -> str:
    """the text in every language at once, like LANGUAGE / JĘZYK, for the one place where
    someone has to find their language before any is chosen."""
    return " / ".join(dict.fromkeys(translate(text, code) for code in LANGUAGES))


def ntr(count: int, one: str, many: str) -> str:
    """the form of the text for this count. english has two forms, polish three."""
    catalog = _catalog(_language)
    if catalog is not None:
        forms = catalog.plurals.get((one, many))
        if forms:
            return forms[min(plural_index(_language, count), len(forms) - 1)]
    return one if count == 1 else many


def N_(text: str) -> str:  # noqa: N802 - the usual gettext name
    """marks a text for the catalog where it is defined. tr() translates it where it is shown."""
    return text
