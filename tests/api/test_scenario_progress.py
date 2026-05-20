"""Scenario step progress for Click Approvals / queue UI."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from api.services.click_approval_store import build_scenario_progress
from ui.redis_client import RunningQueueRow


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
