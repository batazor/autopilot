"""Tests for the Redis-backed adapter.

``plan`` is exercised purely against decoded state dicts (the shape of a
``wos:player:<pid>:state`` hash). The async side-effects use minimal fakes for
the Redis pipeline and the queue — no real Redis, no ADB.
"""
from __future__ import annotations

import json

from games.wos.core.stamina import adapter
from games.wos.core.stamina import allocator as alloc
from games.wos.core.stamina.model import Budget, quota_field, quota_period

NOW = 1_700_000_000.0
BUDGET = Budget.load()
PERIOD = quota_period(NOW, BUDGET.daily_reset_utc)


def _state(**overrides) -> dict[str, str]:
    base = {
        "stamina": "156",
        "stamina_at": str(NOW),        # OCR-written read timestamp; read just now
        "joe_event_active": "0",
    }
    base.update({k: str(v) for k, v in overrides.items()})
    return base


def _qkey(demand_id: str) -> str:
    return quota_field(PERIOD, demand_id)


def test_plan_normal_day_selects_intel():
    state = _state(**{_qkey("intel_events"): 7})
    result = adapter.plan(BUDGET, state, NOW)
    assert result.est == 156.0
    assert result.decision.action == alloc.CONSUME
    assert result.decision.target_id == "intel_events"


def test_plan_joe_day_prioritises_bandits():
    state = _state(joe_event_active=1, **{_qkey("intel_events"): 7, _qkey("joe_bandits"): 12})
    result = adapter.plan(BUDGET, state, NOW)
    assert result.decision.action == alloc.CONSUME
    assert result.decision.target_id == "joe_bandits"
    assert result.decision.task_type == "joe_hunt_bandits"


def test_plan_drains_to_beast_when_intel_quota_full():
    state = _state(**{_qkey("intel_events"): 10})  # intel exhausted, joe off
    result = adapter.plan(BUDGET, state, NOW)
    assert result.decision.action == alloc.CONSUME
    assert result.decision.target_id == "beast_hunt"


def test_plan_interpolates_estimate_between_reads():
    state = _state(stamina=100, stamina_at=NOW - 3600)  # read 1h ago, +12/h
    result = adapter.plan(BUDGET, state, NOW)
    assert result.est == 112.0


def test_plan_false_flag_is_falsy():
    # "false" must not read as a truthy non-empty string.
    state = _state(joe_event_active="false", **{_qkey("intel_events"): 0})
    result = adapter.plan(BUDGET, state, NOW)
    assert result.decision.target_id == "intel_events"  # joe window closed


def test_plan_triggers_supply_when_starved():
    state = _state(stamina=5, joe_event_active=1)
    result = adapter.plan(BUDGET, state, NOW)
    assert result.decision.action == alloc.SUPPLY
    assert result.decision.target_id == "pet_refill"
    assert result.decision.task_type == "pet_stamina_skill"


def test_plan_missing_read_stays_idle():
    state = {"joe_event_active": "0"}  # never OCR'd
    result = adapter.plan(BUDGET, state, NOW)
    assert result.est is None
    assert result.decision.action == alloc.IDLE
    assert result.decision.reason == "no_stamina_reading"


def test_decision_payload_is_json_serialisable():
    state = _state()
    result = adapter.plan(BUDGET, state, NOW)
    payload = adapter.decision_payload(result, NOW)
    blob = json.dumps(payload)            # must not raise
    back = json.loads(blob)
    assert back["action"] in (alloc.CONSUME, alloc.SUPPLY, alloc.IDLE)
    assert {v["id"] for v in back["verdicts"]} == {
        "joe_bandits",
        "intel_events",
        "beast_hunt",
    }


# --- async side-effects with fakes ------------------------------------------


class _FakePipe:
    def __init__(self) -> None:
        self.zadd_calls: list[dict] = []
        self.executed = False

    def zadd(self, key, mapping):
        self.zadd_calls.append({"key": key, "mapping": mapping})
        return self

    def zremrangebyscore(self, *a, **k):
        return self

    def zremrangebyrank(self, *a, **k):
        return self

    def expire(self, *a, **k):
        return self

    async def execute(self):
        self.executed = True
        return []


class _FakeRedis:
    def __init__(self) -> None:
        self.pipe = _FakePipe()
        self.hincrby_calls: list[tuple] = []
        self.hdel_calls: list[tuple] = []

    def pipeline(self, transaction: bool = True):
        return self.pipe

    async def hincrby(self, key, field, amount=1):
        self.hincrby_calls.append((key, field, amount))
        return amount

    async def hdel(self, key, *fields):
        self.hdel_calls.append((key, fields))
        return len(fields)


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def schedule(self, **kwargs):
        self.calls.append(kwargs)
        return True


async def test_write_decision_trace_appends_json_member():
    redis = _FakeRedis()
    result = adapter.plan(BUDGET, _state(), NOW)
    await adapter.write_decision_trace(redis, "12345", result, NOW)

    assert redis.pipe.executed is True
    assert len(redis.pipe.zadd_calls) == 1
    call = redis.pipe.zadd_calls[0]
    assert call["key"] == "wos:player:12345:stamina_decisions"
    member = next(iter(call["mapping"]))
    score = call["mapping"][member]
    assert score == NOW
    assert json.loads(member)["action"] == result.decision.action


async def test_enqueue_decision_pushes_chosen_scenario():
    queue = _FakeQueue()
    result = adapter.plan(BUDGET, _state(joe_event_active=1), NOW)
    pushed = await adapter.enqueue_decision(
        queue,
        instance_id="bs1",
        player_id="12345",
        decision=result.decision,
        period=PERIOD,
        now=NOW,
    )
    assert pushed is True
    assert len(queue.calls) == 1
    call = queue.calls[0]
    assert call["task_type"] == "joe_hunt_bandits"
    assert call["dsl_scenario"] == "joe_hunt_bandits"
    assert call["priority"] == adapter.DEFAULT_PRIORITY + 100  # demand priority lifted into band
    assert call["instance_id"] == "bs1"
    assert call["skip_if_duplicate"] is True
    # Quota + delta markers the worker reads on success.
    assert call["args"]["stamina_quota_id"] == "joe_bandits"
    assert call["args"]["stamina_period"] == PERIOD
    assert call["args"]["stamina_delta"] == -10   # joe cost spent


async def test_enqueue_decision_noop_when_idle():
    queue = _FakeQueue()
    idle = alloc.Decision(action=alloc.IDLE, reason="idle_no_eligible_demand")
    pushed = await adapter.enqueue_decision(
        queue, instance_id="bs1", player_id="12345", decision=idle, period=PERIOD, now=NOW
    )
    assert pushed is False
    assert queue.calls == []


async def test_prune_stale_quota_drops_old_periods():
    redis = _FakeRedis()
    state = {
        "stamina": "100",
        quota_field("20260101", "intel_events"): "10",   # stale
        quota_field(PERIOD, "intel_events"): "3",          # current → keep
        "current_screen": "main_city",
    }
    await adapter.prune_stale_quota(redis, "12345", state, PERIOD)
    assert len(redis.hdel_calls) == 1
    key, fields = redis.hdel_calls[0]
    assert key == "wos:player:12345:state"
    assert set(fields) == {quota_field("20260101", "intel_events")}


async def test_prune_stale_quota_noop_when_all_current():
    redis = _FakeRedis()
    state = {quota_field(PERIOD, "intel_events"): "3"}
    await adapter.prune_stale_quota(redis, "12345", state, PERIOD)
    assert redis.hdel_calls == []


def test_build_view_shape_and_demand_rows():
    state = _state(joe_event_active=1, **{_qkey("intel_events"): 7})
    view = adapter.build_view(BUDGET, state, NOW)

    assert view["enabled"] is False
    assert view["cap"] == 200
    assert view["est"] == 156.0
    assert view["period"] == PERIOD
    assert view["action"] == alloc.CONSUME
    assert view["target"] == "joe_bandits"          # joe active → selected
    # seconds_to_cap finite here (below cap), JSON-safe (not inf).
    assert isinstance(view["seconds_to_cap"], (int, float))

    rows = {d["id"]: d for d in view["demands"]}
    assert set(rows) == {"joe_bandits", "intel_events", "beast_hunt"}
    assert rows["intel_events"]["quota_used"] == 7
    assert rows["intel_events"]["daily_quota"] == 10
    assert rows["joe_bandits"]["active"] is True
    assert rows["joe_bandits"]["selected"] is True
    assert rows["beast_hunt"]["daily_quota"] is None  # unlimited sink


def test_build_view_is_json_serialisable_with_no_read():
    view = adapter.build_view(BUDGET, {"joe_event_active": "0"}, NOW)
    assert view["est"] is None
    assert view["seconds_to_cap"] is None            # inf → None for JSON
    assert view["retry_after_s"] is None             # no estimate → no TTL hint
    json.dumps(view)                                  # must not raise


def test_build_view_retry_after_when_starved():
    # est 5, regen 12/h → 5 more points to reach a 10-cost action = 25 min.
    view = adapter.build_view(BUDGET, _state(stamina=5), NOW)
    assert view["action"] != alloc.CONSUME
    assert 1400 <= view["retry_after_s"] <= 1600


def test_build_view_retry_after_none_when_acting():
    view = adapter.build_view(BUDGET, _state(), NOW)   # est 156 → consumes now
    assert view["action"] == alloc.CONSUME
    assert view["retry_after_s"] is None


def test_decision_signature_ignores_estimate():
    d1 = alloc.Decision(action=alloc.CONSUME, reason=alloc.SELECTED, target_id="intel_events")
    d2 = alloc.Decision(
        action=alloc.CONSUME, reason=alloc.SELECTED, target_id="intel_events", stamina_delta=-10
    )
    assert adapter.decision_signature(d1) == adapter.decision_signature(d2)
    idle = alloc.Decision(action=alloc.IDLE, reason="idle_no_eligible_demand")
    assert adapter.decision_signature(idle) != adapter.decision_signature(d1)


def test_load_budget_caches_by_mtime(tmp_path):
    import os

    p = tmp_path / "budget.yaml"
    p.write_text("cap: 200\nregen_per_hour: 12\ndemands: []\n")
    b1 = adapter.load_budget(p)
    assert adapter.load_budget(p) is b1          # cached, no re-parse
    # Edit + bump mtime → cache invalidates and re-parses.
    p.write_text("cap: 150\nregen_per_hour: 12\ndemands: []\n")
    os.utime(p, (2_000_000_000, 2_000_000_000))
    b3 = adapter.load_budget(p)
    assert b3 is not b1
    assert b3.cap == 150


def test_quota_marker_roundtrip_blocks_full_demand():
    # The field the worker increments (quota_field) is the field plan() reads:
    # once joe's quota is full, the allocator stops selecting it.
    state = _state(joe_event_active=1, **{quota_field(PERIOD, "joe_bandits"): 30})
    result = adapter.plan(BUDGET, state, NOW)
    assert result.decision.target_id != "joe_bandits"
