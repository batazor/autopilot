"""Identity bootstrap scenarios live under modules/core/who_i_am."""

from __future__ import annotations

from pathlib import Path

import yaml

from dsl.registry import iter_scenario_yaml_files, scenario_roots
from dsl.template_resolver import resolve

REPO = Path(__file__).resolve().parents[2]


def test_identity_scenarios_resolve_from_modules() -> None:
    who = resolve(REPO, "who_i_am")
    assert who is not None
    assert "games/wos/core/who_i_am/scenarios" in str(who.path)


def test_identity_scenarios_not_under_core_onboarding() -> None:
    core_paths = {
        p.as_posix()
        for root, p in iter_scenario_yaml_files(REPO)
        if root.module_id is None
    }
    assert not any(p.endswith("onboarding/who_i_am.yaml") for p in core_paths)


def test_scenario_roots_include_identity_modules() -> None:
    labels = {r.label for r in scenario_roots(REPO)}
    assert "games/wos/core/who_i_am/scenarios" in labels


def test_who_i_am_resolves_player_id_before_player_state_writes() -> None:
    who = resolve(REPO, "who_i_am")
    assert who is not None
    data = yaml.safe_load(who.path.read_text(encoding="utf-8"))
    steps = data["steps"]
    ocr_steps = [s for s in steps if isinstance(s, dict) and "ocr" in s]

    assert ocr_steps[0]["ocr"] == "player.id"
    assert ocr_steps[0]["store"] == "player_id"
    assert [s["ocr"] for s in ocr_steps[:2]] == [
        "player.id",
        "player.state",
    ]
    assert all(s["preprocess"] == "fast_line" for s in ocr_steps[:2])
    assert ocr_steps[0]["threshold"] == 0.75
    assert ocr_steps[0]["min_digits"] == 8
    assert ocr_steps[1]["threshold"] == 0.45
    assert "player.power" not in {s["ocr"] for s in ocr_steps}

    push_steps = [s for s in steps if isinstance(s, dict) and "push_scenario" in s]
    assert push_steps[0]["push_scenario"]["name"] == "check_main_city"
    assert push_steps[0]["cond"] == "active_player != null"
