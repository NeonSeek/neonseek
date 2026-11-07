from __future__ import annotations

from pathlib import Path

import pytest

from neonseek import config
from neonseek.errors import ConfigError


def test_data_dir_override(tmp_path, monkeypatch):
    monkeypatch.setenv(config.DATA_DIR_ENV, str(tmp_path))
    assert config.data_dir() == tmp_path
    assert config.db_path() == tmp_path / "neonseek.db"


def test_default_data_dir_is_roaming_appdata(monkeypatch):
    monkeypatch.delenv(config.DATA_DIR_ENV, raising=False)
    path = config.data_dir()
    assert path.name == "NeonSeek"
    assert "Roaming" in path.parts


def test_missing_settings_give_defaults(tmp_path):
    assert config.load_settings(tmp_path / "settings.toml") == config.Settings()


def test_settings_roundtrip_keeps_unknown_keys(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('future_flag = "on"\nfuture_limit = 3\n', encoding="utf-8")
    settings = config.load_settings(path)
    settings.spotify_client_id = 'abc"\\ź'
    settings.library_dir = "D:\\Music\\Neon ź"
    settings.on_missing_flac = config.NOT_FOUND
    settings.max_attempts = 5
    config.save_settings(settings, path)
    again = config.load_settings(path)
    assert again.spotify_client_id == 'abc"\\ź'
    assert again.library_dir == "D:\\Music\\Neon ź"
    assert again.on_missing_flac == config.NOT_FOUND
    assert again.max_attempts == 5 and again.idle_timeout_minutes == 10
    assert again.extra == {"future_flag": "on", "future_limit": 3}


def test_bad_values_fall_back_to_defaults(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(
        'on_missing_flac = "mp3"\nmax_attempts = 0\nidle_timeout_minutes = "ten"\n',
        encoding="utf-8",
    )
    settings = config.load_settings(path)
    assert settings.on_missing_flac == config.LOSSLESS
    assert settings.max_attempts == 3 and settings.idle_timeout_minutes == 10
    with pytest.raises(ConfigError, match="between 5 and"):
        config.check_int_setting("idle_timeout_minutes", 1)


def test_damaged_settings_say_what_to_do(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text("this is = = not toml", encoding="utf-8")
    with pytest.raises(ConfigError, match="delete it"):
        config.load_settings(path)


def test_no_owner_paths_in_source():
    # the app is handed out to others, no developer paths may be baked in
    package = Path(config.__file__).parent
    for path in package.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        assert "c:\\users\\" not in text and "c:/users/" not in text, path
