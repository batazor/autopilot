"""Fleet arbitration (weighted set-packing) — greedy + exact branch-and-bound."""
from __future__ import annotations

from coord.campaign import ResourceClaim, arbitrate, arbitrate_optimal


def _claim(run_id, pri, *resources):
    return ResourceClaim(run_id=run_id, priority=pri, resources=frozenset(resources))


def _value(claims, result):
    by_id = {c.run_id: c.priority for c in claims}
    return sum(by_id[r] for r in result.active)


def test_empty():
    r = arbitrate([])
    assert r.active == () and r.starved == ()


def test_no_conflict_all_active():
    claims = [_claim("a", 5, "account:1"), _claim("b", 3, "account:2")]
    r = arbitrate(claims)
    assert set(r.active) == {"a", "b"}
    assert r.starved == ()


def test_account_mutex_higher_priority_wins():
    # reinforcement (950) vs raid (500) both want fighter account 111
    claims = [
        _claim("reinforce:1", 950, "account:111", "device:dev-b"),
        _claim("raid:1", 500, "account:111", "device:dev-b", "account:222", "device:dev-a"),
    ]
    r = arbitrate(claims)
    assert r.active == ("reinforce:1",)
    assert r.starved == ("raid:1",)
    assert "account:111" in r.contended           # the bottleneck resource
    assert r.owner["account:111"] == "reinforce:1"


def test_device_mutex_blocks_cross_campaign_thrash():
    # two runs want different accounts on the SAME device → only one this tick
    claims = [
        _claim("event:wolf", 600, "account:1", "device:dev-a"),
        _claim("raid:1", 500, "account:2", "device:dev-a"),
    ]
    r = arbitrate(claims)
    assert r.active == ("event:wolf",)
    assert "device:dev-a" in r.contended


def test_optimal_beats_greedy():
    # A(10){x,y} vs B(6){x} + C(6){y}: greedy takes A=10; optimal takes B+C=12.
    claims = [
        _claim("A", 10, "x", "y"),
        _claim("B", 6, "x"),
        _claim("C", 6, "y"),
    ]
    greedy = arbitrate(claims)
    optimal = arbitrate_optimal(claims)
    assert greedy.active == ("A",)
    assert _value(claims, greedy) == 10
    assert set(optimal.active) == {"B", "C"}
    assert _value(claims, optimal) == 12


def test_optimal_never_worse_than_greedy():
    claims = [
        _claim("p", 7, "a", "b"),
        _claim("q", 4, "a"),
        _claim("r", 4, "b"),
        _claim("s", 9, "c"),
    ]
    assert _value(claims, arbitrate_optimal(claims)) >= _value(claims, arbitrate(claims))


def test_optimal_falls_back_to_greedy_on_overflow():
    claims = [_claim("A", 10, "x", "y"), _claim("B", 6, "x"), _claim("C", 6, "y")]
    # tiny node budget forces the fallback → identical to greedy
    fell_back = arbitrate_optimal(claims, max_nodes=1)
    assert set(fell_back.active) == set(arbitrate(claims).active)


def test_empty_resource_claim_always_active():
    claims = [_claim("free", 1)]  # claims nothing → always placeable
    assert arbitrate(claims).active == ("free",)


def test_deterministic_tie_break_by_run_id():
    claims = [_claim("b", 5, "x"), _claim("a", 5, "x")]
    # equal priority, same resource → run_id tie-break makes "a" win
    assert arbitrate(claims).active == ("a",)
