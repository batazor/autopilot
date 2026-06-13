"""API service test for the stamina-budget endpoint glue.

Exercises ``build_player_stamina`` against a fake sync Redis: reads the player
state hash, reuses the pure allocator view, and attaches the decision trace.
"""
from __future__ import annotations

import json
import time

from api.services import players as players_svc


class _FakeRedis:
    def __init__(self, state: dict[str, str], decisions: list[str]) -> None:
        self._state = state
        self._decisions = decisions

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._state)

    def zrevrange(self, key: str, start: int, stop: int) -> list[str]:
        return list(self._decisions[start : stop + 1])


def test_build_player_stamina_shape_and_trace():
    decisions = [
        json.dumps({"action": "consume", "target": "intel_events", "reason": "selected"}),
        json.dumps({"action": "idle", "target": None, "reason": "idle_no_eligible_demand"}),
    ]
    fake = _FakeRedis(
        {"stamina": "156", "stamina_read_at": str(time.time()), "joe_event_active": "0"},
        decisions,
    )

    view = players_svc.build_player_stamina(fake, "12345")

    assert view["cap"] == 200
    assert view["enabled"] is True         # planner active (budget.yaml)
    assert 155 <= view["est"] <= 157
    assert {d["id"] for d in view["demands"]} == {
        "joe_bandits",
        "intel_events",
        "beast_hunt",
    }
    # Trace parsed newest-first.
    assert view["recent"][0]["action"] == "consume"
    assert view["recent"][0]["target"] == "intel_events"
    # Whole payload must be JSON-serialisable for the HTTP response.
    json.dumps(view)


def test_build_player_stamina_tolerates_no_state_and_bad_trace():
    fake = _FakeRedis({}, ["not json", json.dumps({"action": "idle"})])
    view = players_svc.build_player_stamina(fake, "12345")
    assert view["est"] is None             # never read
    assert view["seconds_to_cap"] is None
    assert len(view["recent"]) == 1        # malformed member skipped
