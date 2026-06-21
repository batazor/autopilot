"""Greedy priority allocation across the cost vectors, with the verdict trace."""
from __future__ import annotations

from games.wos.core.resources.allocator import (
    CONSUME,
    IDLE,
    NOT_CONSIDERED,
    QUOTA_FULL,
    RESERVE_HELD,
    SELECTED,
    WINDOW_CLOSED,
    ActionRuntime,
    allocate,
)
from games.wos.core.resources.model import (
    NO_FREE_HERO,
    Action,
    ActionTable,
    WorldView,
)

TABLE = ActionTable.from_dict({
    "resources": {
        "march_slots": {"kind": "slot_lease", "observed": True},
        "stamina": {"kind": "pool_regen", "observed": True, "cap": 200, "regen_per_hour": 12},
        "troops": {"kind": "typed_pool", "observed": True, "types": ["infantry"]},
        "heroes": {"kind": "exclusive_set", "observed": True, "roles": ["combat", "gatherer"]},
    },
})

BEAR = Action.from_dict({
    "id": "bear", "task_type": "bear.rally", "priority": 90,
    "active_when": "bear_active", "reserve": {"march_slots": 1},
    "costs": [
        {"resource": "march_slots", "amount": 1},
        {"resource": "troops", "type": "any", "amount": 60_000},
        {"resource": "heroes", "role": "combat", "count": 3},
    ],
})
BEAST = Action.from_dict({
    "id": "beast", "task_type": "beast_hunt", "priority": 40,
    "costs": [
        {"resource": "march_slots", "amount": 1},
        {"resource": "stamina", "amount": 10},
        {"resource": "troops", "type": "any", "amount": 60_000},
        {"resource": "heroes", "role": "combat", "count": 3},
    ],
})
GATHER = Action.from_dict({
    "id": "gather", "task_type": "gather_resources", "priority": 20,
    "costs": [
        {"resource": "march_slots", "amount": 1},
        {"resource": "troops", "type": "any", "amount": 60_000},
        {"resource": "heroes", "role": "gatherer", "count": 3},
    ],
})
INTEL = Action.from_dict({
    "id": "intel", "task_type": "intel_run", "priority": 60, "daily_quota": 10,
    "costs": [
        {"resource": "march_slots", "amount": 1},
        {"resource": "stamina", "amount": 10},
    ],
})


def _world(slots_free: int = 3, stamina: float | None = 100.0, combat=("a", "b", "c", "d"),
           gatherer=("e", "f", "g", "h")) -> WorldView:
    return WorldView(
        slots_capacity=6, slots_free=slots_free, stamina_est=stamina,
        troops_free={"infantry": 100_000}, troops_observed=True,
        free_heroes={"combat": combat, "gatherer": gatherer}, heroes_observed=True,
    )


def _rt(action: Action, active: bool = True, quota_used: int = 0) -> ActionRuntime:
    return ActionRuntime(action=action, active=active, quota_used=quota_used)


def _by_id(decision):
    return {v.action_id: v for v in decision.verdicts}


def test_highest_priority_affordable_wins():
    d = allocate(_world(), [_rt(BEAST), _rt(GATHER)], TABLE)
    assert d.action == CONSUME
    assert d.target_id == "beast"
    assert d.priority == 40
    assert d.slot_cost == 1
    assert d.stamina_delta == -10
    assert d.assignment.heroes == ("a", "b", "c")
    by = _by_id(d)
    assert by["beast"].reason == SELECTED
    assert by["gather"].reason == NOT_CONSIDERED   # never reached


def test_inactive_window_is_skipped():
    d = allocate(_world(), [_rt(BEAR, active=False), _rt(GATHER)], TABLE)
    assert d.target_id == "gather"
    assert _by_id(d)["bear"].reason == WINDOW_CLOSED


def test_quota_full_blocks_action():
    d = allocate(_world(), [_rt(INTEL, quota_used=10)], TABLE)
    assert d.action == IDLE
    assert _by_id(d)["intel"].reason == QUOTA_FULL


def test_reserve_holds_last_slot_for_higher_priority():
    # Bear's window is open but it has no free combat heroes, so it can't run.
    # It still reserves the one free slot, so gather must NOT take it.
    world = _world(slots_free=1, combat=())
    d = allocate(world, [_rt(BEAR, active=True), _rt(GATHER)], TABLE)
    assert d.action == IDLE
    assert d.reason == "idle_reserve_held"
    by = _by_id(d)
    assert by["bear"].reason == NO_FREE_HERO
    assert by["gather"].reason == RESERVE_HELD


def test_bear_wins_when_fully_affordable():
    world = _world(slots_free=1)
    d = allocate(world, [_rt(BEAR, active=True), _rt(GATHER)], TABLE)
    assert d.action == CONSUME
    assert d.target_id == "bear"
    assert d.stamina_delta == 0          # bear costs no stamina
    assert d.assignment.heroes == ("a", "b", "c")


def test_idle_no_active_window_when_nothing_open():
    d = allocate(_world(), [_rt(BEAR, active=False)], TABLE)
    assert d.action == IDLE
    assert d.reason == "idle_no_active_window"


def test_idle_blocked_on_resources():
    d = allocate(_world(slots_free=0), [_rt(BEAST)], TABLE)
    assert d.action == IDLE
    assert d.reason == "idle_blocked_on_resources"
