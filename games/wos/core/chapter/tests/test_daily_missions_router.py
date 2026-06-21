"""Unit tests for the daily-mission router — pure parse/route over the REAL
``daily_missions.yaml`` registry, so the registry and its regexes are validated
against a realistic accumulated OCR buffer."""
from __future__ import annotations

import re

from games.wos.core.chapter.exec import (
    _load_registry,
    _resolve_args,
    _route_missions,
)

# The full scrolled daily list as ``chapter.claim_missions`` accumulates it,
# with the leading bullets and the (done / target) progress the game renders.
_BUFFER = """\
+ Make 5 Alliance Contribution(s) (0 / 5)
+ Train 10 Infantry (0 / 10)
+ Train 10 Lancers (0 / 10)
+ Train 10 Marksmen (0 / 10)
+ Carry out 5 Intel Mission(s) (1 / 5)
+ Heal 10 injured soldiers (0 / 10)
+ Gather 50,000 Meat (0 / 50000)
+ Gather 50,000 Wood (0 / 50000)
+ Gather 10,000 Coal (0 / 10000)
+ Gather 3,000 Iron (0 / 3000)
+ Upgrade 1 building(s) (0 / 1)
+ Research 1 technology(ies) (0 / 1)
+ Gather 1 time(s) (0 / 1)
+ Fight in 1 Arena Challenge(s) (0 / 1)
+ Complete 1 challenges in The Labyrinth. (0 / 1)
"""


def test_router_maps_every_mission_against_the_real_registry() -> None:
    registry = _load_registry()
    assert registry, "daily_missions.yaml registry failed to load"
    pushes, unautomated = _route_missions(_BUFFER, registry)

    by_scenario: dict[str, list[dict]] = {}
    for p in pushes:
        by_scenario.setdefault(p["scenario"], []).append(p["args"])

    # Troops route to per-troop keys carrying the parsed target count.
    assert {"troop": "infantry", "target": 10} in by_scenario["troops.infantry.train"]
    assert {"troop": "lancer", "target": 10} in by_scenario["troops.lancer.train"]
    assert {"troop": "marksman", "target": 10} in by_scenario["troops.marksman.train"]

    # Simple triggers (no args).
    assert by_scenario["alliance.tech.contribute"] == [{}]
    assert by_scenario["intel_run"] == [{}]
    assert by_scenario["event.labyrinth"] == [{}]

    # Heal + building carry their target count.
    assert by_scenario["heal_injured"] == [{"target": 10}]
    assert by_scenario["building.upgrade"] == [{"target": 1}]

    # Each specific resource gets its own gather push, plus the "Gather 1 time".
    gather_args = by_scenario["gather_resources"]
    for res in ("Meat", "Wood", "Coal", "Iron"):
        assert {"resource": res} in gather_args
    assert {} in gather_args  # "Gather 1 time(s)"

    # Recognised-but-unautomated → reported, never pushed.
    assert "sync_research_status" not in by_scenario
    joined = " ".join(unautomated).lower()
    assert "research" in joined
    assert "arena" in joined


def test_router_ignores_ocr_noise_between_lines() -> None:
    # Wrapped progress + garbage glyphs (real OCR artefacts) between missions.
    noisy = (
        "+ Make 5 Alliance Contribution(s) (0 / *\n2)\n"
        "O®\n"
        "+ Train 10 Infantry (0 / 10)\n"
    )
    pushes, _ = _route_missions(noisy, _load_registry())
    scenarios = {p["scenario"] for p in pushes}
    assert "alliance.tech.contribute" in scenarios
    assert "troops.infantry.train" in scenarios


def test_resolve_args_substitutes_groups_and_coerces_ints() -> None:
    m = re.search(r"Train\s+(?P<target>\d+)\s+Infantry", "Train 12 Infantry")
    assert m is not None
    assert _resolve_args({"troop": "infantry", "target": "${target}"}, m) == {
        "troop": "infantry",
        "target": 12,
    }
