from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from api.services import attention


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.kv.get(key)

    def set(self, key: str, value: str) -> bool:
        self.kv[key] = value
        return True

    def delete(self, key: str) -> int:
        return int(self.kv.pop(key, None) is not None)


@pytest.fixture
def fleet(monkeypatch: pytest.MonkeyPatch):
    """Two-instance fleet with all signal sources stubbed healthy.

    Tests flip individual sources; the fixture returns the mutable dicts so a
    test reads as "given this one deviation, expect this one item".
    """
    now = time.time()
    live_row = {"worker_started_at": str(now - 60), "last_seen_at": str(now)}
    states: dict[str, dict[str, str]] = {"bs1": dict(live_row), "bs2": dict(live_row)}
    queue_heads: dict[str, Any] = {"bs1": None, "bs2": None}
    approvals: dict[str, Any] = {"bs1": None, "bs2": None}
    failures: list[dict[str, Any]] = []

    monkeypatch.setattr(
        attention,
        "load_settings",
        lambda: SimpleNamespace(
            instances=[
                SimpleNamespace(instance_id="bs1"),
                SimpleNamespace(instance_id="bs2"),
            ],
            worker=SimpleNamespace(task_timeout_seconds=300),
        ),
    )
    monkeypatch.setattr(
        attention, "get_instance_state", lambda _client, iid: states.get(iid, {})
    )
    monkeypatch.setattr(
        attention,
        "fetch_next_queue_row_for_instance",
        lambda _client, *, instance_id: queue_heads.get(instance_id),
    )
    monkeypatch.setattr(
        attention.click_approval_store,
        "get_pending",
        lambda _client, iid: approvals.get(iid),
    )
    monkeypatch.setattr(
        attention.click_approval_store,
        "scenario_display_name",
        lambda key: key.title(),
    )
    monkeypatch.setattr(attention, "read_load_failures", lambda _client: failures)
    monkeypatch.setattr(attention, "_bot_process_running", lambda: False)
    return SimpleNamespace(
        now=now,
        states=states,
        queue_heads=queue_heads,
        approvals=approvals,
        failures=failures,
    )


def _view(client: Any = None) -> dict[str, Any]:
    return attention.build_attention_view(client=client)


def _kinds(view: dict[str, Any]) -> list[str]:
    return [i["kind"] for i in view["items"]]


def test_healthy_fleet_is_empty(fleet) -> None:
    view = _view()
    assert view["items"] == []
    assert view["counts"] == {"critical": 0, "warning": 0, "total": 0}


def test_load_failure_is_critical(fleet) -> None:
    fleet.failures.append(
        {"source": "scenario_loader", "file": "games/wos/x/broken.yaml", "error": "boom", "ts": 1.0}
    )
    view = _view()
    assert _kinds(view) == ["load_failure"]
    item = view["items"][0]
    assert item["severity"] == "critical"
    assert "games/wos/x/broken.yaml" in item["title"]
    assert item["detail"] == "boom"


def test_startup_validation_warning_is_warning_with_trace(fleet) -> None:
    fleet.failures.append(
        {
            "source": "startup_validation",
            "file": "screen_family:shop",
            "error": "2 sibling route gap(s)",
            "severity": "warning",
            "trace": "[warning] screen_family:shop: 2 sibling route gap(s)",
            "ts": 1.0,
        }
    )
    view = _view()
    assert _kinds(view) == ["load_failure"]
    item = view["items"][0]
    assert item["severity"] == "warning"
    assert "warning" in item["title"]
    assert item["debug_log"].startswith("[warning]")


def test_approval_pending_names_scenario(fleet) -> None:
    fleet.approvals["bs1"] = {
        "context": {"scenario": "deals.sign_in"},
        "region": "main_city",
    }
    view = _view()
    assert _kinds(view) == ["approval_pending"]
    item = view["items"][0]
    assert item["instance_id"] == "bs1"
    assert item["detail"] == "Deals.Sign_In · main_city"


def test_device_offline_supersedes_worker_down_and_queue_stuck(fleet) -> None:
    fleet.states["bs2"] = {
        "worker_started_at": str(fleet.now - 9000),
        "last_seen_at": str(fleet.now - 9000),
        "paused": "1",
        "auto_paused": "1",
        "last_error": "device offline (ADB)",
    }
    # 205h-overdue queue head: a consequence of the offline device, not its
    # own item.
    fleet.queue_heads["bs2"] = SimpleNamespace(
        scheduled_at=fleet.now - 205 * 3600, task_type="dismiss_popup"
    )
    view = _view()
    assert _kinds(view) == ["device_offline"]
    assert view["items"][0]["severity"] == "critical"
    assert view["items"][0]["dismissible"] is True


def test_device_offline_attention_can_be_dismissed_until_reconnect(fleet) -> None:
    client = _FakeRedis()
    fleet.states["bs2"] = {
        "worker_started_at": str(fleet.now - 9000),
        "last_seen_at": str(fleet.now - 9000),
        "paused": "1",
        "auto_paused": "1",
        "last_error": "device offline (ADB)",
    }

    assert _kinds(_view(client)) == ["device_offline"]
    assert attention.dismiss_item(client, kind="device_offline", instance_id="bs2") is True
    assert _view(client)["items"] == []

    fleet.states["bs2"] = {
        "worker_started_at": str(fleet.now - 60),
        "last_seen_at": str(fleet.now),
    }
    assert _view(client)["items"] == []
    assert client.kv == {}


def test_partial_worker_down_is_reported(fleet) -> None:
    fleet.states["bs1"] = {
        "worker_started_at": str(fleet.now - 9000),
        "last_seen_at": str(fleet.now - 9000),
    }
    view = _view()
    assert _kinds(view) == ["worker_down"]
    assert view["items"][0]["instance_id"] == "bs1"


def test_all_workers_down_without_bot_process_is_quiet(fleet) -> None:
    stale = {
        "worker_started_at": str(fleet.now - 9000),
        "last_seen_at": str(fleet.now - 9000),
    }
    fleet.states["bs1"] = dict(stale)
    fleet.states["bs2"] = dict(stale)
    assert _view()["items"] == []


def test_all_workers_down_with_bot_process_is_a_crash(
    fleet, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = {
        "worker_started_at": str(fleet.now - 9000),
        "last_seen_at": str(fleet.now - 9000),
    }
    fleet.states["bs1"] = dict(stale)
    fleet.states["bs2"] = dict(stale)
    monkeypatch.setattr(attention, "_bot_process_running", lambda: True)
    assert _kinds(_view()) == ["worker_down", "worker_down"]


def test_queue_stuck_only_beyond_threshold_and_only_when_live(fleet) -> None:
    fleet.queue_heads["bs1"] = SimpleNamespace(
        scheduled_at=fleet.now - 60, task_type="check_main_city"
    )
    assert _view()["items"] == []

    fleet.queue_heads["bs1"] = SimpleNamespace(
        scheduled_at=fleet.now - 2 * 3600, task_type="check_main_city"
    )
    view = _view()
    assert _kinds(view) == ["queue_stuck"]
    item = view["items"][0]
    assert item["severity"] == "warning"
    assert "2h 0m" in item["detail"]
    assert "check_main_city" in item["detail"]


def test_task_stuck_past_worker_timeout(fleet) -> None:
    fleet.states["bs1"].update(
        {
            "state": "busy",
            "current_scenario": "deals.sign_in",
            "current_task_started_at": str(fleet.now - 80 * 60),
        }
    )
    view = _view()
    assert _kinds(view) == ["task_stuck"]
    item = view["items"][0]
    assert item["severity"] == "warning"
    assert "1h 20m" in item["title"]
    assert "deals.sign_in" in item["detail"]


def test_task_under_timeout_is_not_stuck(fleet) -> None:
    fleet.states["bs1"].update(
        {
            "state": "busy",
            "current_scenario": "deals.sign_in",
            "current_task_started_at": str(fleet.now - 60),
        }
    )
    assert _view()["items"] == []


def test_pending_approval_explains_the_long_task(fleet) -> None:
    fleet.states["bs1"].update(
        {
            "state": "busy",
            "current_scenario": "deals.sign_in",
            "current_task_started_at": str(fleet.now - 80 * 60),
        }
    )
    fleet.approvals["bs1"] = {"context": {}, "region": "main_city"}
    assert _kinds(_view()) == ["approval_pending"]


def test_dead_worker_long_task_reports_worker_down_only(fleet) -> None:
    fleet.states["bs1"] = {
        "worker_started_at": str(fleet.now - 9000),
        "last_seen_at": str(fleet.now - 9000),
        "state": "busy",
        "current_scenario": "deals.sign_in",
        "current_task_started_at": str(fleet.now - 9000),
    }
    assert _kinds(_view()) == ["worker_down"]


def test_nav_error_is_warning(fleet) -> None:
    fleet.states["bs1"]["nav_error"] = "navigation_aborted: deals → main_city"
    view = _view()
    assert _kinds(view) == ["nav_error"]
    assert view["items"][0]["severity"] == "warning"


def test_critical_sorts_before_warning(fleet) -> None:
    fleet.states["bs1"]["nav_error"] = "boom"
    fleet.approvals["bs2"] = {"context": {}, "region": "main_city"}
    view = _view()
    assert _kinds(view) == ["approval_pending", "nav_error"]
    assert view["counts"] == {"critical": 1, "warning": 1, "total": 2}
