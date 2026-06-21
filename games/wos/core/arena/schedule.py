"""Multi-account Arena day plan: pack every account's Arena spend onto its
shared device inside the reward windows.

The deployment reality this exists for: **many accounts, few devices**. A
physical device runs its accounts *serially* — only one account is logged in at
a time and switching is slow — so the scarce resource is device-minutes inside
the HIGHER-return window (00:00-22:00 UTC+8; see :mod:`reward_window`). Accounts
are *pinned* to a device (``device_profile_gamers``), they can't be moved to a
freer device, so each device is an independent serial lane that must fit its own
roster.

This planner answers, for a server day: *which account fights Arena at what time
on which device*, so that as many accounts as possible land their fights in the
HIGHER window and none silently misses the 24:00 reset. When a device is
oversubscribed the tail slides HIGHER -> NORMAL -> REDUCED -> missed, which is
exactly the signal the operator needs ("this device has more accounts than fit
before 22:00 — add a device or start earlier").

Model: each device packs its accounts back-to-back from ``start_minute``; a slot
is ``per_account_minutes`` long (account switch + the daily challenge spend). A
slot's tier is read at its *start* (the optimistic edge — returns only decay
later within the slot). Devices run in parallel.

Pure: data in, a plan out — no Redis, no clock. The consumer (deferred) turns
slots into staggered ``run_at`` values when publishing Arena tasks to each
device queue (one queue per device today; tasks already carry ``player_id``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .reward_window import (
    ACTIVE_WINDOWS,
    MINUTES_PER_DAY,
    Tier,
    high_return_window,
    tier_at_minute,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# A plausible default for one account's whole Arena turn: account switch +
# relaunch settle + spending the daily free challenges + dismissing results.
# Tune per deployment; override per-account via ``account_minutes``.
DEFAULT_PER_ACCOUNT_MINUTES = 6


@dataclass(frozen=True, slots=True)
class ArenaSlot:
    """One account's reserved Arena turn on its device lane."""

    account_id: str
    device_id: str
    order: int            # position in the device lane (0-based)
    start_min: int        # UTC+8 minute-of-day the turn begins
    end_min: int          # UTC+8 minute-of-day it should finish
    tier: Tier | None     # return tier at start; None == won't finish before reset

    @property
    def missed(self) -> bool:
        """The turn doesn't fit before the 24:00 reset — Arena is forfeited today."""
        return self.tier is None


@dataclass(frozen=True, slots=True)
class ArenaDayPlan:
    """The full per-device packing for one server day."""

    slots: tuple[ArenaSlot, ...]

    def counts(self) -> dict[str, int]:
        """Slot counts keyed by tier value plus ``"missed"`` — the at-a-glance health."""
        out = {t.value: 0 for t in Tier}
        out["missed"] = 0
        for s in self.slots:
            out["missed" if s.missed else s.tier.value] += 1  # type: ignore[union-attr]
        return out

    def missed(self) -> tuple[ArenaSlot, ...]:
        """Accounts that won't get an Arena turn before reset (oversubscribed)."""
        return tuple(s for s in self.slots if s.missed)

    def on_time(self) -> tuple[ArenaSlot, ...]:
        """Accounts whose turn lands in the HIGHER-return window."""
        return tuple(s for s in self.slots if s.tier is Tier.HIGHER)

    def capacity_ok(self) -> bool:
        """True when every account fits before the daily reset (nobody missed)."""
        return not any(s.missed for s in self.slots)

    def bottleneck_device(self) -> str | None:
        """Device whose lane finishes latest — the one to relieve first (or None)."""
        if not self.slots:
            return None
        return max(self.slots, key=lambda s: s.end_min).device_id


def plan_arena_day(
    devices: Mapping[str, Sequence[str]],
    *,
    per_account_minutes: int = DEFAULT_PER_ACCOUNT_MINUTES,
    account_minutes: Mapping[str, int] | None = None,
    start_minute: int = 0,
    windows: tuple[tuple[int, int, Tier], ...] = ACTIVE_WINDOWS,
) -> ArenaDayPlan:
    """Pack each device's pinned accounts into serial Arena turns.

    ``devices`` maps a device id to the ordered accounts that share it. ``order``
    is the spend order on that device (put the accounts you care about most
    first — they get the earliest, highest-return slots). ``start_minute`` is the
    UTC+8 minute the day's packing begins (0 = server midnight; pass the current
    minute-of-day for a mid-day replan). Per-account durations override the
    default via ``account_minutes``.
    """
    if per_account_minutes <= 0:
        msg = "per_account_minutes must be positive"
        raise ValueError(msg)

    slots: list[ArenaSlot] = []
    for device_id, accounts in devices.items():
        cursor = start_minute
        for order, account_id in enumerate(accounts):
            dur = (account_minutes or {}).get(account_id, per_account_minutes)
            start, end = cursor, cursor + dur
            # Tier only meaningful when the whole turn fits before reset; past
            # that the account simply doesn't get its Arena in today.
            tier = tier_at_minute(start, windows) if end <= MINUTES_PER_DAY else None
            slots.append(ArenaSlot(account_id, device_id, order, start, end, tier))
            cursor = end
    return ArenaDayPlan(tuple(slots))


def high_return_capacity(
    per_account_minutes: int = DEFAULT_PER_ACCOUNT_MINUTES,
    *,
    start_minute: int = 0,
    windows: tuple[tuple[int, int, Tier], ...] = ACTIVE_WINDOWS,
) -> int:
    """Max accounts one device can fit in the HIGHER-return window.

    The headline capacity number: accounts per device beyond this spill into
    NORMAL/REDUCED returns. Compare against your largest device roster to know
    whether you're over capacity.
    """
    if per_account_minutes <= 0:
        msg = "per_account_minutes must be positive"
        raise ValueError(msg)
    _, high_end = high_return_window(windows)
    return max(0, (high_end - start_minute) // per_account_minutes)
