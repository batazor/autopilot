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


# --------------------------------------------------------------------------- #
# Explainability — why() / planners() and their pure helpers
# --------------------------------------------------------------------------- #
NOW = 1_700_000_000.0


class FakeRedisZ:
    """A fake redis exposing just ``zrevrange`` for decision-trace reads."""

    def __init__(self, zsets: dict[str, list[tuple[str, float]]] | None = None) -> None:
        self.zsets = zsets or {}

    def zrevrange(self, key: str, start: int, end: int, withscores: bool = False):
        items = self.zsets.get(key, [])
        sl = items[start : (end + 1 if end >= 0 else None)]
        return sl if withscores else [m for m, _ in sl]


@pytest.fixture
def fake_redis_z(monkeypatch: pytest.MonkeyPatch) -> FakeRedisZ:
    fr = FakeRedisZ()
    monkeypatch.setattr("dashboard.redis_client.require_redis_connection", lambda: fr)
    return fr


def test_decode_source_prefixes() -> None:
    src = lambda tid, p=1, f=False: core._decode_source(tid, priority=p, focused=f)["code"]  # noqa: E731
    assert src("cron:check:1") == "cron"
    assert src("ovl:bs1:x") == "overlay"
    assert src("notify:abc") == "notify"
    assert src("optimizer:abc") == "optimizer"
    assert src("coord-switch:abc") == "coord_switch"  # must beat the coord: prefix
    assert src("coord:abc") == "coordinator"
    assert src("dsl:push:scn") == "dsl_push"
    assert src("queue:abc") == "operator"
    assert src("queue:abc", p=95_000) == "focus"   # high-priority enqueue
    assert src("cron:x", f=True) == "focus"          # focus mode overrides
    assert src("mystery") == "unknown"


def test_input_present_exact_and_wildcard() -> None:
    flat = {"buildings.levels.furnace": "5", "stamina": "120", "blank": ""}
    assert core._input_present(flat, "stamina")
    assert not core._input_present(flat, "blank")        # present but empty
    assert not core._input_present(flat, "missing")
    assert core._input_present(flat, "buildings.levels.*")
    assert not core._input_present(flat, "research.levels.*")


def test_planners_classifies_status_blind_and_last_decision(
    fake_redis_z: FakeRedisZ, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = [
        {"name": "march", "wired": "scheduler", "config": "x/march.yaml",
         "trace_key": "wos:player:{fid}:march_decisions", "observed_inputs": []},
        {"name": "stamina", "wired": "scheduler", "config": "x/stamina.yaml",
         "trace_key": "", "observed_inputs": ["stamina"]},
        {"name": "resources", "wired": "scheduler", "config": "x/res.yaml",
         "trace_key": "", "observed_inputs": ["troops.infantry.available"]},
        {"name": "heroes", "wired": "calculator", "config": "",
         "trace_key": "", "observed_inputs": ["heroes.roster"]},
        {"name": "intel", "wired": "via-march", "config": "x/march.yaml",
         "trace_key": "", "observed_inputs": []},
    ]
    enabled = {"x/march.yaml": True, "x/stamina.yaml": False, "x/res.yaml": False}
    monkeypatch.setattr(core, "_load_planner_manifest", lambda *_a, **_k: manifest)
    monkeypatch.setattr(core, "_yaml_enabled", lambda cfg, _key: enabled.get(cfg))
    monkeypatch.setattr(core, "_resolve_active_fid", lambda *_a, **_k: "42")
    monkeypatch.setattr(core, "_player_flat", lambda *_a, **_k: {"stamina": "120"})
    fake_redis_z.zsets["wos:player:42:march_decisions"] = [
        (json.dumps({"ts": NOW, "action": "dispatch", "reason": "queued intel", "target": "intel"}), NOW),
    ]

    out = core.planners()
    by = {p["name"]: p for p in out["planners"]}
    assert out["fid"] == "42"
    assert by["march"]["status"] == "LIVE"
    assert by["march"]["blind"] is False
    assert by["march"]["last_decision"]["action"] == "dispatch"
    assert by["stamina"]["status"] == "DORMANT"
    assert by["stamina"]["blind"] is False           # stamina observed
    assert by["resources"]["status"] == "DORMANT"
    assert by["resources"]["blind"] is True           # troops reader missing
    assert by["resources"]["missing_inputs"] == ["troops.infantry.available"]
    assert by["heroes"]["status"] == "CALC-ONLY"
    assert by["intel"]["status"] == "VIA-MARCH"


def test_planners_blind_unknown_without_player(
    fake_redis_z: FakeRedisZ, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = [{"name": "stamina", "wired": "scheduler", "config": "x/stamina.yaml",
                 "trace_key": "", "observed_inputs": ["stamina"]}]
    monkeypatch.setattr(core, "_load_planner_manifest", lambda *_a, **_k: manifest)
    monkeypatch.setattr(core, "_yaml_enabled", lambda _cfg, _key: True)
    monkeypatch.setattr(core, "_resolve_active_fid", lambda *_a, **_k: "")  # no active player
    out = core.planners()
    assert out["fid"] == ""
    assert out["planners"][0]["blind"] is None        # unknown without a player


def test_why_running_decodes_source_rank_meta_and_decisions(
    fake_redis_z: FakeRedisZ, one_instance: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dashboard.redis_client import RunningQueueRow

    row = RunningQueueRow(
        task_id="cron:check:42:1", player_id="42", task_type="check_main_city",
        priority=55_000, instance_id="bs1", started_at=100.0, region=None,
        payload={"dsl_scenario": "check_main_city",
                 "rank_meta": {"effective_priority": 54_500, "graph_debuff": 500, "hops": 1}},
    )
    monkeypatch.setattr("dashboard.redis_client.fetch_running_queue_row", lambda *_a, **_k: row)
    monkeypatch.setattr("dashboard.redis_client.get_instance_state", lambda *_a, **_k: {"current_task_id": "cron:check:42:1"})
    fake_redis_z.zsets["wos:player:42:stamina_decisions"] = [
        (json.dumps({"ts": NOW, "action": "idle", "reason": "stamina unknown"}), NOW),
    ]

    out = core.why("bs1")
    assert out["running"] is True
    assert out["scenario"] == "check_main_city"
    assert out["source"]["code"] == "cron"
    assert out["rank_meta"]["graph_debuff"] == 500
    assert out["decisions_player"] == "42"
    assert out["decisions"]["stamina"]["action"] == "idle"
    assert out["decisions"]["march"] is None


def test_drive_assembles_trace_diff_and_restores_approval(
    monkeypatch: pytest.MonkeyPatch, one_instance: None
) -> None:
    class FR:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}
            self.deleted: list[str] = []

        def get(self, k: str):
            return self.store.get(k)

        def set(self, k: str, v: str) -> None:
            self.store[k] = v

        def delete(self, k: str) -> None:
            self.deleted.append(k)
            self.store.pop(k, None)

    fr = FR()
    monkeypatch.setattr("dashboard.redis_client.require_redis_connection", lambda: fr)
    # before snapshot empty, after has the reader's output key
    states = iter([{}, {"troops.infantry.available": "73443"}])
    monkeypatch.setattr("dashboard.redis_client.get_instance_state", lambda *_a, **_k: {})
    monkeypatch.setattr("dashboard.redis_client.get_player_state_hash", lambda *_a, **_k: next(states))

    class _Result:
        success = True
        metadata = {
            "scenario_completed": True,
            "reason": "success",
            "steps_trace": [{"i": "0", "status": "ok", "summary": "exec sync_troop_pool"}],
        }

    async def _fake_async(iid, scn, fid, timeout):  # noqa: ANN001, ANN202
        return _Result()

    monkeypatch.setattr(core, "_drive_async", _fake_async)

    out = core.drive("sync_troop_pool.cron", "bs1", player_id="42", approval=False)
    assert out["ok"] is True
    assert out["completed"] is True
    assert out["approval_bypassed"] is True
    assert len(out["steps"]) == 1
    assert out["state_diff"] == {
        "player:troops.infantry.available": {"before": None, "after": "73443"}
    }
    # approval flag was forced to "0" then removed (no prior value to restore).
    assert "wos:ui:click_approval:enabled:bs1" in fr.deleted


def test_drive_diff_includes_durable_sqlite_state(
    monkeypatch: pytest.MonkeyPatch, one_instance: None
) -> None:
    """The diff surfaces durable SQLite player state (``db:``) a reader wrote,
    not just the Redis hashes — that's where readers persist their output."""

    class FR:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        def get(self, k: str):
            return self.store.get(k)

        def set(self, k: str, v: str) -> None:
            self.store[k] = v

        def delete(self, k: str) -> None:
            self.store.pop(k, None)

    monkeypatch.setattr("dashboard.redis_client.require_redis_connection", lambda: FR())
    monkeypatch.setattr("dashboard.redis_client.get_instance_state", lambda *_a, **_k: {})
    monkeypatch.setattr("dashboard.redis_client.get_player_state_hash", lambda *_a, **_k: {})

    # durable store: empty before, the reader's writes after. The epoch-timestamp
    # field churns every run, so it must be filtered out of the diff.
    flats = iter([
        {},
        {
            "heroes.entries.charlie.star": 4,
            "heroes.entries.charlie.level": 73,
            "heroes.entries.charlie.detail_seen_at": 1782328656.12,
        },
    ])

    class _Store:
        def __init__(self, flat: dict) -> None:
            self._flat = flat

        def to_flat_dict(self) -> dict:
            return self._flat

    class _StateStore:
        def get(self, _fid: str):  # noqa: ANN202
            return _Store(next(flats))

    monkeypatch.setattr("config.state_store.get_state_store", lambda: _StateStore())

    class _Result:
        success = True
        metadata = {"scenario_completed": True, "reason": "success", "steps_trace": []}

    async def _fake_async(iid, scn, fid, timeout):  # noqa: ANN001, ANN202
        return _Result()

    monkeypatch.setattr(core, "_drive_async", _fake_async)

    out = core.drive("scan_hero_details", "bs1", player_id="42", approval=False)
    assert out["state_diff"]["db:heroes.entries.charlie.star"] == {"before": None, "after": "4"}
    assert out["state_diff"]["db:heroes.entries.charlie.level"] == {"before": None, "after": "73"}
    # the ``*_at`` timestamp is heartbeat churn — excluded from the diff.
    assert "db:heroes.entries.charlie.detail_seen_at" not in out["state_diff"]


def test_why_idle_falls_back_to_last_history_task(
    fake_redis_z: FakeRedisZ, one_instance: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dashboard.redis_client import QueueHistoryRow

    h = QueueHistoryRow(
        task_id="queue:abc", task_type="x", scenario="event.fishing_tournament",
        player_id="42", instance_id="bs1", priority=90_000, started_at=10.0,
        finished_at=20.0, duration_s=10.0, success=True, steps_trace=[],
    )
    monkeypatch.setattr("dashboard.redis_client.fetch_running_queue_row", lambda *_a, **_k: None)
    monkeypatch.setattr("dashboard.redis_client.get_instance_state", lambda *_a, **_k: {})
    monkeypatch.setattr("dashboard.redis_client.fetch_queue_history_rows", lambda *_a, **_k: [h])

    out = core.why("bs1")
    assert out["running"] is False
    assert out["from_history"] is True
    assert out["scenario"] == "event.fishing_tournament"
    assert out["source"]["code"] == "focus"   # priority 90_000 → focus enqueue
