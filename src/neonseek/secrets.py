"""secrets live only in windows credential manager through keyring, never in files or logs."""

from __future__ import annotations

import secrets as _random
import string
from dataclasses import dataclass, field

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from neonseek.errors import NeonSeekError
from neonseek.i18n import tr

SERVICE = "NeonSeek"
SPOTIFY_REFRESH_TOKEN = "spotify-refresh-token"
SOULSEEK_USERNAME = "soulseek-username"
SOULSEEK_PASSWORD = "soulseek-password"


class SecretStoreError(NeonSeekError):
    pass


@dataclass(frozen=True)
class SoulseekLogin:
    username: str
    # kept out of repr so a logged object never shows it
    password: str = field(repr=False)


def _failed(exc: Exception) -> SecretStoreError:
    return SecretStoreError(
        tr(
            "Could not use Windows Credential Manager ({error}). Restart Windows and try "
            "again. If it keeps failing, check that the Credential Manager service is running."
        ).format(error=type(exc).__name__)
    )


def get_secret(name: str) -> str | None:
    try:
        return keyring.get_password(SERVICE, name)
    except KeyringError as exc:
        raise _failed(exc) from exc


def set_secret(name: str, value: str) -> None:
    try:
        keyring.set_password(SERVICE, name, value)
    except KeyringError as exc:
        raise _failed(exc) from exc


def delete_secret(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except PasswordDeleteError:
        pass  # nothing stored, nothing to delete
    except KeyringError as exc:
        raise _failed(exc) from exc


USERNAME_MAX_LENGTH = 30
GENERATED_PASSWORD_LENGTH = 32
_PASSWORD_ALPHABET = string.ascii_letters + string.digits


def generate_password() -> str:
    """password for a new soulseek account. letters and digits only, nothing to escape."""
    return "".join(_random.choice(_PASSWORD_ALPHABET) for _ in range(GENERATED_PASSWORD_LENGTH))


def username_problem(username: str) -> str | None:
    """what the soulseek server would reject in this name (nicotine+ protocol docs, D-054)."""
    if not username:
        return tr("Enter a Soulseek username.")
    if len(username) > USERNAME_MAX_LENGTH:
        return tr("A Soulseek username can have at most {count} characters.").format(
            count=USERNAME_MAX_LENGTH
        )
    if any(not " " <= ch <= "~" for ch in username):
        return tr(
            "A Soulseek username can only use plain English letters, digits, spaces and "
            "simple symbols, no accents or emoji."
        )
    if username != username.strip(" "):
        return tr("A Soulseek username cannot start or end with a space.")
    return None


def get_soulseek_login() -> SoulseekLogin | None:
    username = get_secret(SOULSEEK_USERNAME)
    password = get_secret(SOULSEEK_PASSWORD)
    if not username or not password:
        return None
    return SoulseekLogin(username, password)


def set_soulseek_login(login: SoulseekLogin) -> None:
    set_secret(SOULSEEK_USERNAME, login.username)
    set_secret(SOULSEEK_PASSWORD, login.password)


def delete_soulseek_login() -> None:
    delete_secret(SOULSEEK_PASSWORD)
    delete_secret(SOULSEEK_USERNAME)
