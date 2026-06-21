"""Calendar → coordinator EventWindow bridge, end-to-end into calendar_bias."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from games.wos.core.calendar.coordinator_windows import (
    event_windows,
    event_windows_from_digest,
)
from games.wos.core.coordinator import calendar_bias

NOW = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)


def _h(n: float) -> timedelta:
    return timedelta(hours=n)


def test_active_and_upcoming_windows_with_offsets():
    events = [
        ("Power Up", NOW - _h(1), NOW + _h(3)),               # live
        ("Armament Competition", NOW + _h(24), NOW + _h(30)),  # upcoming
        ("Foundry Battle", NOW - _h(10), NOW - _h(2)),         # past → dropped
    ]
    ws = {w.slug: w for w in event_windows(events, NOW)}
    assert "foundry_battle" not in ws
    assert ws["power_up"].active is True
    assert ws["power_up"].starts_in_s == 0.0
    assert abs(ws["power_up"].ends_in_s - 3 * 3600) < 1
    assert ws["armament_competition"].active is False
    assert abs(ws["armament_competition"].starts_in_s - 24 * 3600) < 1


def test_dedup_prefers_the_live_occurrence():
    events = [
        ("Power Up", NOW - _h(50), NOW - _h(46)),   # past occurrence
        ("Power Up", NOW - _h(1), NOW + _h(3)),      # live occurrence
    ]
    ws = event_windows(events, NOW)
    assert len(ws) == 1
    assert ws[0].active is True


def test_live_window_drives_calendar_bias_boosts():
    """The payoff: the read schedule lifts the event's reward domains."""
    bias = calendar_bias(event_windows([("Power Up", NOW - _h(1), NOW + _h(3))], NOW))
    assert bias.domain_boost["research"] == 1.5            # power_up = any_power, non-phased
    assert bias.domain_boost["building_progression"] == 1.5
    assert "any_power" in bias.active_categories


def test_imminent_window_emits_hold_not_boost():
    bias = calendar_bias(event_windows([("Power Up", NOW + _h(10), NOW + _h(14))], NOW))
    assert bias.domain_boost == {}                          # not live yet → no boost
    assert any(h.slug == "power_up" for h in bias.holds)    # but hoard speedups for it


def test_from_digest_round_trips():
    digest = [
        {
            "date": "2026-06-19",
            "events": [
                {
                    "name": "Power Up",
                    "state_flag": "event_power_up",
                    "start": (NOW - _h(1)).isoformat(),
                    "end": (NOW + _h(3)).isoformat(),
                    "active_now": True,
                },
            ],
        },
    ]
    ws = event_windows_from_digest(digest, NOW)
    assert len(ws) == 1
    assert ws[0].slug == "power_up"
    assert ws[0].active is True


def test_empty_schedule_is_no_windows():
    assert event_windows([], NOW) == []
    assert event_windows_from_digest([], NOW) == []
