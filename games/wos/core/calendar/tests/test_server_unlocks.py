"""Server-age unlock schedule (beta profile) — derive tier + anticipate windows."""
from __future__ import annotations

import pytest
from games.wos.core.calendar.server_unlocks import load_unlock_schedule

SCHED = load_unlock_schedule("beta")


def test_hero_generation_derived_from_age():
    assert SCHED.hero_generation_at(0) == 8        # gens 1-8 open from day 0
    assert SCHED.hero_generation_at(21) == 8       # gen 9 not yet (day 22)
    assert SCHED.hero_generation_at(22) == 9
    assert SCHED.hero_generation_at(63) == 9
    assert SCHED.hero_generation_at(64) == 10
    assert SCHED.hero_generation_at(316) == 16
    assert SCHED.hero_generation_at(9999) == 16    # gen 17 is hidden — never derived
    assert SCHED.hero_generation_at(None) == 8


def test_pet_generation_derived_from_age():
    assert SCHED.pet_generation_at(4) == 0
    assert SCHED.pet_generation_at(5) == 1
    assert SCHED.pet_generation_at(159) == 6
    assert SCHED.pet_generation_at(160) == 7


def test_unlocked_modes_grow_with_age():
    assert SCHED.unlocked_modes(0) == {}
    assert SCHED.unlocked_modes(22) == {"red_equipment": 22}
    day64 = SCHED.unlocked_modes(64)
    assert set(day64) == {"red_equipment", "huojing_3", "expert_1"}
    assert "chiyan_technology" in SCHED.unlocked_modes(210)


def test_upcoming_anticipates_windows_soonest_first():
    up = SCHED.upcoming(20, within_days=30)        # window (20, 50]
    keys = [(e.key, e.days_until) for e in up]
    assert ("gen_9", 2) in keys                    # hero gen 9 at day 22
    assert ("red_equipment", 2) in keys            # red gear at day 22
    assert ("pet_gen_3", 10) in keys               # pet gen 3 at day 30
    assert [e.days_until for e in up] == sorted(e.days_until for e in up)
    # tie at +2 resolves hero_generation before mode (kind order)
    assert up[0].key == "gen_9" and up[1].key == "red_equipment"


def test_upcoming_empty_when_nothing_near_or_age_unknown():
    assert SCHED.upcoming(316, within_days=30) == []   # everything already open
    assert SCHED.upcoming(None) == []


def test_unknown_profile_raises():
    with pytest.raises(KeyError):
        load_unlock_schedule("does_not_exist")


def test_live_profile_uses_standard_server_timing():
    """The live profile gates gens far slower than beta (gen 1 from day 0)."""
    live = load_unlock_schedule("live")
    assert live.hero_generation_at(0) == 1         # one gen at a time, gen 1 from start
    assert live.hero_generation_at(39) == 1
    assert live.hero_generation_at(40) == 2
    assert live.hero_generation_at(800) == 11
    assert live.pet_generation_at(54) == 1
    assert live.pet_generation_at(280) == 5
    assert "fire_crystal_age" in live.unlocked_modes(60)
    # vastly different from beta: gen 9 at day 600 (live) vs day 22 (beta)
    assert live.hero_generation_at(599) == 8 and live.hero_generation_at(600) == 9
