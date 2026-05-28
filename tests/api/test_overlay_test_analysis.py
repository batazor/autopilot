from __future__ import annotations

from api.services.overlay_test import (
    _collect_push_candidates,
    _overlay_test_cond_context,
)


def test_overlay_test_cond_context_simulates_no_player() -> None:
    state_flat, simulated, active_player = _overlay_test_cond_context(
        has_active_player=False,
    )
    assert state_flat == {"active_player": ""}
    assert simulated is True
    assert active_player == ""


def test_overlay_test_cond_context_assumes_player_without_redis() -> None:
    state_flat, simulated, active_player = _overlay_test_cond_context(
        has_active_player=True,
    )
    assert simulated is False
    assert state_flat["active_player"]
    assert active_player == state_flat["active_player"]


def test_collect_push_candidates_marks_highest_priority_selected(
    tmp_path,
    monkeypatch,
) -> None:
    repo = tmp_path

    def _enabled(_root, name: str) -> bool | None:
        return True

    def _device_level(_root, name: str) -> bool:
        return True

    monkeypatch.setattr("dsl.dsl_schema.dsl_scenario_yaml_enabled", _enabled)
    monkeypatch.setattr("dsl.dsl_schema.dsl_scenario_yaml_device_level", _device_level)

    results = {
        "low.rule": {
            "matched": True,
            "region": "btn.a",
            "pushScenario": [{"name": "scenario.low", "priority": 10}],
        },
        "high.rule": {
            "matched": True,
            "region": "btn.b",
            "pushScenario": [{"name": "scenario.high", "priority": 90}],
        },
    }
    out = _collect_push_candidates(
        results,
        repo=repo,
        active_player="123",
        current_screen="main_city",
    )
    selected = [row for row in out if row["selected"]]
    assert len(selected) == 1
    assert selected[0]["scenario"] == "scenario.high"
    assert selected[0]["rule"] == "high.rule"
    losers = [row for row in out if not row["selected"]]
    assert losers and all(r["skip_reason"] == "lost_to=high.rule" for r in losers)


def test_collect_push_candidates_marks_sibling_blocked_when_no_winner(
    tmp_path,
    monkeypatch,
) -> None:
    repo = tmp_path

    def _enabled(_root, name: str) -> bool | None:
        return name != "scenario.disabled"

    def _device_level(_root, name: str) -> bool:
        return True

    monkeypatch.setattr("dsl.dsl_schema.dsl_scenario_yaml_enabled", _enabled)
    monkeypatch.setattr("dsl.dsl_schema.dsl_scenario_yaml_device_level", _device_level)

    results = {
        "only.rule": {
            "matched": True,
            "region": "btn.a",
            "pushScenario": [
                {"name": "scenario.ok", "priority": 10},
                {"name": "scenario.disabled", "priority": 10},
            ],
        },
    }
    out = _collect_push_candidates(
        results,
        repo=repo,
        active_player="123",
        current_screen="main_city",
    )
    assert all(not row["selected"] for row in out)
    ok_row = next(row for row in out if row["scenario"] == "scenario.ok")
    assert ok_row["skip_reason"] == "sibling_blocked:scenario.disabled=disabled"
    bad_row = next(row for row in out if row["scenario"] == "scenario.disabled")
    assert bad_row["skip_reason"] == "disabled"
