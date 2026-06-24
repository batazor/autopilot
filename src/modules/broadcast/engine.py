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

    # Decoded calendar snapshot the runner passes in (plain data, game-agnostic):
    #   {"upcoming": [{"slug","name","in_hours","starts"}, ...],
    #    "active":   [{"slug","name","ends"}, ...]}
    CalendarCtx = Mapping[str, list[dict]]

# The two cron shapes the scheduler supports (src/scheduler/runner.py). We treat
# them as cadences: "*/N * * * *" → every N minutes; "M */H * * *" → every H hours.
_EVERY_N_MIN = re.compile(r"^\*/(\d+)\s+\*\s+\*\s+\*\s+\*$")
_MIN_EVERY_H = re.compile(r"^(\d+)\s+\*/(\d+)\s+\*\s+\*\s+\*$")

# Pull the event slug out of an event-trigger cond like "event_bear_hunt == 1".
_EVENT_SLUG_RE = re.compile(r"event_([a-z0-9_]+)")


def event_slug_from_cond(cond: str) -> str:
    """The ``<slug>`` from a ``event_<slug> ...`` cond, or ``""`` if none."""
    m = _EVENT_SLUG_RE.search(cond or "")
    return m.group(1) if m else ""


def upcoming_match(msg: BroadcastMessage, calendar_ctx: CalendarCtx | None) -> dict | None:
    """The nearest upcoming event matching a pre-event message within its lead.

    Returns the calendar entry (``{slug,name,in_hours,starts}``) so the runner can
    template ``{event}``/``{in_hours}`` from it, or ``None`` when nothing qualifies.
    """
    if not calendar_ctx:
        return None
    target = event_slug_from_cond(msg.cond)
    if not target:
        return None
    best: dict | None = None
    for ev in calendar_ctx.get("upcoming") or []:
        if ev.get("slug") != target:
            continue
        ih = ev.get("in_hours")
        if ih is None:
            continue
        if 0.0 <= float(ih) <= float(msg.lead_hours) and (
            best is None or float(ih) < float(best["in_hours"])
        ):
            best = ev
    return best


def in_quiet_hours(msg: BroadcastMessage, server_minutes: int | None) -> bool:
    """True when ``server_minutes`` (UTC+8 minute-of-day) falls in the quiet window."""
    if server_minutes is None:
        return False
    start, end = int(msg.quiet_start_hour), int(msg.quiet_end_hour)
    if start < 0 or end < 0 or start == end:
        return False
    cur_h = (int(server_minutes) // 60) % 24
    start, end = start % 24, end % 24
    if start < end:
        return start <= cur_h < end
    return cur_h >= start or cur_h < end  # window wraps past midnight (e.g. 22→6)


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
    *,
    calendar_ctx: CalendarCtx | None = None,
    server_minutes: int | None = None,
) -> bool:
    """True when ``msg`` should be posted at ``now`` (cooldown + trigger + quiet hours)."""
    if not msg.enabled:
        return False
    if in_quiet_hours(msg, server_minutes):
        return False
    if msg.trigger_kind == TRIGGER_CRON and cron_interval_seconds(msg.cron) <= 0:
        return False  # invalid/unsupported cron — never fire
    gap = min_gap_seconds(msg)
    if last_ts is not None and (float(now) - float(last_ts)) < gap:
        return False
    if msg.trigger_kind == TRIGGER_EVENT:
        if msg.is_pre_event():
            return upcoming_match(msg, calendar_ctx) is not None
        return eval_cond(msg.cond, dict(flat_state))
    return msg.trigger_kind == TRIGGER_CRON


def select_due_message(
    messages: Sequence[BroadcastMessage],
    flat_state: Mapping[str, object],
    now: float,
    last_sent: Mapping[str, float | None],
    game: str,
    *,
    calendar_ctx: CalendarCtx | None = None,
    server_minutes: int | None = None,
) -> BroadcastMessage | None:
    """The single highest-priority message due for ``game`` at ``now``, or ``None``.

    Tie-break is ``(priority, id)`` — lower priority number wins, then lexical id —
    so selection is deterministic and stable across ticks.
    """
    due = [
        m
        for m in messages
        if m.applies_to_game(game)
        and message_due(
            m, flat_state, now, last_sent.get(m.id),
            calendar_ctx=calendar_ctx, server_minutes=server_minutes,
        )
    ]
    if not due:
        return None
    return min(due, key=lambda m: (int(m.priority), m.id))
