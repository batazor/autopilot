"""Unit tests for the pure stamina model: estimation, cap math, quota periods,
condition resolution, and budget.yaml parsing."""
from __future__ import annotations

import math
from datetime import UTC, datetime

from games.wos.core.stamina import model
from games.wos.core.stamina.model import Budget, Demand, Supply

CAP = 200
REGEN = 10.0  # per hour → 1 point / 360s


def _ts(y, mo, d, h=0, mi=0) -> float:
    return datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp()


def test_estimate_none_without_prior_read():
    assert (
        model.estimate_stamina(None, 0.0, 3600.0, cap=CAP, regen_per_hour=REGEN)
        is None
    )


def test_estimate_interpolates_regen():
    # 1h elapsed at 10/h → +10.
    est = model.estimate_stamina(100, 0.0, 3600.0, cap=CAP, regen_per_hour=REGEN)
    assert est == 110.0


def test_estimate_clamps_to_cap():
    est = model.estimate_stamina(195, 0.0, 36_000.0, cap=CAP, regen_per_hour=REGEN)
    assert est == float(CAP)


def test_estimate_negative_elapsed_clamped():
    # now before read_at → no negative regen.
    est = model.estimate_stamina(100, 1000.0, 0.0, cap=CAP, regen_per_hour=REGEN)
    assert est == 100.0


def test_seconds_to_cap_basic():
    # 200 - 156 = 44 points, 10/h → 44 * 360s.
    assert model.seconds_to_cap(156, cap=CAP, regen_per_hour=REGEN) == 44 * 360


def test_seconds_to_cap_at_cap_is_zero():
    assert model.seconds_to_cap(CAP, cap=CAP, regen_per_hour=REGEN) == 0.0


def test_seconds_to_cap_no_regen_is_inf():
    assert model.seconds_to_cap(50, cap=CAP, regen_per_hour=0) == math.inf
    assert model.seconds_to_cap(None, cap=CAP, regen_per_hour=REGEN) == math.inf


def test_seconds_to_afford():
    # Already affordable.
    assert model.seconds_to_afford(20, 10, regen_per_hour=12) == 0.0
    # 5 short at 12/h (1 per 5 min) → 5 * 300s.
    assert model.seconds_to_afford(5, 10, regen_per_hour=12) == 5 * 300
    # No estimate / no regen → unreachable.
    assert model.seconds_to_afford(None, 10, regen_per_hour=12) == math.inf
    assert model.seconds_to_afford(5, 10, regen_per_hour=0) == math.inf


def test_quota_period_shifts_by_reset_hour():
    # 01:00 UTC with a 02:00 reset still belongs to the previous game-day.
    before = _ts(2026, 6, 14, 1, 0)
    assert model.quota_period(before, "02:00") == "20260613"
    assert model.quota_period(before, "00:00") == "20260614"
    # 03:00 UTC is past the 02:00 reset → new game-day.
    after = _ts(2026, 6, 14, 3, 0)
    assert model.quota_period(after, "02:00") == "20260614"


def test_quota_field_format():
    assert model.quota_field("20260614", "intel_events") == "quota:20260614:intel_events"


def test_is_active_resolves_condition():
    joe = Demand(id="joe", task_type="t", priority=100, cost=10, active_when="joe_event_active")
    assert model.is_active(joe, {"joe_event_active": True}) is True
    assert model.is_active(joe, {"joe_event_active": False}) is False
    # Missing field → eval_cond opts out (False), never raises.
    assert model.is_active(joe, {}) is False


def test_is_active_no_condition_is_always_active():
    intel = Demand(id="intel", task_type="t", priority=60, cost=10)
    assert model.is_active(intel, {}) is True


def test_is_triggered_resolves_condition():
    pet = Supply(id="pet", task_type="t", gives=50, trigger_when="stamina < cost")
    assert model.is_triggered(pet, {"stamina": 5, "cost": 10}) is True
    assert model.is_triggered(pet, {"stamina": 50, "cost": 10}) is False


def test_load_default_budget_yaml():
    b = Budget.load()
    assert b.cap == 200
    assert b.regen_per_hour == 12   # 1 stamina / 5 min
    ids = {d.id for d in b.demands}
    assert {"joe_bandits", "intel_events", "beast_hunt"} <= ids

    joe = b.demand("joe_bandits")
    assert joe is not None
    assert joe.priority == 100
    assert joe.reserve_floor == 50
    assert joe.active_when == "joe_event_active"

    beast = b.demand("beast_hunt")
    assert beast.daily_quota is None  # unlimited overflow sink

    assert len(b.supplies) == 1
    assert b.supplies[0].id == "pet_refill"
    assert b.supplies[0].gives == 50


def test_from_dict_defaults_and_unlimited_quota():
    b = Budget.from_dict({"demands": [{"id": "x", "priority": 5, "cost": 7}]})
    d = b.demands[0]
    assert d.task_type == "x"          # falls back to id
    assert d.daily_quota is None
    assert d.reserve_floor == 0
    assert d.active_when is None
