"""Identity bootstrap scenarios live under modules/core/who_i_am."""

from __future__ import annotations

from pathlib import Path

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
