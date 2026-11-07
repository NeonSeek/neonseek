"""soulseek account through the cli, on the fake sockseek process. nothing reaches soulseek."""

from __future__ import annotations

import getpass
import json

import pytest

from conftest import LOGIN_OK, LOGIN_REJECTED
from neonseek import secrets
from neonseek.cli import main


def _answers(monkeypatch, *inputs: str, passwords: tuple[str, ...] = ()) -> None:
    typed = list(inputs)
    monkeypatch.setattr("builtins.input", lambda prompt="": typed.pop(0))
    hidden = list(passwords)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": hidden.pop(0))


def _sent(script) -> list[str]:
    return json.loads(script.with_suffix(".args.json").read_text(encoding="utf-8"))


def _run(capsys, tmp_path, *argv) -> tuple[int, str, str]:
    code = main(["--data-dir", str(tmp_path), *argv])
    out, err = capsys.readouterr()
    return code, out, err


def test_new_account_asks_only_for_a_name(capsys, monkeypatch, tmp_path, fake_sockseek):
    script = fake_sockseek(LOGIN_OK)
    _answers(monkeypatch, "  my-dj-name ")
    code, out, err = _run(capsys, tmp_path, "soulseek", "login", "--new")
    assert code == 0, err
    login = secrets.get_soulseek_login()
    assert login.username == "my-dj-name"
    assert len(login.password) == secrets.GENERATED_PASSWORD_LENGTH
    assert login.password.isalnum()
    # the generated password went to the login once and is never shown
    args = _sent(script)
    assert f"--pass={login.password}" in args and "--no-listen" in args
    assert login.password not in out + err
    assert 'Created the Soulseek account "my-dj-name"' in out
    assert not any(tmp_path.rglob("*.toml"))


def test_generated_passwords_differ():
    assert len({secrets.generate_password() for _ in range(20)}) == 20


def test_taken_name_asks_for_another_without_retry(capsys, monkeypatch, tmp_path, fake_sockseek):
    script = fake_sockseek(LOGIN_REJECTED)
    _answers(monkeypatch, "taken-name")
    code, _, err = _run(capsys, tmp_path, "soulseek", "login", "--new")
    assert code == 1
    assert '"taken-name" is already taken' in err and "Choose another name" in err
    assert "Nothing was saved" in err
    assert secrets.get_soulseek_login() is None
    # one login attempt, no second name tried
    assert len(list(tmp_path.glob("fake-sockseek-*.args.json"))) == 1
    assert "--user=taken-name" in _sent(script)


@pytest.mark.parametrize(
    "name, problem",
    [
        ("", "Enter a Soulseek username"),
        ("x" * 31, "at most 30"),
        ("Zażółć", "no accents"),
        ("emoji 🎧", "no accents"),
    ],
)
def test_bad_names_never_reach_the_server(capsys, monkeypatch, tmp_path, name, problem):
    # no fake process: a check would hit the guard in conftest
    _answers(monkeypatch, name)
    code, _, err = _run(capsys, tmp_path, "soulseek", "login", "--new")
    assert code == 1 and problem in err
    assert secrets.get_soulseek_login() is None


def test_name_rules():
    assert secrets.username_problem(" lead") and secrets.username_problem("trail ")
    assert secrets.username_problem("x" * 30) is None
    assert secrets.username_problem("DJ ~ (ok) #1") is None


def test_own_account_saves_after_check(capsys, monkeypatch, tmp_path, fake_sockseek, _isolated):
    script = fake_sockseek(LOGIN_OK)
    _answers(monkeypatch, "  dj-user ", passwords=("-pa ss=word", "-pa ss=word"))
    code, out, err = _run(capsys, tmp_path, "soulseek", "login")
    assert code == 0, err
    assert 'as "dj-user"' in out
    assert "-pa ss=word" not in out + err
    assert "--pass=-pa ss=word" in _sent(script)
    assert _isolated.values[(secrets.SERVICE, secrets.SOULSEEK_USERNAME)] == "dj-user"
    assert _isolated.values[(secrets.SERVICE, secrets.SOULSEEK_PASSWORD)] == "-pa ss=word"
    code, out, _ = _run(capsys, tmp_path, "soulseek", "status")
    assert code == 0 and "dj-user" in out and "word" not in out


def test_own_account_with_password_like_the_name_warns(
    capsys, monkeypatch, tmp_path, fake_sockseek
):
    fake_sockseek(LOGIN_OK)
    _answers(monkeypatch, "SameName", passwords=("samename", "samename"))
    code, _, err = _run(capsys, tmp_path, "soulseek", "login")
    assert code == 0
    assert "Warning:" in err and "same as your username" in err


def test_own_account_rejected_saves_nothing(capsys, monkeypatch, tmp_path, fake_sockseek):
    fake_sockseek(LOGIN_REJECTED)
    _answers(monkeypatch, "dj-user", passwords=("wrong", "wrong"))
    code, _, err = _run(capsys, tmp_path, "soulseek", "login")
    assert code == 1 and "did not accept this password" in err
    assert secrets.get_soulseek_login() is None


def test_mismatch_and_cancel_save_nothing(capsys, monkeypatch, tmp_path):
    _answers(monkeypatch, "dj-user", passwords=("one", "two"))
    code, _, err = _run(capsys, tmp_path, "soulseek", "login")
    assert code == 1 and "different" in err

    def cancel(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", cancel)
    code, _, err = _run(capsys, tmp_path, "soulseek", "login")
    assert code == 1 and "nothing was saved" in err
    assert secrets.get_soulseek_login() is None


def test_replacing_a_saved_login_asks_first(capsys, monkeypatch, tmp_path, fake_sockseek):
    secrets.set_soulseek_login(secrets.SoulseekLogin("old-user", "old-pass"))
    _answers(monkeypatch, "n")
    code, _, err = _run(capsys, tmp_path, "soulseek", "login", "--new")
    assert code == 1 and "nothing was saved" in err
    assert secrets.get_soulseek_login().username == "old-user"

    fake_sockseek(LOGIN_OK)
    _answers(monkeypatch, "y", "new-user")
    code, _, err = _run(capsys, tmp_path, "soulseek", "login", "--new")
    assert code == 0, err
    assert secrets.get_soulseek_login().username == "new-user"


def test_test_command_and_logout(capsys, monkeypatch, tmp_path, fake_sockseek):
    code, _, err = _run(capsys, tmp_path, "soulseek", "test")
    assert code == 1 and "soulseek login --new" in err

    secrets.set_soulseek_login(secrets.SoulseekLogin("me", "secret-pass"))
    fake_sockseek(LOGIN_OK)
    code, out, _ = _run(capsys, tmp_path, "soulseek", "test")
    assert code == 0 and 'works for "me"' in out

    fake_sockseek(LOGIN_REJECTED)
    code, _, err = _run(capsys, tmp_path, "soulseek", "test")
    assert code == 1 and "did not accept this password" in err

    assert main(["--data-dir", str(tmp_path), "soulseek", "logout"]) == 0
    assert secrets.get_soulseek_login() is None
