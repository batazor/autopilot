"""Catalog-wide static problem sweep + reverse refs (api.services.edit_dsl_api)."""
from __future__ import annotations

from api.services.edit_dsl_api import (
    _step_problems,
    _walk_push_refs,
    _walk_step_problems,
)

SETS = {
    "regions": {"workers", "popup_close"},
    "red_dot": {"workers"},
    "execs": {"scan"},
    "keys": {"other_one"},
}


def test_step_problems_flags_unknowns() -> None:
    assert _step_problems({"click": "ghost"}, **SETS) == ['click: unknown region "ghost"']
    assert _step_problems({"click": ""}, **SETS) == ["click: region not set"]
    assert _step_problems({"push_scenario": "nope"}, **SETS) == [
        'push_scenario: unknown scenario "nope"'
    ]
    assert _step_problems({"exec": "nope"}, **SETS) == ['exec: unknown function "nope"']
    assert _step_problems({"match": "popup_close", "isRedDot": True}, **SETS) == [
        'isRedDot filter, but "popup_close" has no has_red_dot in area'
    ]


def test_step_problems_accepts_valid_and_templated() -> None:
    assert _step_problems({"click": "workers"}, **SETS) == []
    assert _step_problems({"match": "workers", "isRedDot": True}, **SETS) == []
    assert _step_problems({"click": "page.${tab}"}, **SETS) == []
    assert _step_problems({"push_scenario": "claim.${day}"}, **SETS) == []
    assert _step_problems({"wait": "1s"}, **SETS) == []


def test_walk_push_refs_finds_string_and_dict_forms_nested() -> None:
    steps = [
        {"push_scenario": "target"},
        {"push_scenario": {"name": "target", "priority": 90000}},
        {"push_scenario": "other"},
        {"while_match": "r", "steps": [{"push_scenario": "target"}]},
        {"loop": {"max": 2, "steps": [{"push_scenario": "target"}]}},
    ]
    out: list[str] = []
    _walk_push_refs(steps, [], "target", out)
    assert out == ["0", "1", "3/0", "4/0"]


def test_walk_recurses_into_containers_with_step_paths() -> None:
    steps = [
        {"click": "workers"},
        {"while_match": "popup_close", "steps": [{"click": "ghost"}]},
        {"loop": {"max": 2, "steps": [{"cond": "x", "steps": [{"exec": "nope"}]}]}},
    ]
    out: list[dict] = []
    _walk_step_problems(steps, [], "games/x/scenarios/y.yaml", SETS, out)
    assert [(r["step"], r["issue"]) for r in out] == [
        ("1/0", 'click: unknown region "ghost"'),
        ("2/0/0", 'exec: unknown function "nope"'),
    ]
    assert all(r["rel"] == "games/x/scenarios/y.yaml" for r in out)
