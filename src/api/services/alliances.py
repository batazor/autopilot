"""Service layer for alliance member analysis.

Loads roster rows + scan timestamps from SQLite and hands them to the pure
``analyze_roster`` compute. Prefers the immutable per-scan snapshots so a member
who left is dropped from the current view and churn can diff the two latest
scans; falls back to the upsert roster table when no snapshot history exists yet
(e.g. first deploy, before a new scan has run).
"""
from __future__ import annotations

from typing import Any

from games.wos.alliance.base.analytics import analyze_roster

from config.state_sqlite import (
    get_alliance_members,
    get_member_snapshot,
    list_member_snapshot_times,
)

_MAX_INACTIVE_DAYS = 60


def build_members_analysis(
    alliance_name: str,
    *,
    inactive_days: int = 3,
) -> dict[str, Any] | None:
    """Assemble the analysed roster for an alliance, or ``None`` if unknown."""
    name = str(alliance_name or "").strip()
    if not name:
        return None
    days = max(0, min(int(inactive_days), _MAX_INACTIVE_DAYS))

    times = list_member_snapshot_times(name, limit=2)
    previous: list[dict[str, Any]] | None = None
    prev_captured_at: float | None = None
    snapshot_total: int | None = None
    captured_at: float | None = None

    if times:
        current = get_member_snapshot(name, times[0])
        members = current["members"]
        snapshot_total = current["snapshot_total"]
        captured_at = times[0]
        if len(times) >= 2:
            prev = get_member_snapshot(name, times[1])
            previous = prev["members"]
            prev_captured_at = times[1]
    else:
        roster = get_alliance_members(name)
        members = roster["members"]
        captured_at = (
            max((m.get("captured_at") or 0.0) for m in members) if members else None
        )

    if not members:
        return None

    analytics = analyze_roster(
        members,
        previous=previous,
        inactive_days=days,
        snapshot_total=snapshot_total,
        captured_at=captured_at,
        prev_captured_at=prev_captured_at,
    )
    return {
        "alliance_name": name,
        "captured_at": captured_at,
        "member_count": len(members),
        "members": members,
        "analytics": analytics,
    }
