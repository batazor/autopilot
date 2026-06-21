"""StepDirective → coord bus directive mapping (wired vs deferred)."""
from __future__ import annotations

from games.wos.core.fleet import step_kinds as sk
from games.wos.core.fleet.barriers import signal_value
from games.wos.core.fleet.directives import (
    DEFERRED,
    NO_SCENARIO,
    POSTED,
    to_coord_directive,
)

from coord.campaign import StepDirective


def _sd(kind, scenario="", **kw):
    base = {
        "fid": "111", "instance_id": "dev-a", "scenario": scenario, "params": {},
        "idempotency_key": "run1:0:111:" + kind,
    }
    base.update(kw)
    return StepDirective(kind=kind, **base)


def test_run_scenario_becomes_enqueue_directive():
    d, status = to_coord_directive(_sd(sk.RUN_SCENARIO, "event.gather"))
    assert status == POSTED
    assert d is not None
    assert d.kind == "enqueue_scenario"
    assert d.target.kind == "instance" and d.target.value == "dev-a"
    assert d.payload["scenario"] == "event.gather"
    assert d.payload["player_id"] == "111"
    assert d.idempotency_key == "run1:0:111:run_scenario"


def test_run_scenario_carries_sequence_hints():
    sd = _sd(sk.RUN_SCENARIO, "event.gather", requires_switch=True,
             sequence_group="dev-a", sequence_order=1)
    d, _ = to_coord_directive(sd)
    assert d.payload["sequence_group"] == "dev-a"
    assert d.payload["sequence_order"] == 1


def test_run_scenario_without_scenario_is_no_scenario():
    d, status = to_coord_directive(_sd(sk.RUN_SCENARIO, ""))
    assert d is None and status == NO_SCENARIO


def test_deferred_kinds_are_not_posted():
    for kind in (sk.SWITCH_PLAYER, sk.RECALL, sk.ATTACK_COORDS, sk.REINFORCE):
        d, status = to_coord_directive(_sd(kind, "some.scenario"))
        assert d is None and status == DEFERRED


def test_signal_value_reads_flags():
    assert signal_value({"city_empty": "1"}, "city_empty") is True
    assert signal_value({"event_quota_reached": "true"}, "quota_reached") is True
    assert signal_value({}, "city_empty") is False
    assert signal_value({"troops_sent": "0"}, "troops_sent") is False
