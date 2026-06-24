"""Pure selection engine: which broadcast message is due *now*.

Inputs are plain data — the catalog, the player's flat state dict (carrying the
calendar's ``event_<slug>`` flags), the wall-clock, and the per-message last-sent
timestamps. No Redis, no DB, no device — unit-tested in isolation.

A message is **due** when its trigger condition is met *and* enough time has
elapsed since it was last posted (its cooldown — and, for cron messages, at least
the cron interval). When several are due, the lowest ``(priority, id)`` wins, so a
tick posts exactly one message and the rest stay due for the next tick.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from layout.area_versions import eval_cond

from .models import TRIGGER_CRON, TRIGGER_EVENT, BroadcastMessage

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# The two cron shapes the scheduler supports (src/scheduler/runner.py). We treat
# them as cadences: "*/N * * * *" → every N minutes; "M */H * * *" → every H hours.
_EVERY_N_MIN = re.compile(r"^\*/(\d+)\s+\*\s+\*\s+\*\s+\*$")
_MIN_EVERY_H = re.compile(r"^(\d+)\s+\*/(\d+)\s+\*\s+\*\s+\*$")


def cron_interval_seconds(cron: str) -> int:
    """Cadence of a supported cron expr in seconds, or ``0`` if unsupported.

    ``0`` means "never fire" — the UI restricts authoring to the two shapes, so an
    unparseable cron is a misconfiguration that safely opts the message out.
    """
    c = (cron or "").strip()
    m = _EVERY_N_MIN.match(c)
    if m:
        n = int(m.group(1))
        return n * 60 if n > 0 else 0
    m = _MIN_EVERY_H.match(c)
    if m:
        h = int(m.group(2))
        return h * 3600 if h > 0 else 0
    return 0


def min_gap_seconds(msg: BroadcastMessage) -> int:
    """Minimum spacing before this message may repeat to one alliance.

    Also the TTL the runner stamps the cooldown key with after a post, so the
    next due-check honours exactly this spacing.
    """
    gap = max(0, int(msg.cooldown_minutes)) * 60
    if msg.trigger_kind == TRIGGER_CRON:
        gap = max(gap, cron_interval_seconds(msg.cron))
    return gap


def message_due(
    msg: BroadcastMessage,
    flat_state: Mapping[str, object],
    now: float,
    last_ts: float | None,
) -> bool:
    """True when ``msg`` should be posted at ``now`` (cooldown + trigger met)."""
    if not msg.enabled:
        return False
    if msg.trigger_kind == TRIGGER_CRON and cron_interval_seconds(msg.cron) <= 0:
        return False  # invalid/unsupported cron — never fire
    gap = min_gap_seconds(msg)
    if last_ts is not None and (float(now) - float(last_ts)) < gap:
        return False
    if msg.trigger_kind == TRIGGER_EVENT:
        return eval_cond(msg.cond, dict(flat_state))
    return msg.trigger_kind == TRIGGER_CRON


def select_due_message(
    messages: Sequence[BroadcastMessage],
    flat_state: Mapping[str, object],
    now: float,
    last_sent: Mapping[str, float | None],
    game: str,
) -> BroadcastMessage | None:
    """The single highest-priority message due for ``game`` at ``now``, or ``None``.

    Tie-break is ``(priority, id)`` — lower priority number wins, then lexical id —
    so selection is deterministic and stable across ticks.
    """
    due = [
        m
        for m in messages
        if m.applies_to_game(game)
        and message_due(m, flat_state, now, last_sent.get(m.id))
    ]
    if not due:
        return None
    return min(due, key=lambda m: (int(m.priority), m.id))
