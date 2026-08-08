"""Paired ``<field>`` / ``<field>_at`` mappings for the instance state hash.

Several operational fields answered *what* went wrong but not *when*:
``last_error`` and ``queue_blocked_reason`` carried no timestamp, so a reader —
`botctl state`, the attention view, the dashboard — could not tell a
two-second-old failure from a three-hour-old one. The attention view in
particular raised a warning on a `nav_error` of unknown age while its sibling
checks (stuck task, overdue task) all took ``now`` into account.

The convention already exists in this hash (``last_approval_block_at``,
``active_player_at``, ``coord_seen_at``, and the generic ``<region>_at`` that
`agentctl.core` turns into ``age_s``); these helpers just make the pairing
impossible to get half-right. Write the mapping, never the bare field —
the `nav_error` group taught us what happens when one writer knows about a
field and another does not.

``nav_error`` is deliberately NOT here: it belongs to
:class:`navigation.nav_state.NavStateStore`, which owns its whole four-field
group.
"""

from __future__ import annotations

import time

# Truncation guard inherited from the original `_set_instance_state`: an
# exception repr can be arbitrarily long and this hash is read on every UI poll.
_MAX_ERROR_CHARS = 500


def _stamp(at: float | None) -> str:
    return f"{float(at if at is not None else time.time()):.6f}"


def last_error_mapping(error: str, *, at: float | None = None) -> dict[str, str]:
    """Mapping that sets — or clears — ``last_error`` together with its stamp.

    An empty ``error`` clears both, so a recovered instance does not keep a
    timestamp pointing at a failure that no longer exists.
    """
    text = (error or "").strip()
    if not text:
        return {"last_error": "", "last_error_at": ""}
    return {"last_error": text[:_MAX_ERROR_CHARS], "last_error_at": _stamp(at)}


def queue_blocked_mapping(reason: str, *, at: float | None = None) -> dict[str, str]:
    """Mapping that sets — or clears — ``queue_blocked_reason`` with its stamp."""
    text = (reason or "").strip()
    if not text:
        return {"queue_blocked_reason": "", "queue_blocked_reason_at": ""}
    return {"queue_blocked_reason": text, "queue_blocked_reason_at": _stamp(at)}


def field_age_s(raw_at: str | bytes | None, *, now: float | None = None) -> float | None:
    """Seconds since ``raw_at``; ``None`` when absent or unparseable.

    ``None`` means "age unknown", and callers must treat that as *show it
    anyway*. A missing timestamp suppressing an item would make this module a
    new source of silence, which is the opposite of the point.
    """
    if raw_at is None:
        return None
    text = (raw_at.decode() if isinstance(raw_at, bytes) else str(raw_at)).strip()
    if not text:
        return None
    try:
        stamped = float(text)
    except ValueError:
        return None
    if stamped <= 0:
        return None
    return max(0.0, (now if now is not None else time.time()) - stamped)


def format_age(age_s: float | None) -> str:
    """Compact human age suffix (``" (3m ago)"``); ``""`` when unknown."""
    if age_s is None:
        return ""
    if age_s < 60:
        return f" ({int(age_s)}s ago)"
    if age_s < 3600:
        return f" ({int(age_s // 60)}m ago)"
    if age_s < 86400:
        return f" ({int(age_s // 3600)}h ago)"
    return f" ({int(age_s // 86400)}d ago)"
