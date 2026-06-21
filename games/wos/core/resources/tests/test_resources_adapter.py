"""Adapter: state→WorldView resolution, the ledger, queue push, and trace.

``build_world`` / ``plan`` are exercised purely against decoded state dicts. The
async side-effects use minimal Redis/queue fakes — no real Redis, no ADB.
"""
from __future__ import annotations

import json

from games.wos.core.resources import adapter
from games.wos.core.resources import allocator as alloc
from games.wos.core.resources.model import ActionTable

NOW = 1_700_000_000.0

# A fully-observed table (troops + heroes readable) for the selection tests.
OBS_TABLE = ActionTable.from_dict({
    "daily_reset_utc": "02:00",
    "resources": {
        "march_slots": {"kind": "slot_lease", "observed": True},
        "stamina": {"kind": "pool_regen", "observed": True, "cap": 200, "regen_per_hour": 12},
        "troops": {"kind": "typed_pool", "observed": True, "types": ["infantry"]},
        "heroes": {"kind": "exclusive_set", "observed": True, "roles": ["combat", "gatherer"]},
    },
    "actions": [
        {"id": "bear", "task_type": "bear.rally", "priority": 90,
         "active_when": "bear_active", "reserve": {"march_slots": 1},
         "costs": [
             {"resource": "march_slots", "amount": 1},
             {"resource": "troops", "type": "any", "amount": 60_000},
             {"resource": "heroes", "role": "combat", "count": 3},
         ]},
    ],
})


def _roster(*combat: str) -> str:
    return json.dumps([{"id": h, "role": "combat", "free": True} for h in combat])


# --- build_world (pure) ------------------------------------------------------


def test_build_world_reads_slots_and_stamina():
    table = adapter.load_table()
    state = {"marches.capacity": "6", "marches.active_count": "2",
             "stamina": "100", "stamina_at": str(NOW)}
    world = adapter.build_world(table, state, NOW)
    assert world.slots_capacity == 6
    assert world.slots_free == 4
    assert world.stamina_est == 100.0
    assert world.troops_observed is False     # reader not built yet
    assert world.heroes_observed is False


def test_build_world_interpolates_stamina():
    table = adapter.load_table()
    state = {"stamina": "100", "stamina_at": str(NOW - 3600)}   # read 1h ago, +12/h
    world = adapter.build_world(table, state, NOW)
    assert world.stamina_est == 112.0


def test_build_world_subtracts_ledger_holds():
    table = adapter.load_table()
    state = {"marches.capacity": "6", "marches.active_count": "2",
             "stamina": "100", "stamina_at": str(NOW)}
    ledger = [{"slots": 1, "stamina": 25}]
    world = adapter.build_world(table, state, NOW, ledger)
    assert world.slots_free == 3          # 6 − 2 in flight − 1 held
    assert world.stamina_est == 75.0      # 100 − 25 held


def test_build_world_defaults_capacity_when_unread():
    table = adapter.load_table()
    world = adapter.build_world(table, {}, NOW)
    assert world.slots_capacity == 6      # DEFAULT_SLOT_CAPACITY


# --- plan --------------------------------------------------------------------


def test_plan_under_shipped_config_is_idle_without_readers():
    # actions.yaml ships troops/heroes unobserved + block policy → no action can
    # be staffed, so the planner never fires a march it can't fill.
    table = adapter.load_table()
    state = {"marches.capacity": "6", "marches.active_count": "0",
             "stamina": "200", "stamina_at": str(NOW), "bear_hunt_event_active": "1"}
    result = adapter.plan(table, state, NOW)
    assert result.decision.action == alloc.IDLE


def test_plan_selects_when_resources_observed():
    state = {"marches.capacity": "6", "marches.active_count": "0",
             "stamina": "200", "stamina_at": str(NOW),
             "bear_active": "1",
             "troops.infantry.available": "100000",
             "heroes.roster": _roster("jessie", "natalia", "flint", "gina")}
    result = adapter.plan(OBS_TABLE, state, NOW)
    assert result.decision.action == alloc.CONSUME
    assert result.decision.target_id == "bear"
    assert result.decision.assignment.heroes == ("jessie", "natalia", "flint")


def test_plan_false_flag_is_falsy():
    state = {"marches.capacity": "6", "marches.active_count": "0",
             "stamina": "200", "stamina_at": str(NOW),
             "bear_active": "false",
             "troops.infantry.available": "100000",
             "heroes.roster": _roster("jessie", "natalia", "flint")}
    result = adapter.plan(OBS_TABLE, state, NOW)
    assert result.decision.action == alloc.IDLE   # window closed → nothing else


def test_decision_payload_is_json_serialisable():
    state = {"marches.capacity": "6", "marches.active_count": "0",
             "stamina": "200", "stamina_at": str(NOW), "bear_active": "1",
             "troops.infantry.available": "100000",
             "heroes.roster": _roster("jessie", "natalia", "flint")}
    result = adapter.plan(OBS_TABLE, state, NOW)
    payload = adapter.decision_payload(result, NOW)
    back = json.loads(json.dumps(payload))         # must not raise
    assert back["action"] == alloc.CONSUME
    assert back["assignment"]["heroes"] == ["jessie", "natalia", "flint"]
    assert {v["id"] for v in back["verdicts"]} == {"bear"}


# --- reservation ledger ------------------------------------------------------


def test_reservation_entry_captures_cost_bundle():
    state = {"marches.capacity": "6", "marches.active_count": "0",
             "stamina": "200", "stamina_at": str(NOW), "bear_active": "1",
             "troops.infantry.available": "100000",
             "heroes.roster": _roster("jessie", "natalia", "flint")}
    result = adapter.plan(OBS_TABLE, state, NOW)
    # A 6h lease holds the bundle for hours; confirm_by is the short dispatch window.
    entry = adapter.reservation_entry(result.decision, NOW, confirm_ttl=90, lease_seconds=21600)
    assert entry["action_id"] == "bear"
    assert entry["slots"] == 1
    assert entry["heroes"] == ["jessie", "natalia", "flint"]
    assert entry["confirm_by"] == NOW + 90
    assert entry["expires_at"] == NOW + 21600
    assert entry["confirmed"] is False


def test_unconfirmed_lease_expires_at_confirm_window():
    # A long lease that's never confirmed (march never appeared) is rolled back at
    # confirm_by — its hours-long expires_at doesn't keep it alive.
    raw = {"ghost": json.dumps({
        "id": "ghost", "slots": 1, "confirmed": False,
        "confirm_by": NOW + 30, "expires_at": NOW + 21600,
    })}
    assert adapter.filter_active_ledger(raw, NOW)[0]            # active now (within confirm window)
    assert not adapter.filter_active_ledger(raw, NOW + 60)[0]   # past confirm_by → rolled back


def test_confirmed_lease_holds_for_hours():
    # Once confirmed, a gather holds its slot/troops/heroes until the real ends_at.
    raw = {"gather": json.dumps({
        "id": "gather", "slots": 1, "heroes": ["cloris"], "confirmed": True,
        "confirm_by": NOW + 30, "expires_at": NOW + 21600,
    })}
    assert adapter.filter_active_ledger(raw, NOW + 3600)[0]     # still held 1h in
    assert not adapter.filter_active_ledger(raw, NOW + 22000)[0]  # freed after 6h


def test_seconds_until_slot_frees():
    ledger = [
        {"slots": 1, "confirmed": True, "expires_at": NOW + 21600},   # 6h gather
        {"slots": 1, "confirmed": True, "expires_at": NOW + 900},     # 15m beast
    ]
    assert adapter.seconds_until_slot_frees(ledger, NOW) == 900       # soonest slot
    assert adapter.seconds_until_slot_frees([], NOW) is None


def test_filter_active_ledger_drops_expired():
    raw = {
        "live:1": json.dumps({"id": "live:1", "slots": 1, "expires_at": NOW + 30}),
        "dead:1": json.dumps({"id": "dead:1", "slots": 1, "expires_at": NOW - 5}),
        "junk:1": "not-json",
    }
    active, expired = adapter.filter_active_ledger(raw, NOW)
    assert [e["id"] for e in active] == ["live:1"]
    assert set(expired) == {"dead:1", "junk:1"}


# --- async side-effects with fakes -------------------------------------------


class _FakePipe:
    def __init__(self) -> None:
        self.zadd_calls = []
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
    def __init__(self, hashes: dict | None = None) -> None:
        self.pipe = _FakePipe()
        self.hashes = hashes or {}
        self.hset_calls = []
        self.hdel_calls = []

    def pipeline(self, transaction=True):
        return self.pipe

    async def hgetall(self, key):
        return self.hashes.get(key, {})

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hset(self, key, field, value):
        self.hset_calls.append((key, field, value))
        self.hashes.setdefault(key, {})[field] = value
        return 1

    async def hdel(self, key, *fields):
        self.hdel_calls.append((key, fields))
        return len(fields)


class _FakeQueue:
    def __init__(self) -> None:
        self.calls = []

    async def schedule(self, **kwargs):
        self.calls.append(kwargs)
        return True


def _bear_decision():
    state = {"marches.capacity": "6", "marches.active_count": "0",
             "stamina": "200", "stamina_at": str(NOW), "bear_active": "1",
             "troops.infantry.available": "100000",
             "heroes.roster": _roster("jessie", "natalia", "flint")}
    return adapter.plan(OBS_TABLE, state, NOW)


async def test_reserve_writes_ledger_entry():
    redis = _FakeRedis()
    result = _bear_decision()
    res_id = await adapter.reserve(redis, "12345", result.decision, NOW)
    assert res_id is not None
    key, _field, value = redis.hset_calls[0]
    assert key == "wos:player:12345:resource_reservations"
    assert json.loads(value)["heroes"] == ["jessie", "natalia", "flint"]


async def test_confirm_reservation_extends_lease_to_observed_end():
    redis = _FakeRedis()
    result = _bear_decision()
    res_id = await adapter.reserve(redis, "12345", result.decision, NOW)
    ok = await adapter.confirm_reservation(redis, "12345", res_id, ends_at=NOW + 5000)
    assert ok is True
    entry = json.loads(redis.hashes["wos:player:12345:resource_reservations"][res_id])
    assert entry["confirmed"] is True
    assert entry["expires_at"] == NOW + 5000      # held until the real march end


async def test_read_ledger_prunes_expired():
    key = "wos:player:12345:resource_reservations"
    redis = _FakeRedis(hashes={key: {
        "live:1": json.dumps({"id": "live:1", "slots": 1, "expires_at": NOW + 30}),
        "dead:1": json.dumps({"id": "dead:1", "slots": 1, "expires_at": NOW - 5}),
    }})
    active = await adapter.read_ledger(redis, "12345", NOW)
    assert [e["id"] for e in active] == ["live:1"]
    assert redis.hdel_calls and set(redis.hdel_calls[0][1]) == {"dead:1"}


async def test_enqueue_decision_pushes_with_assignment():
    queue = _FakeQueue()
    result = _bear_decision()
    pushed = await adapter.enqueue_decision(
        queue, instance_id="bs1", player_id="12345",
        decision=result.decision, period=result.period, reservation="bear:170", now=NOW,
    )
    assert pushed is True
    call = queue.calls[0]
    assert call["task_type"] == "bear.rally"
    assert call["priority"] == adapter.DEFAULT_PRIORITY + 90
    assert call["args"]["resource_action_id"] == "bear"
    assert call["args"]["resource_reservation"] == "bear:170"
    assert call["args"]["assign_heroes"] == ["jessie", "natalia", "flint"]


async def test_enqueue_decision_noop_when_idle():
    queue = _FakeQueue()
    idle = alloc.Decision(action=alloc.IDLE, reason="idle_no_active_window")
    pushed = await adapter.enqueue_decision(
        queue, instance_id="bs1", player_id="12345",
        decision=idle, period="20260101", reservation=None, now=NOW,
    )
    assert pushed is False
    assert queue.calls == []


async def test_write_decision_trace_appends_member():
    redis = _FakeRedis()
    result = _bear_decision()
    await adapter.write_decision_trace(redis, "12345", result, NOW)
    assert redis.pipe.executed is True
    call = redis.pipe.zadd_calls[0]
    assert call["key"] == "wos:player:12345:resource_decisions"
    member = next(iter(call["mapping"]))
    assert json.loads(member)["target"] == "bear"


def test_load_table_caches_by_mtime(tmp_path):
    import os

    p = tmp_path / "actions.yaml"
    p.write_text("enabled: false\nresources: {}\nactions: []\n")
    t1 = adapter.load_table(p)
    assert adapter.load_table(p) is t1
    p.write_text("enabled: true\nresources: {}\nactions: []\n")
    os.utime(p, (2_000_000_000, 2_000_000_000))
    t3 = adapter.load_table(p)
    assert t3 is not t1
    assert t3.enabled is True
