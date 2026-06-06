"""All committed scenario YAML must pass the DSL schema."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from dsl.dsl_schema import (
    DslStep,
    dsl_scenario_yaml_priority,
    dump_scenario,
    parse_scenario,
)
from dsl.registry import iter_scenario_yaml_files

REPO_ROOT = Path(__file__).resolve().parents[2]


def _runnable_yaml_files() -> list[Path]:
    """All runnable scenario YAMLs from active module scenario roots."""
    return [path for _root, path in iter_scenario_yaml_files(REPO_ROOT)]


def _yaml_id(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


@pytest.mark.parametrize("path", _runnable_yaml_files(), ids=_yaml_id)
def test_parse_existing_scenario(path: Path) -> None:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenario = parse_scenario(raw)
    assert scenario.name, f"missing name in {path}"


@pytest.mark.parametrize("path", _runnable_yaml_files(), ids=_yaml_id)
def test_round_trip_preserves_structure(path: Path) -> None:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenario = parse_scenario(raw)
    dumped = dump_scenario(scenario)
    reparsed = parse_scenario(dumped)
    assert reparsed.model_dump(by_alias=True, exclude_none=True) == scenario.model_dump(
        by_alias=True, exclude_none=True
    )


def test_dsl_scenario_yaml_priority_reads_mail_scenarios() -> None:
    assert dsl_scenario_yaml_priority(REPO_ROOT, "mail.claim.system") == 80_000
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


def test_step_rejects_removed_set_node_action() -> None:
    with pytest.raises(ValidationError):
        DslStep.model_validate({"set_node": "main_city"})
    with pytest.raises(ValidationError):
        DslStep.model_validate({"set_node": "main_city", "steps": [{"click": "x"}]})


def test_step_rejects_multiple_action_keys() -> None:
    with pytest.raises(ValidationError):
        DslStep.model_validate({"click": "x", "wait": "1s"})


def test_long_click_accepts_wait_as_duration() -> None:
    """Runtime treats ``wait`` on a ``long_click`` step as long-press duration
    (see ``tasks/dsl_scenario_inline_mixin.py:483``). Schema must agree at
    every nesting level so building.upgrade.yaml validates."""
    DslStep.model_validate({"long_click": "upgrade_button", "wait": "5s"})


def test_exec_accepts_ttl_and_wait_modifiers() -> None:
    """An ``exec:`` step hands siblings to its handler as args and the runtime
    also applies step-level ``wait`` / ``ttl`` around it (see the independent
    ``if "exec"`` / ``if "ttl"`` branches in
    ``tasks/dsl_scenario_execute_mixin.py``). Schema must agree so the dreamscape
    solver scenarios validate."""
    step = DslStep.model_validate(
        {
            "exec": "dreamscape_memory_solve_loop",
            "mode": "solo",
            "ttl": "5m",
            "wait": "300ms",
            "max_iterations": 3000,
        }
    )
    assert step.step_type() == "exec"


def test_exec_still_rejects_real_action_conflicts() -> None:
    """Only ``wait`` / ``ttl`` are modifiers on an ``exec`` step — a true second
    action key (``click``, ``match``, …) still fails validation."""
    with pytest.raises(ValidationError):
        DslStep.model_validate({"exec": "x", "click": "y"})


def test_system_back_is_action_key() -> None:
    step = DslStep.model_validate({"system_back": True})
    assert step.step_type() == "system_back"


def test_long_click_still_rejects_real_action_conflicts() -> None:
    """``wait`` is the only modifier — adding a true second action key
    (``click``, ``match``, etc.) still fails validation."""
    with pytest.raises(ValidationError):
        DslStep.model_validate({"long_click": "x", "click": "y"})


def test_loop_validates_nested_steps() -> None:
    """``loop.steps`` previously slipped past the validator because ``loop``
    was an opaque ``dict[str, Any]``. Now it's a typed LoopSpec so the inner
    long_click+wait step must pass the same checks as a top-level step."""
    # Valid: long_click + wait inside a loop.
    DslStep.model_validate(
        {
            "loop": {
                "max": 3,
                "steps": [
                    {"long_click": "upgrade_button", "wait": "5s"},
                    {"wait": "1s"},
                ],
            }
        }
    )
    # Invalid: a real "multiple action keys" combo nested in a loop should now
    # be caught (was silently ignored before the LoopSpec refactor).
    with pytest.raises(ValidationError):
        DslStep.model_validate(
            {
                "loop": {
                    "max": 3,
                    "steps": [{"click": "x", "match": "y"}],
                }
            }
        )


def test_repeat_validates_nested_steps() -> None:
    """Mirror of ``test_loop_validates_nested_steps`` for ``repeat:``."""
    DslStep.model_validate(
        {
            "repeat": {
                "max": 2,
                "until_match": "done",
                "steps": [{"click": "x"}, {"wait": "1s"}],
            }
        }
    )
    with pytest.raises(ValidationError):
        DslStep.model_validate(
            {
                "repeat": {
                    "max": 2,
                    "steps": [{"click": "x", "wait": "1s"}],
                }
            }
        )
