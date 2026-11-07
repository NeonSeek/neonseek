"""paths and user settings. everything lives per windows user in %APPDATA%\\NeonSeek."""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

import platformdirs

from neonseek.errors import ConfigError
from neonseek.i18n import LANGUAGES, tr

APP_NAME = "NeonSeek"
# dev and test override, never set in a normal install
DATA_DIR_ENV = "NEONSEEK_DATA_DIR"


def data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False, roaming=True))


def db_path(base: Path | None = None) -> Path:
    return (base or data_dir()) / "neonseek.db"


def settings_path(base: Path | None = None) -> Path:
    return (base or data_dir()) / "settings.toml"


def logs_dir(base: Path | None = None) -> Path:
    return (base or data_dir()) / "logs"


def covers_dir(base: Path | None = None) -> Path:
    return (base or data_dir()) / "covers"


def cache_dir(base: Path | None = None) -> Path:
    """caches of helper libraries (yt-dlp), never outside the neonseek data folder."""
    return (base or data_dir()) / "cache"


# on_missing_flac values (PLAN.md section 7)
LOSSLESS = "lossless"
NOT_FOUND = "not_found"
ON_MISSING_FLAC = (LOSSLESS, NOT_FOUND)

# tags_from_source values (PLAN.md section 8)
TAGS_FILL = "fill"
TAGS_OVERWRITE = "overwrite"
TAGS_OFF = "off"
TAGS_FROM_SOURCE = (TAGS_FILL, TAGS_OVERWRITE, TAGS_OFF)

DEFAULT_THEME = "neon"

# size of text and everything around it in the window, in percent (owner decision, D-115)
UI_SCALES = (90, 100, 110, 125, 150)
DEFAULT_UI_SCALE = 100
THEME_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


@dataclass
class Settings:
    # given by the user in the wizard or settings, not a secret
    spotify_client_id: str = ""
    # folder of the music library, empty until the user picks one
    library_dir: str = ""
    on_missing_flac: str = LOSSLESS
    max_attempts: int = 3
    idle_timeout_minutes: int = 10
    # uploader tags are inconsistent, the source is uniform (D-083)
    tags_from_source: str = TAGS_OVERWRITE
    # file name of a theme without .toml, a missing one falls back to the default
    theme: str = DEFAULT_THEME
    ui_scale: int = DEFAULT_UI_SCALE
    # the list of spotify playlists is read again when the window starts (D-130)
    sync_on_start: bool = True
    # en or pl, empty until the first start picks one from windows (D-163)
    language: str = ""
    # keys written by a newer version, kept so a downgrade does not lose them
    extra: dict[str, object] = field(default_factory=dict)


# a value outside these limits falls back to the default, a hand edited file never crashes a run
# the most attempts the settings allow, also the longest "failed n/n" label
MAX_ATTEMPTS_LIMIT = 100
_INT_LIMITS = {"max_attempts": (1, MAX_ATTEMPTS_LIMIT), "idle_timeout_minutes": (5, 24 * 60)}


def _known_keys() -> set[str]:
    return {f.name for f in fields(Settings)} - {"extra"}


def load_settings(path: Path | None = None) -> Settings:
    path = path or settings_path()
    if not path.exists():
        return Settings()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(
            tr(
                "Settings file {path} is damaged ({error}). Fix it or delete it to start over."
            ).format(path=path, error=exc)
        ) from exc
    known = _known_keys()
    settings = Settings(extra={k: v for k, v in raw.items() if k not in known})
    for key in ("spotify_client_id", "library_dir"):
        value = raw.get(key, "")
        setattr(settings, key, value.strip() if isinstance(value, str) else "")
    if raw.get("on_missing_flac") in ON_MISSING_FLAC:
        settings.on_missing_flac = raw["on_missing_flac"]
    if raw.get("tags_from_source") in TAGS_FROM_SOURCE:
        settings.tags_from_source = raw["tags_from_source"]
    if isinstance(raw.get("theme"), str) and THEME_KEY.match(raw["theme"]):
        settings.theme = raw["theme"]
    settings.ui_scale = ui_scale(raw.get("ui_scale"))
    if raw.get("language") in LANGUAGES:
        settings.language = raw["language"]
    if isinstance(raw.get("sync_on_start"), bool):
        settings.sync_on_start = raw["sync_on_start"]
    for key, (low, high) in _INT_LIMITS.items():
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
            setattr(settings, key, value)
    return settings


def check_int_setting(key: str, value: int) -> int:
    low, high = _INT_LIMITS[key]
    if not low <= value <= high:
        raise ConfigError(
            tr("{key} must be between {low} and {high}.").format(key=key, low=low, high=high)
        )
    return value


CHOICES = {
    "on_missing_flac": ON_MISSING_FLAC,
    "tags_from_source": TAGS_FROM_SOURCE,
    "language": tuple(LANGUAGES),
}
# settings the user changes in the settings window (spotify_client_id has its own command)
EDITABLE = ("library_dir", "on_missing_flac", "max_attempts", "idle_timeout_minutes",
            "tags_from_source", "theme", "ui_scale", "sync_on_start", "language")  # fmt: skip
# yes and no as the cli takes them
BOOLEANS = {"true": True, "yes": True, "on": True, "1": True,
            "false": False, "no": False, "off": False, "0": False}  # fmt: skip


def ui_scale(value: object) -> int:
    """a scale from the list, anything else is the default."""
    if isinstance(value, int) and not isinstance(value, bool) and value in UI_SCALES:
        return value
    return DEFAULT_UI_SCALE


def step_ui_scale(current: int, step: int) -> int:
    """the next bigger (step 1) or smaller (step -1) scale, it stops at the ends."""
    scales = list(UI_SCALES)
    index = scales.index(ui_scale(current)) + step
    return scales[max(0, min(index, len(scales) - 1))]


def check_key(key: str) -> str:
    if key not in EDITABLE:
        raise ConfigError(
            tr("Unknown setting {key}. Settings you can change: {keys}.").format(
                key=key, keys=", ".join(EDITABLE)
            )
        )
    return key


def set_setting(settings: Settings, key: str, value: str) -> object:
    """checks a typed value and stores it. returns the stored value."""
    check_key(key)
    value = value.strip()
    if key in CHOICES:
        if value not in CHOICES[key]:
            raise ConfigError(
                tr("{key} can be: {values}.").format(key=key, values=", ".join(CHOICES[key]))
            )
        stored: object = value
    elif key in _INT_LIMITS:
        try:
            number = int(value)
        except ValueError:
            raise ConfigError(tr("{key} must be a whole number.").format(key=key)) from None
        stored = check_int_setting(key, number)
    elif key == "ui_scale":
        number = value.rstrip("%").strip()
        if not number.isdigit() or int(number) not in UI_SCALES:
            raise ConfigError(
                tr("{key} can be: {values}.").format(
                    key=key, values=", ".join(f"{s}%" for s in UI_SCALES)
                )
            )
        stored = int(number)
    elif key == "sync_on_start":
        if value.lower() not in BOOLEANS:
            raise ConfigError(tr("{key} can be: true, false.").format(key=key))
        stored = BOOLEANS[value.lower()]
    elif key == "theme":
        # whether the theme exists depends on the user's theme folder, actions checks that
        if not THEME_KEY.match(value):
            raise ConfigError(tr("{value} is not a theme name.").format(value=value))
        stored = value
    else:
        stored = check_library_dir(value)
    setattr(settings, key, stored)
    return stored


def check_library_dir(value: str) -> str:
    path = Path(os.path.expandvars(value.strip().strip('"'))).expanduser()
    if not value.strip() or not path.is_absolute():
        raise ConfigError(
            tr("Give the full path of the library folder, for example D:\\Music\\Library.")
        )
    if path.exists() and not path.is_dir():
        raise ConfigError(tr("{path} is a file, not a folder. Choose a folder.").format(path=path))
    return str(path)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        # a json string is a valid toml basic string
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"unsupported settings value: {value!r}")


def save_settings(settings: Settings, path: Path | None = None) -> None:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    values: dict[str, object] = dict(settings.extra)
    for key in _known_keys():
        values[key] = getattr(settings, key)
    text = "".join(f"{key} = {_toml_value(value)}\n" for key, value in sorted(values.items()))
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# window state: sizes, places and the last chapter of the manual (D-182). not settings, so a
# damaged or missing file only means windows open at their default place


def ui_state_path(base: Path | None = None) -> Path:
    return (base or data_dir()) / "window-state.json"


def load_ui_state(base: Path | None = None) -> dict:
    try:
        state = json.loads(ui_state_path(base).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_ui_state(state: dict, base: Path | None = None) -> None:
    path = ui_state_path(base)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # the window opens at its default place next time
