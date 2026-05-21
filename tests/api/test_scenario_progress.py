"""Scenario step progress for Click Approvals / queue UI."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from api.services.click_approval_store import build_scenario_progress
from dashboard.redis_client import RunningQueueRow

if TYPE_CHECKING:
    import pytest


def test_build_scenario_progress_shows_step_while_busy_without_running_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the queue running TTL expired, instance state still drives progress."""
    monkeypatch.setattr(
        "api.services.click_approval_store.fetch_running_queue_row",
        lambda *_a, **_k: None,
    )
    client = MagicMock()
    instance_state = {
        "state": "busy",
        "current_scenario": "claim_exploration_rewards",
        "current_task_id": "t1",
        "current_task_type": "claim_exploration_rewards",
        "last_active_scenario_step": "2",
        "last_active_scenario_iter": "3",
        "nav_target": "",
    }
    progress = build_scenario_progress(client, "bs1", instance_state)
    assert progress["is_running"] is True
    assert progress["step_current"] == 2
    assert progress["step_total"] == 3
    assert progress["step_iter"] == 3


def test_build_scenario_progress_idle_keeps_last_step_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.click_approval_store.fetch_running_queue_row",
        lambda *_a, **_k: None,
    )
    client = MagicMock()
    instance_state = {
        "state": "ready",
        "current_scenario": "claim_exploration_rewards",
        "last_active_scenario_step": "2",
    }
    progress = build_scenario_progress(client, "bs1", instance_state)
    assert progress["is_running"] is False
    assert progress["step_current"] == 2


def test_build_scenario_progress_running_row_match(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fetch(_client: object, *, instance_id: str) -> RunningQueueRow:
        assert instance_id == "bs1"
        return RunningQueueRow(
            task_id="t1",
            player_id="p1",
            task_type="claim_exploration_rewards",
            priority=0,
            instance_id="bs1",
            started_at=0.0,
            region=None,
            payload=None,
        )

    monkeypatch.setattr(
        "api.services.click_approval_store.fetch_running_queue_row",
        _fetch,
    )
    client = MagicMock()
    instance_state = {
        "state": "busy",
        "current_scenario": "claim_exploration_rewards",
        "current_task_id": "t1",
        "current_task_type": "claim_exploration_rewards",
        "last_active_scenario_step": "1",
    }
    progress = build_scenario_progress(client, "bs1", instance_state)
    assert progress["is_running"] is True
    assert progress["step_current"] == 1
    assert progress["completed_steps"] == 2
    assert progress["is_navigating"] is False


def test_build_scenario_progress_navigating_zero_fill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.click_approval_store.fetch_running_queue_row",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "api.services.click_approval_store._scenario_step_summaries",
        lambda _k: ("ocr", "exec", "push"),
    )
    client = MagicMock()
    instance_state = {
        "state": "busy",
        "current_scenario": "who_i_am",
        "current_task_id": "t1",
        "current_task_type": "who_i_am",
        "last_active_scenario_step": "0",
        "nav_target": "chief_profile",
    }
    progress = build_scenario_progress(client, "bs1", instance_state)
    assert progress["is_navigating"] is True
    assert progress["completed_steps"] == 0
    assert progress["progress_ratio"] == 0.0
    assert "Navigating → chief_profile" in progress["progress_label"]
    assert "Step 1/3" not in progress["progress_label"]
