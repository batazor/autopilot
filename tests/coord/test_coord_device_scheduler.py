"""Single-device scheduling with switch cost — verified against brute force."""
from __future__ import annotations

import itertools

from coord.campaign import Job, schedule_device


def _brute(jobs, switch_s):
    """Max on-time value over every ordered subset (small n)."""
    best = 0.0
    n = len(jobs)
    for k in range(n + 1):
        for subset in itertools.permutations(range(n), k):
            t = 0.0
            val = 0.0
            ok = True
            for idx in subset:
                t += switch_s + jobs[idx].service_s
                if t > jobs[idx].deadline_s:
                    ok = False
                    break
                val += jobs[idx].value
            if ok:
                best = max(best, val)
    return best


def test_empty():
    s = schedule_device([])
    assert s.order == () and s.total_value == 0.0


def test_all_fit_no_deadline_pressure():
    jobs = [
        Job("a", service_s=10, value=1, deadline_s=1000),
        Job("b", service_s=10, value=1, deadline_s=1000),
    ]
    s = schedule_device(jobs, switch_s=2)
    assert set(s.order) == {"a", "b"}
    assert s.dropped == ()
    assert s.makespan == 24.0           # 2*(switch 2 + service 10)


def test_drops_when_window_too_short():
    # window only fits one job (switch 5 + service 50 = 55 ≤ 60; a second won't fit)
    jobs = [
        Job("low", service_s=50, value=1, deadline_s=60),
        Job("high", service_s=50, value=10, deadline_s=60),
    ]
    s = schedule_device(jobs, switch_s=5)
    assert s.order == ("high",)         # keep the valuable one
    assert s.dropped == ("low",)
    assert s.total_value == 10.0


def test_edd_ordering_keeps_both_on_time():
    # serviced in earliest-deadline-first order so both make it
    jobs = [
        Job("late", service_s=10, value=1, deadline_s=40),
        Job("early", service_s=10, value=1, deadline_s=22),
    ]
    s = schedule_device(jobs, switch_s=1)
    assert s.order == ("early", "late")  # early first (deadline 22 then 40)
    assert s.dropped == ()


def test_matches_brute_force_deterministic():
    # a deterministic mix of values/deadlines/services
    jobs = [
        Job("j0", service_s=7, value=5, deadline_s=20),
        Job("j1", service_s=4, value=3, deadline_s=12),
        Job("j2", service_s=9, value=8, deadline_s=30),
        Job("j3", service_s=3, value=2, deadline_s=10),
        Job("j4", service_s=6, value=6, deadline_s=18),
    ]
    for switch_s in (0, 1, 3):
        s = schedule_device(jobs, switch_s=switch_s)
        assert s.total_value == _brute(jobs, switch_s)
        # the reported order is actually on-time
        t = 0.0
        by_id = {j.account_id: j for j in jobs}
        for acc in s.order:
            t += switch_s + by_id[acc].service_s
            assert t <= by_id[acc].deadline_s


def test_switch_cost_can_force_a_drop():
    jobs = [
        Job("a", service_s=10, value=1, deadline_s=25),
        Job("b", service_s=10, value=1, deadline_s=25),
    ]
    # no switch cost → both fit (10, 20); big switch cost → only one fits
    assert len(schedule_device(jobs, switch_s=0).order) == 2
    assert len(schedule_device(jobs, switch_s=6).order) == 1
