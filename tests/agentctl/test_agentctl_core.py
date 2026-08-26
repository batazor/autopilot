"""Unit tests for :mod:`agentctl.core`.

These never touch a live Redis or device: ``core`` lazily imports its
dependencies inside each function, so we monkeypatch the underlying helpers
(``dashboard.redis_client.*``, ``api.services.queue_api.enqueue_user_task``, …)
and assert (a) the returned shapes and (b) that control functions push the
exact Redis payloads.
"""

from __future__ import annotations

import json
from typing import ClassVar

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
        metadata: ClassVar[dict] = {
            "scenario_completed": True,
            "reason": "success",
            "steps_trace": [{"i": "0", "status": "ok", "summary": "exec sync_troop_pool"}],
        }

    async def _fake_async(iid, scn, fid, timeout_s):
        return _Result()

    monkeypatch.setattr(core, "_drive_async", _fake_async)
    monkeypatch.setattr(core, "_device_holder", lambda *_a, **_k: None)  # device free

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
        def get(self, _fid: str):
            return _Store(next(flats))

    monkeypatch.setattr("config.state_store.get_state_store", lambda: _StateStore())

    class _Result:
        success = True
        metadata: ClassVar[dict] = {"scenario_completed": True, "reason": "success", "steps_trace": []}

    async def _fake_async(iid, scn, fid, timeout_s):
        return _Result()

    monkeypatch.setattr(core, "_drive_async", _fake_async)
    monkeypatch.setattr(core, "_device_holder", lambda *_a, **_k: None)  # device free

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


# --------------------------------------------------------------------------- #
# Live observation — current_detection / instance_diagnosis / fleet_health
# --------------------------------------------------------------------------- #
def test_current_detection_structures_instance_hash(
    fake_redis: FakeRedis, one_instance: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = {
        "current_screen": "main_city",
        "last_overlay_match_region": "workers_icon",
        "last_overlay_match_score": "0.93",
        "last_overlay_match_threshold": "0.85",
        "last_overlay_text": "",                       # empty → omitted
        "furnace.level_text": "30",
        "furnace.level_confidence": "0.97",
        "furnace.level_at": str(NOW),
        "paused": "1",
        "nav_error": "",
        "active_player": "42",
    }
    monkeypatch.setattr("dashboard.redis_client.get_instance_state", lambda *_a, **_k: row)

    out = core.current_detection("bs1")
    assert out["current_screen"] == "main_city"
    assert out["last_overlay"]["region"] == "workers_icon"
    assert out["last_overlay"]["score"] == 0.93
    assert "text" not in out["last_overlay"]            # empty value dropped
    regs = {r["name"]: r for r in out["regions"]}
    assert regs["furnace.level"]["text"] == "30"
    assert regs["furnace.level"]["confidence"] == 0.97
    assert regs["furnace.level"]["age_s"] is not None
    assert out["context"]["paused"] is True
    assert out["context"]["active_player"] == "42"


def test_instance_diagnosis_enriches_with_blind_planners(
    fake_redis: FakeRedis, one_instance: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api.services.attention.diagnose_instance",
        lambda *_a, **_k: {
            "instance_id": "bs1", "verdict": "ok", "issues": [], "active_player": "",
        },
    )
    monkeypatch.setattr(core, "_resolve_active_fid", lambda *_a, **_k: "42")
    monkeypatch.setattr(
        core, "planners",
        lambda *_a, **_k: {
            "planners": [
                {"name": "resources", "blind": True, "missing_inputs": ["troops.infantry.available"]},
                {"name": "march", "blind": False, "missing_inputs": []},
            ]
        },
    )

    out = core.instance_diagnosis("bs1")
    assert out["active_player"] == "42"
    assert out["blind_planners"] == [
        {"name": "resources", "missing_inputs": ["troops.infantry.available"]}
    ]


def test_fleet_health_passthrough(
    fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api.services.attention.fleet_health",
        lambda _c: {"verdict": "HEALTHY", "live_workers": 2, "instances_total": 2},
    )
    out = core.fleet_health()
    assert out["verdict"] == "HEALTHY"
    assert out["live_workers"] == 2


# --------------------------------------------------------------------------- #
# reader_health — fact → reader/consumer inversion + freshness + ordering
# --------------------------------------------------------------------------- #
def test_reader_health_inverts_sorts_and_dates(
    fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = [
        {"name": "resources", "observed_inputs": ["troops.infantry.available", "heroes.roster"],
         "note": "входы читаются sync_hero_roster + sync_troop_pool"},
        {"name": "troops", "observed_inputs": ["troops.infantry.available"],
         "note": "sync_troop_pool max-per-type"},
        {"name": "vip", "observed_inputs": ["vip.level"], "note": "Ридер sync_vip_level пишет vip.level"},
    ]
    monkeypatch.setattr(core, "_load_planner_manifest", lambda *_a, **_k: manifest)
    monkeypatch.setattr(core, "_resolve_active_fid", lambda *_a, **_k: "42")
    monkeypatch.setattr(core, "_player_flat", lambda *_a, **_k: {"vip.level": "5", "vip.synced_at": str(NOW)})

    out = core.reader_health()
    by = {f["fact"]: f for f in out["facts"]}
    troops = by["troops.infantry.available"]
    assert troops["consumer_count"] == 2                 # resources + troops
    assert "sync_troop_pool" in troops["readers"]
    assert troops["present"] is False                    # no reader has run
    vip = by["vip.level"]
    assert vip["present"] is True
    assert vip["synced_at"] == NOW                       # found via vip.synced_at parent probe
    assert vip["age_s"] is not None
    # Most-blocking first: blind facts precede present ones, by consumer count.
    assert out["facts"][0]["fact"] == "troops.infantry.available"
    assert out["facts"][-1]["fact"] == "vip.level"


# --------------------------------------------------------------------------- #
# Freshness verdict — fresh / stale / present / missing
# --------------------------------------------------------------------------- #
def test_freshness_verdict_boundaries() -> None:
    fv = core._freshness_verdict
    assert fv(None, None, None) == "unknown"      # no player resolved
    assert fv(False, None, 100) == "missing"
    assert fv(True, None, None) == "present"        # present, no TTL
    assert fv(True, None, 100) == "present"         # TTL set but age unknown
    assert fv(True, 50.0, 100) == "fresh"
    assert fv(True, 100.0, 100) == "fresh"          # exactly at TTL is still fresh
    assert fv(True, 200.0, 100) == "stale"


def test_reader_health_computes_freshness_state(
    fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time as _t

    manifest = [
        {"name": "troops", "observed_inputs": ["troops.infantry.available"],
         "note": "sync_troop_pool", "freshness_ttl_seconds": 100},
        {"name": "vip", "observed_inputs": ["vip.level"],
         "note": "sync_vip_level", "freshness_ttl_seconds": 100},
        {"name": "charms", "observed_inputs": ["charms.owned"],
         "note": "sync_charms", "freshness_ttl_seconds": 100},
    ]
    monkeypatch.setattr(core, "_load_planner_manifest", lambda *_a, **_k: manifest)
    monkeypatch.setattr(core, "_resolve_active_fid", lambda *_a, **_k: "42")
    flat = {
        "troops.infantry.available": "1000",
        "troops.synced_at": str(_t.time() - 50),    # age ~50 < ttl 100 → fresh
        "vip.level": "8",
        "vip.synced_at": str(_t.time() - 200),       # age ~200 > ttl 100 → stale
        # charms.owned absent → missing
    }
    monkeypatch.setattr(core, "_player_flat", lambda *_a, **_k: flat)

    out = core.reader_health()
    by = {f["fact"]: f for f in out["facts"]}
    assert by["troops.infantry.available"]["state"] == "fresh"
    assert by["troops.infantry.available"]["ttl_s"] == 100
    assert by["vip.level"]["state"] == "stale"
    assert by["charms.owned"]["state"] == "missing"
    # actionable first: missing, then stale, then fresh.
    assert [f["state"] for f in out["facts"]] == ["missing", "stale", "fresh"]


def test_planners_flags_stale_input(
    fake_redis_z: FakeRedisZ, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time as _t

    manifest = [{
        "name": "troops", "wired": "calculator", "config": "", "trace_key": "",
        "observed_inputs": ["troops.infantry.available"], "freshness_ttl_seconds": 100,
    }]
    monkeypatch.setattr(core, "_load_planner_manifest", lambda *_a, **_k: manifest)
    monkeypatch.setattr(core, "_yaml_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr(core, "_resolve_active_fid", lambda *_a, **_k: "42")
    flat = {"troops.infantry.available": "1000", "troops.synced_at": str(_t.time() - 200)}
    monkeypatch.setattr(core, "_player_flat", lambda *_a, **_k: flat)

    p = core.planners()["planners"][0]
    assert p["blind"] is False                 # present
    assert p["stale"] is True                  # but older than TTL
    assert p["stale_inputs"] == ["troops.infantry.available"]
    assert p["freshness_ttl_seconds"] == 100


# --------------------------------------------------------------------------- #
# drive() device-busy pre-flight guard
# --------------------------------------------------------------------------- #
def test_drive_blocks_on_live_supervisor_worker(
    fake_redis: FakeRedis, one_instance: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(core, "_instance_uses_scrcpy", lambda *_a, **_k: True)
    monkeypatch.setattr(core, "_device_holder", lambda *_a, **_k: "worker")
    with pytest.raises(AgentctlError, match="holding the device"):
        core.drive("x", "bs1")  # auto_pause_worker=False → must refuse


def test_drive_auto_pause_stops_and_restarts_isolated_worker(
    one_instance: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FR:
        def get(self, k: str) -> None:
            return None

        def set(self, k: str, v: str) -> None:
            pass

        def delete(self, k: str) -> None:
            pass

    monkeypatch.setattr("dashboard.redis_client.require_redis_connection", lambda: FR())
    monkeypatch.setattr(core, "_instance_uses_scrcpy", lambda *_a, **_k: True)
    monkeypatch.setattr(core, "_device_holder", lambda *_a, **_k: "isolated")
    monkeypatch.setattr("dashboard.redis_client.get_instance_state", lambda *_a, **_k: {})
    monkeypatch.setattr("dashboard.redis_client.get_player_state_hash", lambda *_a, **_k: {})

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr("worker.local_bot.stop_instance_worker", lambda iid: calls.append(("stop", iid)))
    monkeypatch.setattr("worker.local_bot.start_instance_worker", lambda iid: calls.append(("start", iid)))

    from types import SimpleNamespace

    async def _fake_async(*_a, **_k):
        return SimpleNamespace(
            success=True,
            metadata={"scenario_completed": True, "reason": "ok", "steps_trace": []},
        )

    monkeypatch.setattr(core, "_drive_async", _fake_async)

    out = core.drive("x", "bs1", player_id="42", auto_pause_worker=True)
    assert out["worker_paused"] is True
    assert ("stop", "bs1") in calls
    assert ("start", "bs1") in calls


def _fake_registry(names: list[str]):
    from types import SimpleNamespace

    return SimpleNamespace(
        devices=[
            SimpleNamespace(
                name=n,
                effective_serial=f"127.0.0.1:{5600 + i}",
                screenshot_backend="",
                input_backend="",
            )
            for i, n in enumerate(names)
        ]
    )


def _patch_devices(monkeypatch, *, names, online, driven) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr("config.devices.load_devices", lambda: _fake_registry(names))
    monkeypatch.setattr(core, "_online_serials", lambda: set(online))
    monkeypatch.setattr(
        "config.loader.load_settings",
        lambda: SimpleNamespace(
            instances=[SimpleNamespace(instance_id=n) for n in driven]
        ),
    )


def test_devices_flags_online_emulators_the_bot_does_not_drive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An emulator can be ONLINE and still be pure waste.

    ONLINE alone never revealed this: a running emulator outside WOS_INSTANCES
    keeps rendering its game at full cost while the bot ignores it.
    """
    _patch_devices(
        monkeypatch,
        names=["bs1", "bs2", "bs3"],
        online=["127.0.0.1:5601", "127.0.0.1:5602"],  # bs2, bs3 up; bs1 down
        driven=["bs2"],                                # bot only drives bs2
    )
    out = core.devices()

    by_name = {d["name"]: d for d in out["devices"]}
    assert by_name["bs2"]["driven"] is True
    assert by_name["bs3"]["driven"] is False
    assert out["undriven_online"] == ["bs3"]
    # bs1 is not driven either, but it isn't running — nothing to reclaim.
    assert "bs1" not in out["undriven_online"]


def test_devices_reports_no_waste_when_every_running_emulator_is_driven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_devices(
        monkeypatch,
        names=["bs1", "bs2"],
        online=["127.0.0.1:5600", "127.0.0.1:5601"],
        driven=["bs1", "bs2"],
    )
    out = core.devices()
    assert out["undriven_online"] == []


def test_devices_skips_cpu_sampling_unless_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plain call must stay instant — CPU sampling needs a time window."""
    _patch_devices(monkeypatch, names=["bs1"], online=["127.0.0.1:5600"], driven=[])
    called = False

    def _sampler(entries):
        nonlocal called
        called = True
        return {"bs1": 42.0}

    monkeypatch.setattr(core, "_emulator_cpu_percent", _sampler)

    out = core.devices()
    assert called is False
    assert out["devices"][0]["cpu_pct"] is None
    assert out["undriven_cpu_pct"] is None

    out = core.devices(cpu=True)
    assert called is True
    assert out["devices"][0]["cpu_pct"] == 42.0
    assert out["undriven_cpu_pct"] == 42.0
