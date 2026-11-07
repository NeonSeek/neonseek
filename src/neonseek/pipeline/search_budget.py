"""keeps the soulseek search limit across sockseek processes.

sockseek allows 34 searches per 220 s, but each process counts only its own. pass 2 or the next
run starting right after a pass would get a fresh 34 and could double the rate, which the server
punishes with a 30 minute ban. so the searches of every process are kept in the database and a
new process starts only when it cannot go over the limit (D-070).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# sockseek defaults (--searches-per-time, --searches-renew-time), never raised
SEARCHES_PER_TIME = 34
RENEW_S = 220.0


@dataclass(frozen=True)
class StartPlan:
    wait_s: float  # wait this long before starting the process
    # lower --searches-per-time for this process, none keeps the default
    searches_per_time: int | None = None


def plan_start(
    recent: list[float],
    now: float,
    tracks: int,
    limit: int = SEARCHES_PER_TIME,
    window_s: float = RENEW_S,
) -> StartPlan:
    """recent: times of searches in the last window. tracks: searches the new process needs."""
    used = [t for t in recent if t > now - window_s]
    if not used or window_s <= 0:
        return StartPlan(0.0)
    left = limit - len(used)
    if left > 0 and tracks <= left:
        # everything fits in what is left of this window, the process gets only that much
        return StartPlan(0.0, left)
    # nothing left, or not enough: wait until the window has no searches, then the process
    # gets the whole default. a process never starts with a limit of 0
    return StartPlan(max(0.0, max(used) + window_s - now))


def search_estimate_s(tracks: int, limit: int = SEARCHES_PER_TIME, window_s: float = RENEW_S):
    """upper estimate of the search time of pass 1 shown before a start (D-045)."""
    return math.ceil(tracks / limit) * int(window_s) if tracks else 0
