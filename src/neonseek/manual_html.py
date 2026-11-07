"""the manual as html for the help window and the pdf (D-182, D-183). no qt here: a small
markdown reader for what the manual uses, and a style from the colors of a theme or for paper.

markdown understood: headings with {#id}, paragraphs, - and 1. lists, pipe tables, ``` code
blocks, > [!WARNING] title and > [!NOTE] title boxes, **bold**, `code`, [links](url) and
![captions](images/...) on their own line.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass

from neonseek import themes

# the least contrast of text in the help window, the same as text.muted in the theme rules
TEXT_MIN = 4.5
BORDER_MIN = 3.0
IMAGE_SCHEME = "image:"

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*(?:\{#([A-Za-z0-9_-]+)\})?\s*$")
_ORDERED = re.compile(r"^\s*(\d+)\.\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_IMAGE_LINE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)\)\s*$")
_CALLOUT = re.compile(r"^\[!(WARNING|NOTE)\]\s*(.*)$")
_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


@dataclass(frozen=True)
class Style:
    """colors, fonts and sizes of the manual. sizes are in unit, px on screen, pt on paper."""

    text: str
    muted: str
    background: str
    raised: str  # code, table headers and warning boxes
    border: str
    link: str
    warning: str  # the bar and title of a warning box
    note: str  # the bar and title of a note box
    warning_title: str
    note_title: str
    highlight: str  # search hits in the window
    highlight_text: str
    font: str
    mono: str
    size: float
    unit: str = "px"
    # line height in percent of the natural line of the font, which is about 1.33 times the
    # text size: 113 gives lines 1.5 times the text size apart (D-196)
    line: int = 113
    # qt reads css margins and paddings in screen pixels even on a printer, font sizes not
    spacing: float = 1.0

    def at(self, factor: float) -> str:
        """a font size, factor times the text size."""
        return f"{self.size * factor:.1f}{self.unit}"

    def gap(self, factor: float) -> str:
        """a margin or padding, factor times the text size."""
        return f"{self.size * factor * self.spacing:.1f}{self.unit}"


def _readable(candidates: list[str], background: str, minimum: float) -> str:
    """the first color with enough contrast, else the one with the most."""
    for color in candidates:
        if themes.contrast(color, background) >= minimum:
            return color
    return max(candidates, key=lambda color: themes.contrast(color, background))


def window_style(
    colors: dict[str, str], font: str, mono: str, size: float, line: int = 113
) -> Style:
    """the help window in the colors of the current theme. line is the line height that
    gives 1.5 times the text size with this font."""
    surface, raised = colors["bg.surface"], colors["bg.elevated"]
    highlight = colors["accent.secondary"]
    text, accent, warning = (
        colors["text.primary"],
        colors["accent.secondary"],
        colors["state.warning"],
    )
    on_highlight = [colors["bg.base"], colors["text.on_accent"], text]
    return Style(
        text=colors["text.primary"],
        muted=colors["text.muted"],
        background=surface,
        raised=raised,
        border=colors["border"],
        link=_readable([colors["accent.secondary"], colors["text.primary"]], surface, TEXT_MIN),
        warning=colors["state.warning"],
        note=colors["accent.secondary"],
        warning_title=_readable([warning, text], raised, TEXT_MIN),
        note_title=_readable([accent, text], raised, TEXT_MIN),
        highlight=highlight,
        highlight_text=_readable(on_highlight, highlight, TEXT_MIN),
        font=font,
        mono=mono,
        size=size,
        line=line,
    )


def print_style(font: str, mono: str, size: float) -> Style:
    """the pdf: dark text on white paper, warnings in amber. size in pixels of the printer,
    qt reads css lengths as pixels only."""
    return Style(
        text="#1A1A1A",
        muted="#555555",
        background="#FFFFFF",
        raised="#F3F1EE",
        border="#9A9A9A",
        link="#1A4E8C",
        warning="#C77700",
        note="#2B6CB0",
        warning_title="#8A4B00",
        note_title="#1F4E85",
        highlight="#FFE08A",
        highlight_text="#1A1A1A",
        font=font,
        mono=mono,
        size=size,
        line=112,
        spacing=96 / 300,
    )


def contrast_pairs(style: Style) -> list[tuple[str, str, str, float]]:
    """what the help window paints on what, with the least contrast it needs."""
    return [
        ("text", style.text, style.background, TEXT_MIN),
        ("captions", style.muted, style.background, TEXT_MIN),
        ("links", style.link, style.background, TEXT_MIN),
        ("text in boxes, tables and code", style.text, style.raised, TEXT_MIN),
        ("warning titles", style.warning_title, style.raised, TEXT_MIN),
        ("note titles", style.note_title, style.raised, TEXT_MIN),
        ("search hits", style.highlight_text, style.highlight, TEXT_MIN),
        ("warning bar", style.warning, style.background, BORDER_MIN),
        ("note bar", style.note, style.background, BORDER_MIN),
    ]


def stylesheet(style: Style) -> str:
    """css for the qt text document. qt knows a subset of css, so tables and boxes also get
    html attributes."""
    s = style
    return f"""
p {{ margin-top: 0; margin-bottom: {s.gap(0.9)}; line-height: {s.line}%; }}
h1 {{ font-size: {s.at(1.9)}; font-weight: 600; margin-top: 0; margin-bottom: {s.gap(0.8)}; }}
h2 {{ font-size: {s.at(1.5)}; font-weight: 600; margin-top: {s.gap(1.6)};
      margin-bottom: {s.gap(0.7)}; }}
h3 {{ font-size: {s.at(1.18)}; font-weight: 600; margin-top: {s.gap(1.5)};
      margin-bottom: {s.gap(0.5)}; }}
ul, ol {{ margin-top: 0; margin-bottom: {s.gap(0.8)}; }}
li {{ margin-bottom: {s.gap(0.45)}; line-height: {s.line}%; }}
code {{ font-family: '{s.mono}'; font-size: {s.at(1)}; background-color: {s.raised}; }}
pre {{ font-family: '{s.mono}'; font-size: {s.at(1)}; margin: 0; }}
a {{ color: {s.link}; }}
td {{ line-height: {round(s.line * 0.9)}%; }}
p.caption {{ color: {s.muted}; font-size: {s.at(0.9)}; margin-top: {s.gap(0.35)};
             margin-bottom: {s.gap(1.3)}; }}
p.figure {{ margin-bottom: 0; }}
"""


def _inline(text: str) -> str:
    """bold, code and links. code keeps its text as it is."""
    parts = []
    last = 0
    for match in _CODE.finditer(text):
        parts.append(_inline_text(text[last : match.start()]))
        parts.append(f"<code>{html.escape(match.group(1))}</code>")
        last = match.end()
    parts.append(_inline_text(text[last:]))
    return "".join(parts)


def _inline_text(text: str) -> str:
    out = html.escape(text, quote=False)
    out = _BOLD.sub(r"<b>\1</b>", out)
    return _LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', out)


@dataclass
class _Writer:
    style: Style
    image_size: Callable[[str], tuple[int, int] | None]
    out: list[str]

    def table_attributes(self, padding: float) -> str:
        return (
            f'width="100%" cellspacing="0" cellpadding="{padding:.0f}" border="1" '
            f'style="border-collapse: collapse; border-style: solid; border-color: '
            f'{self.style.border}; margin-bottom: {self.style.gap(1.0)};"'
        )


def to_html(
    markdown: str,
    style: Style,
    image_size: Callable[[str], tuple[int, int] | None] = lambda _: None,
) -> str:
    """the markdown as html with the style. image_size gives width and height of an image as
    shown, none keeps its own size."""
    writer = _Writer(style, image_size, [])
    _blocks(markdown.splitlines(), writer)
    body = "\n".join(writer.out)
    s = style
    return (
        f"<html><body style=\"font-family: '{s.font}'; font-size: {s.at(1)}; color: {s.text};\">"
        f"{body}</body></html>"
    )


def _blocks(lines: list[str], w: _Writer) -> None:
    i = 0
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            w.out.append(f"<p>{_inline(' '.join(part.strip() for part in paragraph))}</p>")
            paragraph.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush()
            i += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            level, title, anchor = len(heading.group(1)), heading.group(2), heading.group(3)
            name = f'<a name="{anchor}"></a>' if anchor else ""
            w.out.append(f"<h{level}>{name}{_inline(title)}</h{level}>")
            i += 1
            continue
        if stripped.startswith("```"):
            flush()
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            _code_block(code, w)
            continue
        if stripped.startswith(">"):
            flush()
            quoted = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quoted.append(lines[i].strip()[1:].lstrip())
                i += 1
            _box(quoted, w)
            continue
        if stripped.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            _table(rows, w)
            continue
        image = _IMAGE_LINE.match(stripped)
        if image:
            flush()
            _image(image.group(1), image.group(2), w)
            i += 1
            continue
        if _ORDERED.match(line) or _BULLET.match(line):
            flush()
            i = _list(lines, i, w)
            continue
        paragraph.append(line)
        i += 1
    flush()


def _list(lines: list[str], i: int, w: _Writer) -> int:
    ordered = bool(_ORDERED.match(lines[i]))
    pattern = _ORDERED if ordered else _BULLET
    items: list[list[str]] = []
    while i < len(lines):
        line = lines[i]
        match = pattern.match(line)
        if match:
            items.append([match.group(2) if ordered else match.group(1)])
        elif line.strip() and line.startswith((" ", "\t")) and items:
            items[-1].append(line.strip())
        else:
            break
        i += 1
    tag = "ol" if ordered else "ul"
    body = "".join(f"<li>{_inline(' '.join(item))}</li>" for item in items)
    w.out.append(f"<{tag}>{body}</{tag}>")
    return i


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _table(rows: list[str], w: _Writer) -> None:
    rows = [r for r in rows if not re.fullmatch(r"\|?[\s:|-]+\|?", r)]
    if not rows:
        return
    s = w.style
    out = [f"<table {w.table_attributes(s.size * 0.55 * s.spacing)}>"]
    for index, row in enumerate(rows):
        cells = _cells(row)
        if index == 0:
            out.append(
                "<tr>"
                + "".join(f'<th bgcolor="{s.raised}" align="left">{_inline(c)}</th>' for c in cells)
                + "</tr>"
            )
        else:
            out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
    out.append("</table>")
    w.out.append("".join(out))


def _code_block(code: list[str], w: _Writer) -> None:
    s = w.style
    text = html.escape("\n".join(code), quote=False)
    w.out.append(
        f'<table width="100%" cellspacing="0" cellpadding="{s.size * 0.7 * s.spacing:.0f}" '
        f'border="0" '
        f'bgcolor="{s.raised}" style="margin-bottom: {s.gap(1.0)};"><tr><td>'
        f"<pre>{text}</pre></td></tr></table>"
    )


def _box(quoted: list[str], w: _Writer) -> None:
    """> [!WARNING] title as a box with a colored bar, other quotes as a plain box."""
    s = w.style
    first = _CALLOUT.match(quoted[0]) if quoted else None
    kind = first.group(1) if first else "NOTE"
    title = first.group(2) if first else ""
    body = quoted[1:] if first else quoted
    bar = s.warning if kind == "WARNING" else s.note
    title_color = s.warning_title if kind == "WARNING" else s.note_title
    inner = _Writer(s, w.image_size, [])
    if title:
        inner.out.append(f'<p><b style="color: {title_color};">{_inline(title)}</b></p>')
    _blocks(body, inner)
    content = "\n".join(inner.out)
    # the last paragraph of a box needs no room below it
    content = re.sub(r"<p>(?!.*<p>)", '<p style="margin-bottom: 0;">', content, flags=re.S)
    w.out.append(
        f'<table width="100%" cellspacing="0" cellpadding="0" border="1" '
        f'style="border-collapse: collapse; border-style: solid; border-color: {bar}; '
        f'margin-top: {s.gap(0.3)}; margin-bottom: {s.gap(1.1)};">'
        f'<tr><td width="{max(3, round(s.size * 0.35 * s.spacing))}" bgcolor="{bar}"></td>'
        f'<td bgcolor="{s.raised}" style="padding: {s.gap(0.8)};">{content}</td></tr></table>'
    )


def _image(caption: str, source: str, w: _Writer) -> None:
    shown = w.image_size(source)
    size = f' width="{shown[0]}" height="{shown[1]}"' if shown else ""
    w.out.append(
        f'<p class="figure" align="center"><a href="{IMAGE_SCHEME}{html.escape(source)}">'
        f'<img src="{html.escape(source)}"{size}></a></p>'
    )
    if caption:
        w.out.append(f'<p class="caption" align="center">{_inline(caption)}</p>')
