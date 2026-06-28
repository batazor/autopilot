"""Unit tests for the pure alliance-roster analytics."""
from __future__ import annotations

from games.wos.alliance.base.analytics import SECONDS_PER_DAY, analyze_roster


def _member(name, power, *, rank=1, level=30, online=False, last_seconds=None):
    return {
        "member_key": name.casefold(),
        "name": name,
        "rank": rank,
        "power": power,
        "level": level,
        "online": online,
        "last_online_text": "Online" if online else "",
        "last_online_seconds": 0 if online else last_seconds,
    }


def test_power_stats_total_avg_median_extremes_and_ordering():
    members = [
        _member("Alpha", 100),
        _member("Bravo", 300),
        _member("Charlie", 200),
        _member("Delta", 0),  # OCR miss — excluded from avg/median/min/max
    ]
    power = analyze_roster(members)["power"]
    assert power["total"] == 600
    assert power["avg"] == 200
    assert power["median"] == 200
    assert power["min"] == 100
    assert power["max"] == 300
    assert power["counted"] == 3
    assert [m["name"] for m in power["top"]] == ["Bravo", "Charlie", "Alpha"]
    assert power["bottom"][0]["name"] == "Alpha"  # weakest first


def test_activity_buckets_online_inactive_and_unknown():
    members = [
        _member("On", 100, online=True),
        _member("Idle1d", 100, last_seconds=1 * SECONDS_PER_DAY),
        _member("Idle5d", 100, last_seconds=5 * SECONDS_PER_DAY),
        _member("NoData", 100, last_seconds=None),
    ]
    activity = analyze_roster(members, inactive_days=3)["activity"]
    assert activity["online_now"] == 1
    assert activity["unknown_count"] == 1
    assert activity["threshold_days"] == 3
    # only the 5-day-idle member crosses the 3-day threshold
    assert activity["inactive_count"] == 1
    assert activity["inactive"][0]["name"] == "Idle5d"
    assert activity["inactive"][0]["days"] == 5.0


def test_rank_breakdown_counts_all_ranks_desc():
    members = [
        _member("Leader", 500, rank=5),
        _member("Officer", 400, rank=4),
        _member("Grunt1", 100, rank=1),
        _member("Grunt2", 90, rank=1),
    ]
    ranks = analyze_roster(members)["ranks"]
    by_rank = {row["rank"]: row["count"] for row in ranks}
    assert [row["rank"] for row in ranks] == [5, 4, 3, 2, 1, 0]
    assert by_rank == {5: 1, 4: 1, 3: 0, 2: 0, 1: 2, 0: 0}


def test_churn_detects_joined_and_left():
    previous = [_member("Stay", 100), _member("Gone", 200)]
    current = [_member("Stay", 100), _member("New", 300)]
    churn = analyze_roster(
        current,
        previous=previous,
        snapshot_total=2,
        captured_at=200.0,
        prev_captured_at=100.0,
    )["churn"]
    assert churn["available"] is True
    assert [m["name"] for m in churn["joined"]] == ["New"]
    assert [m["name"] for m in churn["left"]] == ["Gone"]
    assert churn["captured_at"] == 200.0
    assert churn["prev_captured_at"] == 100.0


def test_churn_needs_two_scans():
    churn = analyze_roster([_member("Solo", 100)], previous=None)["churn"]
    assert churn["available"] is False
    assert churn["reason"] == "need_two_scans"


def test_churn_suppressed_on_partial_scan():
    previous = [_member(f"M{i}", 100) for i in range(10)]
    # only 5 of an expected 10 parsed — must not report 5 false "left"
    current = [_member(f"M{i}", 100) for i in range(5)]
    churn = analyze_roster(current, previous=previous, snapshot_total=10)["churn"]
    assert churn["available"] is False
    assert churn["reason"] == "partial_scan"
    assert churn["parsed"] == 5
    assert churn["expected"] == 10
    assert churn["left"] == []
