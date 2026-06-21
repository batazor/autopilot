"""Unit tests for :mod:`agentctl.core`.

These never touch a live Redis or device: ``core`` lazily imports its
dependencies inside each function, so we monkeypatch the underlying helpers
(``dashboard.redis_client.*``, ``api.services.queue_api.enqueue_user_task``, …)
and assert (a) the returned shapes and (b) that control functions push the
exact Redis payloads.
"""

from __future__ import annotations

import json

import pytest

from agentctl import core
from agentctl.core import AgentctlError


class FakeRedis:
    """Records ``lpush`` / ``publish`` so we can assert control payloads."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def lpush(self, key: str, val: str) -> int:
        self.calls.append(("lpush", key, val))
        return 1

    def publish(self, key: str, val: str) -> int:
        self.calls.append(("publish", key, val))
        return 1


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fr = FakeRedis()
    monkeypatch.setattr("dashboard.redis_client.require_redis_connection", lambda: fr)
    return fr


@pytest.fixture
def one_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "list_instances", lambda: ["bs1"])


# --------------------------------------------------------------------------- #
# resolve_instance
# --------------------------------------------------------------------------- #
def test_resolve_single(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "list_instances", lambda: ["bs1"])
    assert core.resolve_instance(None) == "bs1"
    assert core.resolve_instance("bs1") == "bs1"


def test_resolve_ambiguous_requires_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "list_instances", lambda: ["bs1", "bs2"])
    with pytest.raises(AgentctlError, match="pass an instance id"):
        core.resolve_instance(None)


def test_resolve_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "list_instances", lambda: ["bs1"])
    with pytest.raises(AgentctlError, match="unknown instance"):
        core.resolve_instance("zzz")


# --------------------------------------------------------------------------- #
# control: pause / resume / queue_run_now push the right Redis payloads
# --------------------------------------------------------------------------- #
def test_pause_pushes_command(fake_redis: FakeRedis, one_instance: None) -> None:
    out = core.pause("bs1")
    assert out["ok"] is True
    assert out["instance_id"] == "bs1"
    assert fake_redis.calls == [
        ("lpush", "wos:ui:command:bs1", json.dumps({"cmd": "pause"})),
    ]


def test_resume_pushes_command(fake_redis: FakeRedis, one_instance: None) -> None:
    core.resume("bs1")
    assert fake_redis.calls[0][1] == "wos:ui:command:bs1"
    assert json.loads(fake_redis.calls[0][2]) == {"cmd": "resume"}


def test_queue_run_now_nudges_scheduler(
    fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("dashboard.redis_client.run_queue_task_now", lambda *_a, **_k: True)
    out = core.queue_run_now("task1")
    assert out["ok"] is True
    assert (
        "lpush",
        "wos:ui:command:scheduler",
        json.dumps({"cmd": "optimize_now"}),
    ) in fake_redis.calls


def test_queue_run_now_requires_task_id() -> None:
    with pytest.raises(AgentctlError, match="task_id is required"):
        core.queue_run_now("")


# --------------------------------------------------------------------------- #
# run_scenario delegates to enqueue_user_task with the right kwargs
# --------------------------------------------------------------------------- #
def test_run_scenario_enqueues(
    fake_redis: FakeRedis, one_instance: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_enqueue(client: object, **kw: object) -> dict[str, object]:
        captured.update(kw)
        captured["client"] = client
        return {"task_id": "queue:abc", "queue_key": "wos:queue:bs1", "replaced": 0}

    monkeypatch.setattr("api.services.queue_api.enqueue_user_task", fake_enqueue)
    out = core.run_scenario("check_main_city", "bs1", player_id="42", priority=123)

    assert out["task_id"] == "queue:abc"
    assert out["instance_id"] == "bs1"
    assert out["scenario"] == "check_main_city"
    assert captured["scenario_key"] == "check_main_city"
    assert captured["instance_id"] == "bs1"
    assert captured["player_id"] == "42"
    assert captured["priority"] == 123
    assert captured["client"] is fake_redis
    assert isinstance(captured["scheduled_at"], float)


def test_run_scenario_unknown_scenario_raises(
    fake_redis: FakeRedis, one_instance: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_enqueue(client: object, **kw: object) -> dict[str, object]:
        msg = "unknown scenario: nope"
        raise KeyError(msg)

    monkeypatch.setattr("api.services.queue_api.enqueue_user_task", fake_enqueue)
    with pytest.raises(AgentctlError, match="unknown scenario"):
        core.run_scenario("nope", "bs1")


# --------------------------------------------------------------------------- #
# queue() shape: pending sorted by run_at, rows converted to dicts
# --------------------------------------------------------------------------- #
def test_queue_shape_and_sort(
    fake_redis: FakeRedis, one_instance: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dashboard.redis_client import QueueRow, RunningQueueRow

    rows = [
        QueueRow(
            task_id="t2", player_id="p", task_type="b", priority=1,
            scheduled_at=200.0, instance_id="bs1", cooperative=False,
        ),
        QueueRow(
            task_id="t1", player_id="p", task_type="a", priority=1,
            scheduled_at=100.0, instance_id="bs1", cooperative=False,
        ),
    ]
    running = RunningQueueRow(
        task_id="r", player_id="p", task_type="run", priority=1,
        instance_id="bs1", started_at=50.0,
    )
    monkeypatch.setattr("dashboard.redis_client.fetch_queue_rows_for_instances", lambda *_a, **_k: rows)
    monkeypatch.setattr("dashboard.redis_client.fetch_running_queue_row", lambda *_a, **_k: running)
    monkeypatch.setattr("dashboard.redis_client.count_queue_tasks_for_instance", lambda *_a, **_k: 2)

    out = core.queue("bs1")
    assert out["queue_size"] == 2
    assert [r["task_id"] for r in out["pending"]] == ["t1", "t2"]
    assert out["running"]["task_id"] == "r"
    assert "history" not in out


# --------------------------------------------------------------------------- #
# trace(): prefers the live hash field, falls back to history
# --------------------------------------------------------------------------- #
def test_trace_prefers_live(
    fake_redis: FakeRedis, one_instance: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps = [{"i": 0, "status": "ok", "summary": "x"}]
    state = {"last_active_scenario_trace": json.dumps(steps), "current_scenario": "scn"}
    monkeypatch.setattr("dashboard.redis_client.get_instance_state", lambda *_a, **_k: state)

    out = core.trace("bs1")
    assert out["source"] == "live"
    assert out["scenario"] == "scn"
    assert out["steps"] == steps


def test_trace_falls_back_to_history(
    fake_redis: FakeRedis, one_instance: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dashboard.redis_client import QueueHistoryRow

    h = QueueHistoryRow(
        task_id="t", task_type="a", scenario="scn", player_id="p", instance_id="bs1",
        priority=1, started_at=1.0, finished_at=2.0, duration_s=1.0, success=True,
        steps_trace=[{"i": 0}],
    )
    monkeypatch.setattr("dashboard.redis_client.get_instance_state", lambda *_a, **_k: {})
    monkeypatch.setattr(
        "dashboard.redis_client.fetch_queue_history_rows",
        lambda *_a, **_k: [h],
    )

    out = core.trace("bs1")
    assert out["source"] == "history"
    assert out["steps"] == [{"i": 0}]


# --------------------------------------------------------------------------- #
# player(): flat dict + prefix filter
# --------------------------------------------------------------------------- #
def test_player_flatten_and_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStore:
        def to_flat_dict(self) -> dict[str, object]:
            return {"a.b": 1, "a.c": 2, "x": 3}

    class FakeStateStore:
        def get(self, fid: str) -> FakeStore:
            return FakeStore()

        def all_player_ids(self) -> list[str]:
            return ["42"]

    monkeypatch.setattr("config.state_store.get_state_store", lambda: FakeStateStore())

    assert core.player("42")["state"] == {"a.b": 1, "a.c": 2, "x": 3}
    assert set(core.player("42", "a")["state"]) == {"a.b", "a.c"}


def test_player_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStateStore:
        def get(self, fid: str) -> None:
            return None

        def all_player_ids(self) -> list[str]:
            return ["1"]

    monkeypatch.setattr("config.state_store.get_state_store", lambda: FakeStateStore())
    with pytest.raises(AgentctlError, match="unknown player"):
        core.player("999")


# --------------------------------------------------------------------------- #
# screenshot() + bot_lifecycle()
# --------------------------------------------------------------------------- #
def test_screenshot_returns_existing_path(
    tmp_path, one_instance: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    png = tmp_path / "bs1.png"
    png.write_bytes(b"\x89PNG")
    monkeypatch.setattr("dashboard.reference_preview.rolling_live_preview_path", lambda *_a, **_k: png)

    out = core.screenshot("bs1")
    assert out["exists"] is True
    assert out["path"] == str(png)
    assert out["age_s"] is not None


def test_bot_lifecycle_unknown_action() -> None:
    with pytest.raises(AgentctlError, match="unknown bot action"):
        core.bot_lifecycle("frobnicate")
