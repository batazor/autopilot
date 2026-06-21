"""Barrier-signal readers: map a campaign barrier ``signal`` name to a per-account
state flag (read off the decoded ``wos:player:<fid>:state`` hash).

These flags are **deferred readers** — no on-device scenario writes them yet.
The planner reads booleans through here; tests inject them synthetically. When a
real reader lands (e.g. an event-points reader writing ``event_quota_reached``),
the campaign goes live with no engine change.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

_TRUTHY = {"1", "true", "yes", "on"}

# campaign signal name → flat player-state flag key
SIGNAL_FLAGS: dict[str, str] = {
    "quota_reached": "event_quota_reached",
    "joined": "event_joined",
    "city_empty": "city_empty",
    "attack_landed": "attack_landed",
    "troops_sent": "troops_sent",
}


def signal_value(flat_state: Mapping[str, Any], name: str) -> bool:
    """Is the barrier signal ``name`` set in a gamer's flat state?"""
    key = SIGNAL_FLAGS.get(name, name)
    raw = flat_state.get(key)
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in _TRUTHY
