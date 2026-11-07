"""format policy: which track goes to which pass (PLAN.md section 7)."""

from __future__ import annotations

from neonseek.config import LOSSLESS, Settings
from neonseek.db.repo import (
    DONE,
    EXCLUDED,
    FAILED,
    NOT_FOUND,
    PENDING,
    STAGE_FALLBACK,
    STAGE_FLAC,
)


def waiting_for_fallback(status: str, stage: str, attempts: int, settings: Settings) -> bool:
    """the one condition for "waits for the wav/aiff pass". does not depend on the selection
    of collections (D-005), the table, the report and pass 2 all use it."""
    return (
        settings.on_missing_flac == LOSSLESS
        and stage == STAGE_FLAC
        and (status == NOT_FOUND or (status == FAILED and attempts >= settings.max_attempts))
    )


def _open(status: str, attempts: int, settings: Settings) -> bool:
    return status == PENDING or (status == FAILED and attempts < settings.max_attempts)


def wants_pass1(status: str, stage: str, attempts: int, settings: Settings) -> bool:
    return stage == STAGE_FLAC and _open(status, attempts, settings)


def wants_pass2(status: str, stage: str, attempts: int, settings: Settings) -> bool:
    """in pass 2 now, or after the move to the fallback stage at its start."""
    if settings.on_missing_flac != LOSSLESS:
        return False
    if stage == STAGE_FALLBACK:
        return _open(status, attempts, settings)
    return waiting_for_fallback(status, stage, attempts, settings)


def in_fallback_pass(status: str, stage: str, attempts: int, settings: Settings) -> bool:
    return stage == STAGE_FALLBACK and _open(status, attempts, settings)


def state_for_own_download(
    status: str, stage: str, attempts: int, excluded_from: str | None
) -> tuple[str, str, int] | None:
    """the state a chosen track gets right before its own download (D-125): an excluded
    track comes back, not found and failed start again from flac like a retry from the
    report, pending stays. none for a downloaded track, there is nothing to do."""
    if status == EXCLUDED:
        status = excluded_from or PENDING
    if status == DONE:
        return None
    if status in (NOT_FOUND, FAILED):
        return PENDING, STAGE_FLAC, 0
    return PENDING, stage, attempts
