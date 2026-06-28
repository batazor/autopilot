"""Pure analytics over an alliance roster — no I/O, fully unit-testable.

Consumes plain member dicts (as produced by the SQLite snapshot / roster
readers) and returns a JSON-friendly analysis: power distribution, activity /
inactivity, rank composition, and churn (joined / left) versus a previous scan.
The API service layer loads the rows and the scan timestamps; this module only
computes.
"""
from __future__ import annotations

import statistics
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

SECONDS_PER_DAY = 86_400
_RANKS = (5, 4, 3, 2, 1, 0)
_RANK_LABELS = {5: "R5", 4: "R4", 3: "R3", 2: "R2", 1: "R1", 0: "R0"}
# A scan is "complete enough" to trust churn when it parsed at least this
# fraction of the expected roster; below it we suppress churn so OCR drop-outs
# are not reported as members who left.
_COMPLETE_FRACTION = 0.9
_TOP_N = 5


def _norm_key(member: Mapping[str, Any]) -> str:
    key = str(member.get("member_key") or "").strip()
    if key:
        return key
    return " ".join(str(member.get("name") or "").split()).casefold()


def _power(member: Mapping[str, Any]) -> int:
    try:
        return int(member.get("power") or 0)
    except (TypeError, ValueError):
        return 0


def _brief(member: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": str(member.get("name") or ""),
        "rank": int(member.get("rank") or 0),
        "power": _power(member),
        "level": int(member.get("level") or 0),
    }


def _power_stats(members: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    powers = [_power(m) for m in members]
    valued = [p for p in powers if p > 0]
    by_power = sorted((m for m in members if _power(m) > 0), key=_power, reverse=True)
    return {
        "total": sum(powers),
        "avg": round(statistics.fmean(valued)) if valued else 0,
        "median": round(statistics.median(valued)) if valued else 0,
        "min": min(valued) if valued else 0,
        "max": max(valued) if valued else 0,
        # how many members had a readable (non-zero) power; the rest are OCR misses
        "counted": len(valued),
        "top": [_brief(m) for m in by_power[:_TOP_N]],
        # weakest members, ascending (weakest first)
        "bottom": [_brief(m) for m in reversed(by_power[-_TOP_N:])] if by_power else [],
    }


def _activity(
    members: Sequence[Mapping[str, Any]],
    *,
    inactive_days: int,
) -> dict[str, Any]:
    threshold = max(0, int(inactive_days)) * SECONDS_PER_DAY
    online_now = 0
    inactive: list[dict[str, Any]] = []
    unknown = 0
    for m in members:
        if m.get("online"):
            online_now += 1
            continue
        secs = m.get("last_online_seconds")
        if secs is None:
            unknown += 1
            continue
        secs = int(secs)
        if secs >= threshold:
            inactive.append(
                {
                    "name": str(m.get("name") or ""),
                    "rank": int(m.get("rank") or 0),
                    "power": _power(m),
                    "last_online_text": str(m.get("last_online_text") or ""),
                    "last_online_seconds": secs,
                    "days": round(secs / SECONDS_PER_DAY, 1),
                }
            )
    inactive.sort(key=lambda x: x["last_online_seconds"], reverse=True)
    return {
        "online_now": online_now,
        "inactive_count": len(inactive),
        "inactive": inactive,
        "unknown_count": unknown,
        "threshold_days": int(inactive_days),
    }


def _rank_breakdown(members: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[int, int] = dict.fromkeys(_RANKS, 0)
    for m in members:
        r = int(m.get("rank") or 0)
        counts[r] = counts.get(r, 0) + 1
    return [
        {"rank": r, "label": _RANK_LABELS.get(r, f"R{r}"), "count": counts.get(r, 0)}
        for r in _RANKS
    ]


def _churn(
    members: Sequence[Mapping[str, Any]],
    previous: Sequence[Mapping[str, Any]] | None,
    *,
    snapshot_total: int | None,
    captured_at: float | None,
    prev_captured_at: float | None,
) -> dict[str, Any]:
    if previous is None:
        return {"available": False, "reason": "need_two_scans", "joined": [], "left": []}
    parsed = len(members)
    if snapshot_total and parsed < int(snapshot_total) * _COMPLETE_FRACTION:
        return {
            "available": False,
            "reason": "partial_scan",
            "parsed": parsed,
            "expected": int(snapshot_total),
            "joined": [],
            "left": [],
        }
    cur = {k: m for m in members if (k := _norm_key(m))}
    prev = {k: m for m in previous if (k := _norm_key(m))}
    joined = [_brief(m) for k, m in cur.items() if k not in prev]
    left = [_brief(m) for k, m in prev.items() if k not in cur]
    joined.sort(key=lambda x: x["power"], reverse=True)
    left.sort(key=lambda x: x["power"], reverse=True)
    return {
        "available": True,
        "joined": joined,
        "left": left,
        "joined_count": len(joined),
        "left_count": len(left),
        "captured_at": captured_at,
        "prev_captured_at": prev_captured_at,
    }


def analyze_roster(
    members: Sequence[Mapping[str, Any]],
    *,
    previous: Sequence[Mapping[str, Any]] | None = None,
    inactive_days: int = 3,
    snapshot_total: int | None = None,
    captured_at: float | None = None,
    prev_captured_at: float | None = None,
) -> dict[str, Any]:
    """Analyse an alliance roster.

    ``members`` / ``previous`` are member dicts with ``name``, ``rank``,
    ``power``, ``level``, ``online``, ``last_online_text`` and
    ``last_online_seconds`` (plus an optional ``member_key``). ``previous`` is
    the prior scan's roster used for churn; pass ``None`` when only one scan
    exists. ``snapshot_total`` is the expected roster size of the current scan
    and guards churn against partial scans.
    """
    roster = list(members or [])
    return {
        "member_count": len(roster),
        "power": _power_stats(roster),
        "activity": _activity(roster, inactive_days=inactive_days),
        "ranks": _rank_breakdown(roster),
        "churn": _churn(
            roster,
            previous,
            snapshot_total=snapshot_total,
            captured_at=captured_at,
            prev_captured_at=prev_captured_at,
        ),
    }
