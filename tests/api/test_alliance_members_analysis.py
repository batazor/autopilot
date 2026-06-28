"""Tests for the alliance member-analysis service (snapshot history → analytics)."""
from __future__ import annotations

from api.services.alliances import build_members_analysis
from config.state_sqlite import (
    record_alliance_members_history,
    record_alliance_members_snapshot,
)


def _m(name, power, *, rank=1, online=False, last_seconds=None):
    return {
        "name": name,
        "rank": rank,
        "power": power,
        "level": 30,
        "online": online,
        "last_online_text": "Online" if online else "",
        "last_online_seconds": 0 if online else last_seconds,
    }


def test_unknown_alliance_returns_none() -> None:
    assert build_members_analysis("Nope") is None


def test_analysis_uses_latest_snapshot_and_diffs_churn() -> None:
    record_alliance_members_history(
        alliance_name="Crimson",
        members=[_m("Stay", 100, rank=4), _m("Gone", 200, rank=3)],
        total_count=2,
        captured_at=100.0,
    )
    record_alliance_members_history(
        alliance_name="Crimson",
        members=[
            _m("Stay", 120, rank=4, online=True),
            _m("New", 300, rank=1, last_seconds=5 * 86_400),
        ],
        total_count=2,
        captured_at=200.0,
    )

    data = build_members_analysis("Crimson", inactive_days=3)
    assert data is not None
    assert data["captured_at"] == 200.0
    assert data["member_count"] == 2
    # current view = latest snapshot only (Gone, who left, is absent)
    assert {m["name"] for m in data["members"]} == {"Stay", "New"}

    analytics = data["analytics"]
    assert analytics["power"]["total"] == 420
    assert analytics["activity"]["online_now"] == 1
    assert analytics["activity"]["inactive_count"] == 1  # New, 5d idle > 3d
    churn = analytics["churn"]
    assert churn["available"] is True
    assert [m["name"] for m in churn["joined"]] == ["New"]
    assert [m["name"] for m in churn["left"]] == ["Gone"]


def test_analysis_falls_back_to_upsert_roster_without_history() -> None:
    # Only the legacy upsert roster exists (no snapshot history yet).
    record_alliance_members_snapshot(
        alliance_name="Legacy",
        members=[_m("Solo", 100, rank=5)],
    )
    data = build_members_analysis("Legacy")
    assert data is not None
    assert data["member_count"] == 1
    assert data["analytics"]["churn"]["available"] is False
    assert data["analytics"]["churn"]["reason"] == "need_two_scans"
