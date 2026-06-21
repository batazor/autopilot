"""Arena of Glory reward-window timing (Whiteout Survival **beta** ruleset).

Arena point *returns* swing with the server clock: attacking early in the day
banks far more points than the same win late at night. The bot can't change
*how* it fights — points are only gained on attack and never lost on defeat, so
every challenge is worth taking — but it CAN choose *when* to spend the daily
challenges. This module is the pure "what return am I on right now / should I
spend?" calculator a scheduler or the coordinator consults.

Three return tiers across the server day (all boundaries are **UTC+8**, the
arena's stated reference clock):

    00:00 - 22:00   HIGHER   spend here — max points
    22:00 - 23:30   NORMAL   still fine
    23:30 - 24:00   REDUCED  use-or-lose only (challenges reset at 24:00)

NOTE — the **beta** server differs from the live/standard ruleset only at the
normal->reduced cut: live drops to REDUCED at **23:00**, beta holds NORMAL until
**23:30**. Both sets are defined below; :data:`ACTIVE_WINDOWS` selects beta.

Pure: a UTC ``datetime`` in, plain values out — unit-tested without a clock. The
live "how many challenges are left" reader and the navigate-and-fight dispatch
are the consumers (deferred); this module only answers "what tier / fight now?".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

# Arena returns are quoted in UTC+8 (stated on the in-game guide). Real instants
# are shifted by this offset to read the server wall clock.
SERVER_UTC_OFFSET = timedelta(hours=8)

MINUTES_PER_DAY = 24 * 60


class Tier(StrEnum):
    """Return tier for an arena attack at a given server time."""

    HIGHER = "higher"    # increased returns — the window to spend in
    NORMAL = "normal"    # standard returns
    REDUCED = "reduced"  # cut returns, just before the daily reset


# (start_minute_of_day, end_minute_of_day, tier), half-open [start, end), in
# UTC+8 minutes from local midnight. Each set MUST tile [0, MINUTES_PER_DAY)
# with no gaps or overlaps.
BETA_WINDOWS: tuple[tuple[int, int, Tier], ...] = (
    (0,             22 * 60,        Tier.HIGHER),   # 00:00-22:00
    (22 * 60,       23 * 60 + 30,   Tier.NORMAL),   # 22:00-23:30
    (23 * 60 + 30,  MINUTES_PER_DAY, Tier.REDUCED),  # 23:30-24:00
)

# Live/standard ruleset — kept for reference and an easy switch. Differs from
# beta only in the normal->reduced cut (23:00 vs beta's 23:30).
STANDARD_WINDOWS: tuple[tuple[int, int, Tier], ...] = (
    (0,         22 * 60,        Tier.HIGHER),   # 00:00-22:00
    (22 * 60,   23 * 60,        Tier.NORMAL),   # 22:00-23:00
    (23 * 60,   MINUTES_PER_DAY, Tier.REDUCED),  # 23:00-24:00
)

# The bot targets the beta server.
ACTIVE_WINDOWS = BETA_WINDOWS


def server_local(now_utc: datetime) -> datetime:
    """Real UTC instant -> arena server-local (UTC+8) wall clock.

    A naive datetime is assumed to already be UTC. Only ``.hour``/``.minute`` of
    the result are meaningful (the tzinfo stays UTC after the manual shift).
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    return now_utc.astimezone(UTC) + SERVER_UTC_OFFSET


def minute_of_day(now_utc: datetime) -> int:
    """UTC+8 minutes since local midnight (0..1439) for a real UTC instant."""
    local = server_local(now_utc)
    return local.hour * 60 + local.minute


@dataclass(frozen=True, slots=True)
class WindowStatus:
    """Where ``now`` falls in the arena reward day."""

    tier: Tier
    minutes_into_day: int          # 0..1439, UTC+8
    minutes_until_next_tier: int   # to the next boundary (== until reset in REDUCED)
    minutes_until_reset: int       # to 24:00 UTC+8 (daily challenge reset)

    @property
    def is_high_return(self) -> bool:
        return self.tier is Tier.HIGHER

    @property
    def is_reduced(self) -> bool:
        return self.tier is Tier.REDUCED


def _window_at(
    minute: int,
    windows: tuple[tuple[int, int, Tier], ...],
) -> tuple[int, int, Tier]:
    """The ``(start, end, tier)`` window covering a UTC+8 minute-of-day."""
    for start, end, tier in windows:
        if start <= minute < end:
            return start, end, tier
    # The window sets tile the whole day, so this is unreachable for a valid
    # minute-of-day; guard against a malformed custom set rather than mis-tier.
    msg = f"no arena window covers UTC+8 minute {minute}"
    raise ValueError(msg)


def tier_at_minute(
    minute: int,
    windows: tuple[tuple[int, int, Tier], ...] = ACTIVE_WINDOWS,
) -> Tier:
    """Reward tier for a UTC+8 minute-of-day (0..1439). Used by the day planner
    to tier each scheduled slot without building a datetime."""
    return _window_at(minute, windows)[2]


def classify(
    now_utc: datetime,
    windows: tuple[tuple[int, int, Tier], ...] = ACTIVE_WINDOWS,
) -> WindowStatus:
    """Classify a real UTC instant into its arena return tier (default: beta)."""
    m = minute_of_day(now_utc)
    _start, end, tier = _window_at(m, windows)
    return WindowStatus(
        tier=tier,
        minutes_into_day=m,
        minutes_until_next_tier=end - m,
        minutes_until_reset=MINUTES_PER_DAY - m,
    )


@dataclass(frozen=True, slots=True)
class FightDecision:
    """Whether to spend an arena challenge now, and why."""

    fight: bool
    tier: Tier
    reason: str
    minutes_until_reset: int


def should_fight(
    now_utc: datetime,
    challenges_remaining: int,
    *,
    windows: tuple[tuple[int, int, Tier], ...] = ACTIVE_WINDOWS,
    skip_reduced: bool = False,
) -> FightDecision:
    """Should the bot spend an arena challenge right now?

    Points are only gained on attack and never lost, so the default is *fight
    whenever a challenge is available*. Because the bot runs continuously it
    naturally spends them during the HIGHER window each day (max returns);
    REDUCED is only ever reached as a use-or-lose safety net before the 24:00
    reset.

    ``skip_reduced=True`` forgoes the dwindling late-night returns — but this
    *forfeits* those challenges (they reset at 24:00, worth zero), so it is off
    by default.
    """
    status = classify(now_utc, windows)
    if challenges_remaining <= 0:
        return FightDecision(False, status.tier, "no_challenges", status.minutes_until_reset)
    if status.tier is Tier.REDUCED and skip_reduced:
        return FightDecision(False, status.tier, "skip_reduced_window", status.minutes_until_reset)
    return FightDecision(True, status.tier, f"fight_{status.tier.value}_window", status.minutes_until_reset)


def high_return_window(
    windows: tuple[tuple[int, int, Tier], ...] = ACTIVE_WINDOWS,
) -> tuple[int, int]:
    """(start_min, end_min) UTC+8 of the HIGHER-return window.

    The ideal slot to schedule the daily arena spend; the in-game guide's
    "battle before 22:00" recommendation is exactly this window's end.
    """
    for start, end, tier in windows:
        if tier is Tier.HIGHER:
            return (start, end)
    msg = "no HIGHER window defined"
    raise ValueError(msg)
