"""every theme has all tokens and the minimal wcag contrast (PLAN.md section 9)."""

from __future__ import annotations

import re

import pytest

from neonseek import themes
from neonseek.ui.theme import loader

SHIPPED = list(themes.BUILTIN)


@pytest.mark.parametrize("key", SHIPPED)
def test_builtin_theme_is_complete(key):
    theme = themes.builtin_theme(key)
    assert set(theme.colors) == set(themes.TOKENS)
    assert all(re.fullmatch(r"#[0-9A-F]{6}", value) for value in theme.colors.values())
    assert theme.name and theme.builtin


@pytest.mark.parametrize("key", SHIPPED)
def test_builtin_theme_contrast(key):
    assert themes.contrast_problems(themes.builtin_theme(key)) == []


@pytest.mark.parametrize("key", SHIPPED)
def test_progress_text_reads_on_the_filled_part(key):
    # the text over the gradient keeps its contrast on every point of it (D-133)
    theme = themes.builtin_theme(key)
    token = themes.progress_text_token(theme)
    assert themes.progress_text_contrast(theme, token) >= themes.PROGRESS_TEXT_MIN


def test_mono_text_on_the_bar_was_white_on_white_and_is_not_anymore():
    mono = themes.builtin_theme("mono")
    assert themes.progress_text_contrast(mono, "text.primary") < 1.5  # the old text color
    assert themes.progress_text_token(mono) != "text.primary"


def test_mono_stays_black_and_white():
    for value in themes.builtin_theme("mono").colors.values():
        assert value[1:3] == value[3:5] == value[5:7], value


def test_noir_is_black_with_colored_accents_states_and_headings():
    noir, mono = themes.builtin_theme("noir"), themes.builtin_theme("mono")
    assert "noir" in themes.BUILTIN and len(themes.BUILTIN) == 6
    for token in ("bg.base", "bg.surface", "bg.elevated"):
        assert noir.colors[token] == mono.colors[token]
    for token in ("accent.primary", "accent.secondary", "text.muted", "state.success",
                  "state.queued", "state.warning", "state.error"):  # fmt: skip
        value = noir.colors[token]
        assert not value[1:3] == value[3:5] == value[5:7], token  # a real color, not gray


def test_neon_is_the_owner_palette():
    neon = themes.builtin_theme("neon")
    assert neon.glow and neon.dark
    assert neon.colors["bg.base"] == "#1A1230" and neon.colors["accent.primary"] == "#D6278F"
    assert neon.colors["state.queued"] == "#BC5E94"  # lightened mauve


def test_contrast_math():
    assert themes.contrast("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert themes.contrast("#777777", "#777777") == pytest.approx(1.0)


def _toml(**changes) -> str:
    colors = dict(themes.builtin_theme("neon").colors)
    colors.update(changes)
    lines = ['name = "Mine"', "glow = false", "dark = true"]
    lines += [f'"{token}" = "{value}"' for token, value in colors.items() if value is not None]
    return "\n".join(lines) + "\n"


def test_quoted_dotted_keys_and_tables_both_work():
    theme = themes.parse_theme("mine", _toml())
    assert theme.name == "Mine" and not theme.glow and not theme.builtin


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("not toml [", "not valid toml"),
        (_toml(**{"state.error": None}), "missing colors: state.error"),
        (_toml(**{"bg.base": "red"}), "colors must look like"),
        (_toml().replace("glow = false", 'glow = "yes"'), "glow must be true or false"),
    ],
)
def test_broken_theme_is_rejected(text, problem):
    with pytest.raises(themes.ThemeError, match=problem):
        themes.parse_theme("x", text)


def test_user_themes_are_listed_and_broken_ones_skipped(tmp_path):
    folder = themes.user_dir(tmp_path)
    folder.mkdir()
    (folder / "Sunset.toml").write_text(_toml(), encoding="utf-8")
    (folder / "broken.toml").write_text("name = 1\n[bg", encoding="utf-8")
    (folder / "neon.toml").write_text(_toml(), encoding="utf-8")
    (folder / "bad name!.toml").write_text(_toml(), encoding="utf-8")
    logged: list[str] = []
    found = themes.load_themes(tmp_path, logged.append)
    assert "sunset" in found and not found["sunset"].builtin
    assert found["neon"].builtin  # a user file never replaces a built in theme
    assert "broken" not in found
    skipped = [line for line in logged if line.startswith("theme ")]
    assert len(skipped) == 3 and any("broken.toml" in line for line in skipped)


def test_pick_falls_back_to_the_default(tmp_path):
    found = themes.load_themes(tmp_path)
    assert themes.pick(found, "gone").key == themes.DEFAULT


def test_template_has_no_colors_of_its_own():
    text = loader.template_text()
    assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", text)
    assert not re.search(r"\brgba?\(", text)
    named = {"white", "black", "red", "green", "blue", "gray", "grey", "magenta", "cyan"}
    assert not named & set(re.findall(r"[a-z]+", text.lower()))


@pytest.mark.parametrize("key", SHIPPED)
def test_template_renders_every_placeholder(key):
    theme = themes.builtin_theme(key)
    extra = {
        **loader.size_tokens(1.5),
        "font_family": "JetBrains Mono",
        "font_size": "18px",
        "font_size_large": "26px",
        "icon_check": "c.svg",
        "icon_chevron_down": "d.svg",
    }
    qss = loader.render_qss(theme, extra)
    assert "${" not in qss and "$" not in qss
    assert theme.colors["accent.primary"] in qss


def test_every_icon_can_be_colored():
    from importlib import resources

    folder = resources.files("neonseek.ui.theme").joinpath("icons")
    names = [item.name[:-4] for item in folder.iterdir() if item.name.endswith(".svg")]
    assert names
    for name in names:
        text = loader.svg_text(name)
        assert "currentColor" in text, name
        assert not re.search(r"#[0-9A-Fa-f]{3,6}\b", text), name


def test_logo_versions_follow_the_size(qapp):
    small, full = loader.logo_svg(loader.LOGO_FULL_FROM - 1), loader.logo_svg(loader.LOGO_FULL_FROM)
    # the simplified logo has no grooves, both keep their own colors in every theme
    assert b"<path" not in small and b"<path" in full
    for svg in (small, full):
        assert b"currentColor" not in svg
        for color in (b"#1A1230", b"#D6278F", b"#5FE3D3", b"#6B1F6E"):
            assert color in svg
    sizes = {size.width() for size in loader.app_icon().availableSizes()}
    assert sizes == set(loader.LOGO_SIZES)
    pixmap = loader.logo_pixmap(56, 2.0)
    assert pixmap.width() == 112 and pixmap.devicePixelRatio() == 2.0
    assert not (loader._PACKAGE / "icons" / "terminal.svg").is_file()
