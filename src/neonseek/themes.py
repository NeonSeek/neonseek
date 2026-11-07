"""color themes as data: one toml file per theme with the same tokens (PLAN.md section 9).

no qt here. the gui renders the style sheet and colors the icons from these values, the cli
lists and picks themes through the theme setting.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from neonseek import config
from neonseek.errors import NeonSeekError
from neonseek.i18n import tr

TOKENS = (
    "bg.base",
    "bg.surface",
    "bg.elevated",
    "border",
    "text.primary",
    "text.muted",
    "text.on_accent",
    "accent.primary",
    "accent.secondary",
    "chip.bg",
    "state.success",
    "state.queued",
    "state.warning",
    "state.error",
)
FLAGS = ("glow", "dark")
DEFAULT = config.DEFAULT_THEME
BUILTIN = ("neon", "daylight", "mono", "phosphor", "ice", "noir")

# minimal wcag contrast of every theme: foreground, background, ratio (PLAN.md section 9)
CONTRAST_RULES: tuple[tuple[str, str, float], ...] = (
    ("text.primary", "bg.base", 7.0),
    ("text.primary", "bg.surface", 7.0),
    ("text.primary", "bg.elevated", 4.5),
    ("text.muted", "bg.surface", 4.5),
    ("text.on_accent", "accent.primary", 4.5),
    ("accent.primary", "bg.surface", 3.0),
    ("accent.secondary", "bg.surface", 3.0),
    ("state.success", "bg.surface", 3.0),
    ("state.queued", "bg.surface", 3.0),
    ("state.warning", "bg.surface", 3.0),
    ("state.error", "bg.surface", 3.0),
)

# the text over the filled part of the progress bar: one of these tokens, the one that stands
# out most against the whole gradient accent.primary -> accent.secondary (D-133)
PROGRESS_TEXT_TOKENS = ("text.on_accent", "bg.base", "text.primary")
PROGRESS_TEXT_MIN = 3.0
GRADIENT_STEPS = 10

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_KEY = config.THEME_KEY


class ThemeError(NeonSeekError):
    pass


@dataclass(frozen=True)
class Theme:
    key: str  # file name without .toml, stored in settings
    name: str  # shown in the list
    colors: dict[str, str]  # token -> #rrggbb
    glow: bool
    dark: bool
    builtin: bool = True

    def color(self, token: str) -> str:
        return self.colors[token]


def _flatten(table: dict, prefix: str = "") -> dict[str, object]:
    flat: dict[str, object] = {}
    for key, value in table.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{name}."))
        else:
            flat[name] = value
    return flat


def parse_theme(key: str, text: str, builtin: bool = False) -> Theme:
    """a theme from toml text. any missing or wrong value makes the whole file invalid."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ThemeError(tr("not valid toml ({error})").format(error=exc)) from exc
    flat = _flatten(raw)
    missing = [t for t in TOKENS if t not in flat]
    if missing:
        raise ThemeError(tr("missing colors: {tokens}").format(tokens=", ".join(missing)))
    bad = [t for t in TOKENS if not isinstance(flat[t], str) or not _HEX.match(flat[t])]
    if bad:
        raise ThemeError(
            tr("colors must look like #1A2B3C: {tokens}").format(tokens=", ".join(bad))
        )
    flags = {}
    for flag in FLAGS:
        value = flat.get(flag, False)
        if not isinstance(value, bool):
            raise ThemeError(tr("{flag} must be true or false").format(flag=flag))
        flags[flag] = value
    name = flat.get("name")
    if not isinstance(name, str) or not name.strip():
        name = key
    return Theme(
        key=key,
        name=name.strip(),
        colors={t: str(flat[t]).upper() for t in TOKENS},
        glow=flags["glow"],
        dark=flags["dark"],
        builtin=builtin,
    )


def builtin_theme(key: str) -> Theme:
    folder = resources.files("neonseek.ui.theme").joinpath("themes")
    text = folder.joinpath(f"{key}.toml").read_text(encoding="utf-8")
    return parse_theme(key, text, builtin=True)


def user_dir(data_dir: Path) -> Path:
    return data_dir / "themes"


def load_themes(data_dir: Path, log: Callable[[str], None] = lambda _: None) -> dict[str, Theme]:
    """built in themes first, then the user's toml files. a broken file is skipped and logged,
    it never stops the app. a user file cannot replace a built in theme."""
    themes: dict[str, Theme] = {}
    for key in BUILTIN:
        try:
            themes[key] = builtin_theme(key)
        except (OSError, ThemeError) as exc:
            # only a missing file in a broken install, the default always comes first
            log(f"built in theme {key} skipped: {exc}")
    folder = user_dir(data_dir)
    files = sorted(folder.glob("*.toml")) if folder.is_dir() else []
    for path in files:
        key = path.stem.lower()
        if not _KEY.match(key):
            log(f"theme {path.name} skipped: use only letters, digits, - and _ in the file name")
            continue
        if key in themes:
            log(f"theme {path.name} skipped: a theme named {key} already exists")
            continue
        try:
            themes[key] = parse_theme(key, path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ThemeError) as exc:
            log(f"theme {path.name} skipped: {exc}")
    return themes


def pick(themes: dict[str, Theme], key: str) -> Theme:
    """the theme of the setting, the default when it is gone."""
    return themes.get(key) or themes.get(DEFAULT) or next(iter(themes.values()))


# wcag 2 contrast


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def contrast_problems(theme: Theme) -> list[str]:
    problems = []
    for fg, bg, minimum in CONTRAST_RULES:
        ratio = contrast(theme.colors[fg], theme.colors[bg])
        if ratio < minimum:
            problems.append(f"{fg} on {bg}: {ratio:.2f} < {minimum}")
    return problems


def mix(a: str, b: str, share: float) -> str:
    """a color between a and b, share 0 is a, 1 is b. the way qt blends a gradient."""
    channels = []
    for i in (1, 3, 5):
        start, end = int(a[i : i + 2], 16), int(b[i : i + 2], 16)
        channels.append(round(start + (end - start) * share))
    return "#" + "".join(f"{c:02X}" for c in channels)


def progress_gradient(theme: Theme) -> list[str]:
    start, end = theme.colors["accent.primary"], theme.colors["accent.secondary"]
    return [mix(start, end, step / GRADIENT_STEPS) for step in range(GRADIENT_STEPS + 1)]


def progress_text_contrast(theme: Theme, token: str) -> float:
    """the worst contrast of a token on any point of the progress gradient."""
    color = theme.colors[token]
    return min(contrast(color, point) for point in progress_gradient(theme))


def progress_text_token(theme: Theme) -> str:
    """the token for text over the filled part of the progress bar."""
    return max(PROGRESS_TEXT_TOKENS, key=lambda token: progress_text_contrast(theme, token))
