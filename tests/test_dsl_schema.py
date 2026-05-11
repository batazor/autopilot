"""All committed scenario YAML must pass the DSL schema."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from scenarios.dsl_schema import (
    DslStep,
    dsl_scenario_yaml_priority,
    dump_scenario,
    parse_scenario,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_ROOT = REPO_ROOT / "scenarios"


def _runnable_yaml_files() -> list[Path]:
    return sorted(
        p
        for p in SCENARIOS_ROOT.rglob("*.yaml")
        if "drafts/" not in p.relative_to(SCENARIOS_ROOT).as_posix()
    )


@pytest.mark.parametrize("path", _runnable_yaml_files(), ids=lambda p: p.name)
def test_parse_existing_scenario(path: Path) -> None:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenario = parse_scenario(raw)
    assert scenario.name, f"missing name in {path}"


@pytest.mark.parametrize("path", _runnable_yaml_files(), ids=lambda p: p.name)
def test_round_trip_preserves_structure(path: Path) -> None:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenario = parse_scenario(raw)
    dumped = dump_scenario(scenario)
    reparsed = parse_scenario(dumped)
    assert reparsed.model_dump(by_alias=True, exclude_none=True) == scenario.model_dump(
        by_alias=True, exclude_none=True
    )


def test_dsl_scenario_yaml_priority_reads_mail_scenarios() -> None:
    assert dsl_scenario_yaml_priority(REPO_ROOT, "read_mail_gifts") == 80_000
    assert dsl_scenario_yaml_priority(REPO_ROOT, "nonexistent_scenario_key") is None


def test_step_accepts_bare_group() -> None:
    """No action, no cond, just ``steps:`` — matches runtime grouped-step handler."""
    step = DslStep.model_validate(
        {"steps": [{"click": "main_city_back"}, {"wait": "1s"}]}
    )
    assert step.step_type() == "group"
    assert step.cond is None
    assert step.steps is not None and len(step.steps) == 2


def test_step_accepts_composite_cond_group() -> None:
    """``cond`` + ``steps`` — guarded group, also runtime-supported."""
    step = DslStep.model_validate(
        {"cond": "currentNode == main_city", "steps": [{"wait": "1s"}]}
    )
    assert step.step_type() == "cond"


def test_step_rejects_action_less_step_without_steps() -> None:
    """Empty step (no action, no group) is still invalid."""
    with pytest.raises(ValidationError):
        DslStep.model_validate({})


def test_step_rejects_empty_steps_group() -> None:
    """``steps: []`` with no action is invalid — nothing to run."""
    with pytest.raises(ValidationError):
        DslStep.model_validate({"steps": []})


def test_step_rejects_multiple_action_keys() -> None:
    with pytest.raises(ValidationError):
        DslStep.model_validate({"click": "x", "wait": "1s"})
