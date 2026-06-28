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
        {"source": "dsl_validation", "file": "games/wos/x/broken.yaml", "error": "boom", "ts": 1.0}
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


def test_pending_approval_is_not_an_attention_item(fleet) -> None:
    # A click approval waiting for the operator is normal in approval mode and
    # is surfaced in the bot control panel — not as an error/attention item.
    fleet.approvals["bs1"] = {
        "context": {"scenario": "deals.sign_in"},
        "region": "main_city",
    }
    assert _view()["items"] == []


def test_pending_approval_suppresses_stale_worker_attention(fleet) -> None:
    fleet.states["bs1"] = {
        "worker_started_at": str(fleet.now - 9000),
        "last_seen_at": str(fleet.now - 9000),
        "state": "busy",
        "current_scenario": "deals.sign_in",
        "current_task_started_at": str(fleet.now - 9000),
    }
    fleet.approvals["bs1"] = {
        "context": {"scenario": "deals.sign_in"},
        "region": "button.claim",
    }

    assert _view()["items"] == []


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


def test_device_offline_retry_exhausted_attention_requires_operator(fleet) -> None:
    fleet.states["bs2"] = {
        "worker_started_at": str(fleet.now - 9000),
        "last_seen_at": str(fleet.now - 9000),
        "paused": "1",
        "auto_paused": "0",
        "last_error": "device offline (ADB): retry limit reached (5/5); user action required",
        "adb_offline_attempts": "5",
        "adb_offline_retry_exhausted": "1",
    }

    view = _view()

    assert _kinds(view) == ["device_offline"]
    assert "retry limit reached (5/5)" in view["items"][0]["detail"]
    assert "operator resumes" in view["items"][0]["detail"]


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


def test_pending_approval_suppresses_the_long_task(fleet) -> None:
    fleet.states["bs1"].update(
        {
            "state": "busy",
            "current_scenario": "deals.sign_in",
            "current_task_started_at": str(fleet.now - 80 * 60),
        }
    )
    fleet.approvals["bs1"] = {"context": {}, "region": "main_city"}
    # The long-running task is the approval wait, not a hang — stay quiet.
    assert _view()["items"] == []


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
    fleet.states["bs1"]["nav_error"] = "boom"  # warning, bs1 stays live
    fleet.states["bs2"] = {
        "worker_started_at": str(fleet.now - 9000),
        "last_seen_at": str(fleet.now - 9000),
    }  # stale → worker_down (critical)
    view = _view()
    assert _kinds(view) == ["worker_down", "nav_error"]
    assert view["counts"] == {"critical": 1, "warning": 1, "total": 2}


# --------------------------------------------------------------------------- #
# fleet_health — one verdict for agent steering
# --------------------------------------------------------------------------- #
def test_fleet_health_healthy(fleet) -> None:
    h = attention.fleet_health(client=None)
    assert h["verdict"] == "HEALTHY"
    assert h["live_workers"] == 2
    assert h["instances_total"] == 2
    assert h["items"] == []


def test_fleet_health_degraded_on_warning(fleet) -> None:
    fleet.states["bs1"]["nav_error"] = "boom"
    h = attention.fleet_health(client=None)
    assert h["verdict"] == "DEGRADED"
    assert h["issues_by_kind"] == {"nav_error": 1}


def test_fleet_health_critical_on_worker_down(fleet) -> None:
    fleet.states["bs2"] = {
        "worker_started_at": str(fleet.now - 9000),
        "last_seen_at": str(fleet.now - 9000),
    }  # stale → worker_down (critical), bs1 still live so no suppression
    h = attention.fleet_health(client=None)
    assert h["verdict"] == "CRITICAL"
    assert h["critical"] == 1
    assert h["live_workers"] == 1


# --------------------------------------------------------------------------- #
# diagnose_instance — why is one instance idle?
# --------------------------------------------------------------------------- #
def test_diagnose_instance_healthy(fleet) -> None:
    d = attention.diagnose_instance(None, "bs1", now=fleet.now)
    assert d["verdict"] == "ok"
    assert d["issues"] == []
    assert d["live"] is True
    assert d["approval_pending"] is False


def test_diagnose_instance_flags_pending_approval(fleet) -> None:
    fleet.approvals["bs1"] = "claim_mail"
    d = attention.diagnose_instance(None, "bs1", now=fleet.now)
    assert d["approval_pending"] is True
    assert "approval_pending" in [i["kind"] for i in d["issues"]]
    assert d["verdict"] == "degraded"


def test_diagnose_instance_flags_manual_pause(fleet) -> None:
    fleet.states["bs1"]["paused"] = "1"
    d = attention.diagnose_instance(None, "bs1", now=fleet.now)
    assert "paused" in [i["kind"] for i in d["issues"]]
    assert d["verdict"] == "degraded"
