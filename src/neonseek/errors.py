"""errors shown to the user. the message always says what to do next."""

from __future__ import annotations


class NeonSeekError(Exception):
    """base for errors whose message is meant for the user."""

    @property
    def message(self) -> str:
        return str(self.args[0]) if self.args else ""


class ConfigError(NeonSeekError):
    pass


class DatabaseError(NeonSeekError):
    pass


class CollectionError(NeonSeekError):
    pass
