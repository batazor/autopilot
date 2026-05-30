"""Helpers for durable event reset timers stored in SQLite player state."""
from __future__ import annotations

import logging
import time
from typing import Any

from config.state_schema import EventTimerState
from config.state_store import get_state_store

logger = logging.getLogger(__name__)


def event_timer_to_dict(entry: object) -> dict[str, Any]:
    if isinstance(entry, EventTimerState):
        return entry.model_dump(mode="json")
    if hasattr(entry, "model_dump"):
        try:
            data = entry.model_dump(mode="json")  # type: ignore[attr-defined]
        except TypeError:
            data = entry.model_dump()  # type: ignore[attr-defined]
        return data if isinstance(data, dict) else {}
    return dict(entry) if isinstance(entry, dict) else {}


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def event_timer_remaining_seconds(
    entry: object,
    *,
    now: float | None = None,
) -> float | None:
    data = event_timer_to_dict(entry)
    if not data:
        return None
    reset_at = _float_or_none(data.get("reset_at"))
    if reset_at is not None and reset_at > 0:
        return max(0.0, reset_at - (time.time() if now is None else now))
    remaining_s = _float_or_none(data.get("remaining_s"))
    if remaining_s is not None:
        return max(0.0, remaining_s)
    return None


def store_event_timer(
    *,
    player_id: str,
    event_name: str,
    raw_text: str,
    remaining_s: int,
    recorded_at: float | None = None,
    source_region: str = "",
    confidence: float = 0.0,
) -> bool:
    """Persist an event reset timer in SQLite player state.

    The event name is used as an exact dict key, so dotted names like
    ``shop.artisans_trove`` are not split into nested state paths.
    """
    pid = str(player_id or "").strip()
    name = str(event_name or "").strip()
    if not pid or not name:
        return False
    remaining = max(0, int(remaining_s))
    recorded = time.time() if recorded_at is None else float(recorded_at)
    try:
        store = get_state_store().get_or_create(pid)
        snapshot = store.snapshot()
        raw_timers = getattr(snapshot, "event_timers", {}) or {}
        timers: dict[str, EventTimerState] = {}
        if isinstance(raw_timers, dict):
            for key, value in raw_timers.items():
                if isinstance(value, EventTimerState):
                    timers[str(key)] = value
                elif isinstance(value, dict):
                    timers[str(key)] = EventTimerState.model_validate(value)
        timers[name] = EventTimerState(
            remaining_s=remaining,
            recorded_at=recorded,
            reset_at=recorded + remaining,
            raw_text=str(raw_text or ""),
            source_region=str(source_region or ""),
            confidence=max(0.0, min(1.0, float(confidence or 0.0))),
        )
        store.set("event_timers", timers)
        return True
    except Exception:
        logger.exception(
            "event_timer: failed to persist timer event=%s player=%s",
            name,
            pid,
        )
        return False


def read_event_timer(player_id: str, event_name: str) -> EventTimerState | None:
    pid = str(player_id or "").strip()
    name = str(event_name or "").strip()
    if not pid or not name:
        return None
    try:
        store = get_state_store().get(pid)
        if store is None:
            return None
        timers = getattr(store.snapshot(), "event_timers", {}) or {}
        if not isinstance(timers, dict):
            return None
        entry = timers.get(name)
        if isinstance(entry, EventTimerState):
            return entry
        if isinstance(entry, dict):
            return EventTimerState.model_validate(entry)
        return None
    except Exception:
        logger.debug(
            "event_timer: failed to read timer event=%s player=%s",
            name,
            pid,
            exc_info=True,
        )
        return None
