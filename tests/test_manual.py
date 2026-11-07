"""the user manual (D-164): the same chapters in every language, its images, the pdf script and
neonseek help."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from neonseek import cli, i18n, manual

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / "docs" / "manual"
LANGUAGES = list(i18n.LANGUAGES)


def _text(language: str) -> str:
    return (MANUAL / f"{language}.md").read_text(encoding="utf-8")


def test_every_language_has_a_manual():
    for language in LANGUAGES:
        assert manual.source_path(language, MANUAL).name == f"{language}.md"


def test_the_manuals_have_the_same_chapters():
    shapes = {
        language: [chapter.level for chapter in manual.chapters(_text(language))]
        for language in LANGUAGES
    }
    english = shapes[i18n.ENGLISH]
    assert len([level for level in english if level == 2]) >= 15
    for language, shape in shapes.items():
        assert shape == english, language


def test_the_manuals_show_the_same_screenshots_as_jpeg():
    names = {}
    for language in LANGUAGES:
        images = manual.images(_text(language))
        assert images, language
        for image in images:
            path = MANUAL / image
            assert path.exists(), image
            assert path.suffix == ".jpg" and path.read_bytes()[:2] == b"\xff\xd8", image
            assert Path(image).parent.name == language
        names[language] = [Path(image).name for image in images]
    assert names[i18n.POLISH] == names[i18n.ENGLISH]


def test_the_manuals_cover_what_the_owner_asked_for():
    english = _text(i18n.ENGLISH)
    for words in ("SmartScreen", "Uninstall", "Redirect URI", "CSV", "Reload Tag", "Recycle Bin",
                  "banned", "Relocate", "WAV", "Soulseek server rules forbid"):  # fmt: skip
        assert words in english, words
    polish = _text(i18n.POLISH)
    for words in ("SmartScreen", "Odinstaluj", "Redirect URI", "Reload Tag", "Kosz", "zbanowane",
                  "zakazują automatycznych klientów"):  # fmt: skip
        assert words in polish, words


def test_chapter_text_and_its_bounds():
    text = _text(i18n.ENGLISH)
    first = manual.chapter_text(text, 1)
    assert first.startswith("## ") and "![" not in first
    with pytest.raises(IndexError):
        manual.chapter_text(text, 99)


def test_cli_help_lists_and_shows_chapters(capsys, tmp_path):
    assert cli.main(["--data-dir", str(tmp_path), "help"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "1. Installing" in out and "en.md" in out
    assert cli.main(["--data-dir", str(tmp_path), "help", "2"]) == cli.EXIT_OK
    assert capsys.readouterr().out.startswith("## First start")
    assert cli.main(["--data-dir", str(tmp_path), "help", "99"]) == cli.EXIT_ERROR


def test_cli_speaks_the_language_of_the_settings(capsys, tmp_path):
    cli.main(["--data-dir", str(tmp_path), "settings", "language", "pl"])
    capsys.readouterr()
    assert cli.main(["--data-dir", str(tmp_path), "help"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "pl.md" in out and i18n.translate("NeonSeek manual: {path}", "pl").split(":")[0] in out
    with pytest.raises(SystemExit):
        cli.main(["--data-dir", str(tmp_path), "--help"])
    assert i18n.translate("usage: ", "pl").strip() in capsys.readouterr().out


def test_the_pdf_script_builds_both_pdfs(tmp_path):
    pytest.importorskip("PySide6")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_manual.py"), "--out", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    for language in LANGUAGES:
        pdf = tmp_path / manual.pdf_name(language)
        data = pdf.read_bytes()
        assert data.startswith(b"%PDF") and len(data) > 200_000
        # every chapter on a new page: more pages than chapters
        chapters = [c for c in manual.chapters(_text(language)) if c.level == 2]
        assert len(re.findall(rb"/Type\s*/Page\b(?!s)", data)) > len(chapters)


def test_the_window_builds_a_missing_pdf_into_the_data_folder(tmp_path, qapp, monkeypatch):
    from neonseek.ui import manual_view

    monkeypatch.setattr(manual, "built_pdf", lambda language: tmp_path / "none.pdf")
    path = manual_view.pdf_for(tmp_path, i18n.POLISH)
    assert path == manual.cached_pdf(tmp_path, i18n.POLISH)
    assert path.read_bytes().startswith(b"%PDF")
    # built once, then used again while the manual does not change
    stamp = path.stat().st_mtime_ns
    assert manual_view.pdf_for(tmp_path, i18n.POLISH).stat().st_mtime_ns == stamp


# screenshots without real paths (D-181)

SCREENSHOTS = ROOT / "scripts" / "manual_screenshots.py"
FORBIDDEN = ("D:" + chr(92) + "Music" + chr(92) + "NeonSeek", "%APPDATA%" + chr(92) + "NeonSeek")


def test_the_screenshot_script_uses_no_real_paths():
    source = SCREENSHOTS.read_text(encoding="utf-8")
    for path in FORBIDDEN:
        assert path not in source and path.replace(chr(92), chr(92) * 2) not in source, path


def test_the_manual_and_the_app_use_made_up_example_paths():
    for path in [MANUAL / "en.md", MANUAL / "pl.md", *(ROOT / "src" / "neonseek").rglob("*.py")]:
        text = path.read_text(encoding="utf-8")
        assert FORBIDDEN[0] not in text and FORBIDDEN[0].replace(chr(92), chr(92) * 2) not in text


def test_shown_texts_hide_the_temporary_folders(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import manual_screenshots as shots
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    data = tmp_path / "data"
    real = shots.real_paths(tmp_path, data)
    text = f"Themes: {data}{chr(92)}themes, file {tmp_path / 'Warehouse set.csv'}, {Path.home()}"
    shown = shots.neutral_text(text, real)
    assert str(tmp_path) not in shown and str(Path.home()) not in shown
    assert shots.SHOWN_DATA in shown and shots.SHOWN_LISTS in shown
    assert shots.LIBRARY not in FORBIDDEN


# the html of the help window and the pdf (D-182, D-183)

SAMPLE = """## Chapter {#chap}

A **bold** word, `C:/x/y` and a [link](https://example.com) & <tag>.

### Part {#part}

- one
- two

1. first
2. second

| Head | Other |
|---|---|
| a | b |

```
Artist;Title
```

> [!WARNING] Careful
> The account may be banned.

![A caption](images/en/main.jpg)
"""


def test_markdown_becomes_styled_html():
    from neonseek import manual_html, themes

    style = manual_html.window_style(themes.builtin_theme("neon").colors, "Sans", "Mono", 15)
    out = manual_html.to_html(SAMPLE, style, lambda source: (600, 300))
    assert '<h2><a name="chap"></a>Chapter</h2>' in out
    assert '<h3><a name="part"></a>Part</h3>' in out
    assert "<b>bold</b>" in out and "<code>C:/x/y</code>" in out
    assert '<a href="https://example.com">link</a>' in out and "&amp; &lt;tag&gt;" in out
    assert "<ul><li>one</li><li>two</li></ul>" in out and "<ol><li>first</li>" in out
    assert "<th" in out and "<td>a</td>" in out and 'border="1"' in out
    assert "<pre>Artist;Title</pre>" in out
    # the warning box: bar and title in the warning color of the theme
    warning = style.warning
    assert f'bgcolor="{warning}"' in out and "Careful" in out and "banned" in out
    assert 'href="image:images/en/main.jpg"' in out and 'width="600" height="300"' in out
    assert '<p class="caption" align="center">A caption</p>' in out


def test_the_manuals_share_chapter_ids_and_boxes():
    books = {language: manual.load(language, MANUAL) for language in LANGUAGES}
    ids = {
        language: [(p.id, [s.id for s in p.sections]) for p in book.parts]
        for language, book in books.items()
    }
    assert ids[i18n.POLISH] == ids[i18n.ENGLISH]
    assert all(part_id for part_id, _ in ids[i18n.ENGLISH])
    boxes = {language: _text(language).count("> [!WARNING]") for language in LANGUAGES}
    assert boxes[i18n.ENGLISH] == boxes[i18n.POLISH] >= 4


def test_search_finds_chapters_without_case_and_accents():
    book = manual.load(i18n.POLISH, MANUAL)
    hits = manual.search(book, "MENEDZER POSWIADCZEN")
    assert hits and all(count > 0 for count in hits.values())
    assert manual.search(book, "") == {}


def test_the_terminal_chapter_has_no_marks():
    text = _text(i18n.ENGLISH)
    deleting = manual.chapter_text(text, [c.id for c in manual.chapters(text) if c.level == 2]
                                   .index("deleting") + 1)  # fmt: skip
    assert "{#" not in deleting and "[!WARNING]" not in deleting and "rekordbox" in deleting


def test_the_pdf_has_the_boxes_the_spacing_and_the_contents(qapp):
    from neonseek import manual_html
    from neonseek.ui import manual_view

    for language in LANGUAGES:
        book = manual.load(language, MANUAL)
        style = manual_html.print_style("Segoe UI", "Mono", 44)
        text = manual_view.pdf_html(book, style, lambda _: None, language, [3] * len(book.parts))
        assert text.count("page-break-before: always") == len(book.parts)
        assert text.count(f'bgcolor="{style.warning}"') >= 4
        assert text.count(i18n.translate("page {number}", language).format(number=3)) == len(
            book.parts
        )
        # one proportional font for the text, the monospace one only for code
        assert f"font-family: '{style.font}'" in text
        assert manual_html.stylesheet(style).count(style.mono) == 2
