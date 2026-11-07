"""the user manual: one markdown file per language, its chapters, the search and the pdf
(D-164, D-182). no qt here, the window shows the manual and builds the pdf in neonseek.ui.

every heading of level 2 and 3 ends with {#id}. the ids are the same in every language, so the
window keeps its chapter when the language changes and other windows can open a chapter.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from neonseek import i18n
from neonseek.textfold import find_all

# docs/manual in the repository, manual\ next to the program in the package (phase 4)
_REPO_MANUAL = Path(__file__).resolve().parents[2] / "docs" / "manual"
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*(?:\{#([A-Za-z0-9_-]+)\})?\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_CALLOUT = re.compile(r"^(\s*>\s*)\[!(WARNING|NOTE)\]\s*")
_ID = re.compile(r"\s*\{#[A-Za-z0-9_-]+\}\s*$")

# chapters other windows open with their help (D-182)
CHAPTER_FIRST_START = "first-start"
CHAPTER_SOULSEEK = "soulseek"
CHAPTER_SPOTIFY = "spotify"
CHAPTER_DOWNLOADING = "downloading"
CHAPTER_REPORT = "report"
CHAPTER_LIBRARY = "library-folder"
CHAPTER_TAGS = "tags"
CHAPTER_APPEARANCE = "appearance"


def manual_dir() -> Path:
    if getattr(sys, "frozen", False):
        # next to NeonSeek.exe, where people find the pdfs too (D-217)
        return Path(sys.executable).parent / "manual"
    return _REPO_MANUAL


def source_path(language: str | None = None, base: Path | None = None) -> Path:
    """the manual in this language, english when there is none."""
    base = base or manual_dir()
    path = base / f"{language or i18n.language()}.md"
    return path if path.exists() else base / f"{i18n.SOURCE_LANGUAGE}.md"


def pdf_name(language: str) -> str:
    return f"NeonSeek-manual-{language}.pdf"


def built_pdf(language: str, base: Path | None = None) -> Path:
    """where scripts/build_manual.py puts the pdf, and where the package carries it."""
    return (base or manual_dir()) / "pdf" / pdf_name(language)


def cached_pdf(data_dir: Path, language: str) -> Path:
    """a pdf the window built itself when the package had none (D-164)."""
    return data_dir / "manual" / pdf_name(language)


def pdf_is_current(pdf: Path, source: Path) -> bool:
    return pdf.exists() and pdf.stat().st_mtime >= source.stat().st_mtime


@dataclass(frozen=True)
class Chapter:
    level: int
    title: str
    # line of the heading in the markdown file, counted from 0
    line: int
    id: str = ""


def chapters(text: str, deepest: int = 3) -> list[Chapter]:
    """headings of level 2 and 3, the table of contents. code blocks are skipped."""
    found = []
    fenced = False
    for number, line in enumerate(text.splitlines()):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        match = None if fenced else _HEADING.match(line)
        if match and 2 <= len(match.group(1)) <= deepest:
            found.append(Chapter(len(match.group(1)), match.group(2), number, match.group(3) or ""))
    return found


@dataclass(frozen=True)
class Section:
    id: str
    title: str


@dataclass
class Part:
    """one chapter of the manual: its markdown with the heading, and its sections."""

    id: str
    title: str
    markdown: str
    sections: list[Section] = field(default_factory=list)


@dataclass
class Manual:
    title: str
    intro: str
    parts: list[Part]

    def part(self, chapter_id: str | None) -> Part | None:
        return next((p for p in self.parts if p.id == chapter_id), None)

    def part_of(self, any_id: str | None) -> Part | None:
        """the chapter with this id, or the chapter that holds this section."""
        for part in self.parts:
            if part.id == any_id or any(s.id == any_id for s in part.sections):
                return part
        return None

    def index(self, chapter_id: str) -> int:
        return next(i for i, p in enumerate(self.parts) if p.id == chapter_id)


def parse(text: str) -> Manual:
    lines = text.splitlines()
    heads = chapters(text)
    tops = [c for c in heads if c.level == 2]
    first = tops[0].line if tops else len(lines)
    title_line = next((line for line in lines[:first] if line.startswith("# ")), "# ")
    intro = "\n".join(line for line in lines[:first] if line != title_line).strip()
    parts = []
    for index, top in enumerate(tops):
        end = tops[index + 1].line if index + 1 < len(tops) else len(lines)
        sections = [
            Section(c.id, c.title) for c in heads if c.level == 3 and top.line < c.line < end
        ]
        parts.append(Part(top.id or f"chapter-{index + 1}", top.title,
                          "\n".join(lines[top.line : end]).strip(), sections))  # fmt: skip
    return Manual(title_line[2:].strip(), intro, parts)


def load(language: str | None = None, base: Path | None = None) -> Manual:
    return parse(source_path(language, base).read_text(encoding="utf-8"))


def images(text: str) -> list[str]:
    """paths of the images the manual shows, as written in it."""
    return re.findall(r"!\[[^\]]*\]\(([^)\s]+)", text)


def plain(markdown: str) -> str:
    """the text a reader sees, without markdown marks, for the search."""
    text = _IMAGE.sub(lambda m: m.group(1), markdown)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"(?m)^\s*>\s*\[!(WARNING|NOTE)\]\s*", "", text)
    text = re.sub(r"(?m)\s*\{#[A-Za-z0-9_-]+\}\s*$", "", text)
    text = re.sub(r"(?m)^\s*(#+|>|-|\d+\.)\s+", "", text)
    return re.sub(r"[*`|]", " ", text)


def search(manual: Manual, query: str) -> dict[str, int]:
    """chapters with hits and how many, in the order of the manual. case and accents do not
    matter."""
    hits = {}
    for part in manual.parts:
        count = len(find_all(plain(part.markdown), query))
        if count:
            hits[part.id] = count
    return hits


def chapter_text(text: str, index: int) -> str:
    """the markdown of one level 2 chapter, counted from 1, with its sections, for a terminal:
    images become their caption, ids and box marks go."""
    lines = text.splitlines()
    tops = [c for c in chapters(text) if c.level == 2]
    if not 1 <= index <= len(tops):
        raise IndexError(index)
    start = tops[index - 1].line
    end = tops[index].line if index < len(tops) else len(lines)
    body = "\n".join(lines[start:end]).strip()
    body = _IMAGE.sub(lambda m: f"[{m.group(1)}]", body)
    body = "\n".join(_ID.sub("", _CALLOUT.sub(r"\1", line)) for line in body.splitlines())
    return body
