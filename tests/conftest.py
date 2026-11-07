from __future__ import annotations

import itertools
import json
import os
import sys
from pathlib import Path

import httpx
import keyring
import pytest
import yt_dlp
from keyring.backend import KeyringBackend

# download runs on the pinned sockseek with a mock library
from download_env import env  # noqa: F401
from fake_bin import FakeRecycleBin
from neonseek import actions, config, i18n, recycle
from neonseek.db import repo as db
from neonseek.engine import sockseek
from neonseek.sources import web_url
from spotify_fake import VARIANTS, FakeSpotify, MemoryTokenStore
from web_fake import PLAYLIST_ID, FakeApple, soundcloud_extractor, text, youtube_extractor

# gui tests never open a window on the screen
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_SOCKSEEK = Path(__file__).resolve().parent / "fake_sockseek.py"

# fake sockseek answers to an empty login list (docs/sockseek-notes.md, section 11.1)
LOGIN_OK = [
    {"err": "[soulseek] Logging in as someone.."},
    {"err": "[debug] [jobs] Soulseek services initialized"},
    {"exit": 0},
]
LOGIN_REJECTED = [
    {"err": "[error] [cli] Soulseek login failed: The server rejected login attempt: INVALIDPASS"},
    {"sleep": 30},
]


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.values:
            raise keyring.errors.PasswordDeleteError(username)
        del self.values[(service, username)]


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"tests must not reach the network: {request.url}")


def _no_ytdlp(*args, **kwargs):
    raise AssertionError("tests must not reach youtube or soundcloud: use web_fake.FakeExtractor")


def _no_ytdlp_network(self, request):
    url = getattr(request, "url", request)
    raise AssertionError(f"tests must not reach the network through yt-dlp: {url}")


_real_start_process = sockseek.start_process


def _offline_sockseek(command: list[str]):
    # the fake process is python, the real sockseek.exe may only run on a mock library
    fake = Path(command[0]).name.lower().startswith("python")
    if not fake and "--mock-files-dir" not in command:
        raise AssertionError("tests must not reach soulseek: sockseek ran without a mock")
    return _real_start_process(command)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    # nothing in the tests may touch the real %APPDATA%\NeonSeek, credential manager or spotify
    monkeypatch.setenv(config.DATA_DIR_ENV, str(tmp_path / "appdata"))
    previous = keyring.get_keyring()
    memory = MemoryKeyring()
    keyring.set_keyring(memory)
    monkeypatch.setattr(
        actions, "new_http_client", lambda: httpx.Client(transport=httpx.MockTransport(_no_network))
    )
    # apple music, youtube and soundcloud only through saved answers (tests/web_fake.py)
    monkeypatch.setattr(
        actions, "new_web_client", lambda: httpx.Client(transport=httpx.MockTransport(_no_network))
    )
    monkeypatch.setattr(actions, "new_ytdlp", _no_ytdlp)
    monkeypatch.setattr(yt_dlp.YoutubeDL, "urlopen", _no_ytdlp_network)
    monkeypatch.setattr(sockseek, "start_process", _offline_sockseek)
    # a mock library makes no real searches, the limit across processes would only slow tests
    monkeypatch.setattr(actions.AppContext, "search_window_s", 0.0)
    yield memory
    keyring.set_keyring(previous)


def _real_bin(path):
    raise AssertionError(f"tests must not reach the windows recycle bin: {path}")


@pytest.fixture(autouse=True)
def _english(monkeypatch):
    """tests read english texts, also on a polish windows. a test that switches the language
    gets english back afterwards (D-163)."""
    monkeypatch.setattr(i18n, "detect_language", lambda: i18n.ENGLISH)
    i18n.set_language(i18n.ENGLISH)
    yield
    i18n.set_language(i18n.ENGLISH)


@pytest.fixture(autouse=True)
def recycle_bin(tmp_path, monkeypatch) -> FakeRecycleBin:
    """every app context of a test deletes into this folder, never into the real bin."""
    fake = FakeRecycleBin(tmp_path / "recycle-bin")
    monkeypatch.setattr(recycle, "default_bin", lambda: fake)
    monkeypatch.setattr(recycle, "shell_delete", _real_bin)
    return fake


@pytest.fixture
def fake_sockseek(tmp_path, monkeypatch):
    """points every app context at the fake sockseek process. call it with the steps."""
    counter = itertools.count()

    def use(steps: list[dict]) -> Path:
        script = tmp_path / f"fake-sockseek-{next(counter)}.json"
        script.write_text(json.dumps(steps), encoding="utf-8")
        command = [sys.executable, str(FAKE_SOCKSEEK), str(script)]
        monkeypatch.setattr(actions.AppContext, "sockseek_command", command)
        return script

    return use


@pytest.fixture
def qapp(qapp):
    """the qt app of pytest-qt with the bundled font, so sizes match the real window."""
    from neonseek.ui import wheel
    from neonseek.ui.theme.loader import load_fonts

    load_fonts()
    wheel.install(qapp)
    return qapp


@pytest.fixture
def csv_dir() -> Path:
    return FIXTURES / "csv"


@pytest.fixture
def repo(tmp_path) -> db.Repo:
    conn = db.connect(tmp_path / "test.db")
    yield db.Repo(conn)
    conn.close()


@pytest.fixture
def ctx(tmp_path) -> actions.AppContext:
    context = actions.AppContext(tmp_path / "data")
    yield context
    context.close()


@pytest.fixture
def apple() -> FakeApple:
    fake = FakeApple()
    fake.add_page(f"/pl/playlist/{PLAYLIST_ID}", text("apple_playlist.html"))
    return fake


@pytest.fixture
def web(ctx, apple, monkeypatch):
    """the app context with the fake apple music and a fake yt-dlp for both services."""
    youtube, soundcloud = youtube_extractor(), soundcloud_extractor()
    both = youtube
    both.answers.update(soundcloud.answers)
    both.tracks = soundcloud.tracks
    sleeps: list[float] = []
    ctx.web_http_factory = apple.client
    ctx.ytdlp_factory = lambda cache_dir, log: both
    # contexts made inside the cli and the gui workers get the same fakes
    monkeypatch.setattr(actions, "new_web_client", apple.client)
    monkeypatch.setattr(actions, "new_ytdlp", lambda cache_dir, log: both)
    monkeypatch.setattr(web_url.time, "sleep", sleeps.append)
    ctx.fake = both
    ctx.sleeps = sleeps
    return ctx


@pytest.fixture(params=VARIANTS)
def fake_spotify(request) -> FakeSpotify:
    """real answers of an older and of a new developer app, spotify tests run on both."""
    return FakeSpotify(request.param)


@pytest.fixture
def spotify_ctx(ctx, fake_spotify) -> actions.AppContext:
    """an app context logged in to the fake spotify."""
    ctx.http_factory = lambda: httpx.Client(transport=fake_spotify.transport())
    ctx.token_store = MemoryTokenStore("refresh-0")
    actions.set_spotify_client_id(ctx, "test-client-id")
    ctx.token_store.save("refresh-0")
    return ctx
