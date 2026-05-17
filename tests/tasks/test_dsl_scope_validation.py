"""``ocr: scope:`` typos used to fall back to ``player`` silently — the
runtime warned once and kept going, writing to the wrong Redis hash while
the cleanup walk in :mod:`tasks.dsl_scenario_helpers` targeted yet another
hash. These tests pin the new fail-fast gate.

Two layers of defence covered here:

1. :class:`scenarios.dsl_schema.DslStep` types ``scope`` as a ``Literal``
   so any committed YAML with a bad scope blows up
   ``test_parse_existing_scenario`` at CI time.

2. :func:`scenarios.dsl_schema.validate_dsl_steps` walks the raw step tree
   (the format the executor sees after template rendering — never goes
   through Pydantic) so an in-flight task with a bad scope is rejected at
   ``execute()`` before any tap fires.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scenarios.dsl_schema import OCR_SCOPES, DslStep, validate_dsl_steps


def test_dsl_step_rejects_unknown_scope_at_parse() -> None:
    """Pydantic schema is the editor / CI gate. ``scope: instnace`` (typo)
    must fail validation, not get silently coerced."""
    with pytest.raises(ValidationError):
        DslStep.model_validate({"ocr": "page.heroes.unit.name", "scope": "instnace"})


def test_dsl_step_accepts_documented_scopes() -> None:
    """Both ``player`` (default) and ``instance`` must validate cleanly,
    and ``scope`` can be omitted entirely (defaults to ``None`` → runtime
    interprets as ``player``)."""
    for s in OCR_SCOPES:
        DslStep.model_validate({"ocr": "x", "scope": s})
    DslStep.model_validate({"ocr": "x"})  # no scope at all


def test_validate_dsl_steps_clean_tree_returns_no_errors() -> None:
    """No OCR step or all OCR steps with valid/omitted scope → empty list."""
    steps = [
        {"click": "btn"},
        {"ocr": "region", "scope": "instance"},
        {"ocr": "region2"},  # default scope
    ]
    assert validate_dsl_steps(steps) == []


def test_validate_dsl_steps_rejects_removed_set_node_action() -> None:
    errors = validate_dsl_steps([{"set_node": "main_city"}])

    assert len(errors) == 1
    assert "set_node" in errors[0]
    assert "unsupported DSL action" in errors[0]


def test_validate_dsl_steps_finds_typo_on_top_level_step() -> None:
    """The error string must name the offending step path so the operator
    can ``grep`` the YAML for it without re-reading the full trace."""
    steps = [{"ocr": "region", "scope": "instnace"}]
    errors = validate_dsl_steps(steps)
    assert len(errors) == 1
    assert "steps.0.scope" in errors[0]
    assert "'instnace'" in errors[0]
    assert "'player'" in errors[0] and "'instance'" in errors[0]


def test_validate_dsl_steps_finds_typo_inside_loop() -> None:
    """Nested ``ocr`` steps inside ``loop`` / ``repeat`` / ``while_match``
    must be walked — the executor renders the rendered runtime in-place,
    so a deep typo is just as broken as a top-level one."""
    steps = [
        {"click": "x"},
        {
            "loop": {
                "max": 3,
                "steps": [
                    {"ocr": "deep", "scope": "instnace"},
                ],
            }
        },
    ]
    errors = validate_dsl_steps(steps)
    assert any("steps.1.loop.steps.0.scope" in e for e in errors), errors


def test_validate_dsl_steps_walks_else_branch() -> None:
    """``while_match.else`` is the back-off path for "no iterations" — a
    typo there is the worst kind because it only fires on the unhappy
    path. Walker must enter it."""
    steps = [
        {
            "while_match": "popup",
            "max": 1,
            "steps": [{"click": "popup"}],
            "else": [{"ocr": "fallback", "scope": "wrong"}],
        }
    ]
    errors = validate_dsl_steps(steps)
    assert any("steps.0.else.0.scope" in e for e in errors), errors


@pytest.mark.asyncio
async def test_execute_fails_fast_on_invalid_scope(
    tmp_path, mocker, redis_async
) -> None:
    """End-to-end: a scenario YAML with a typo'd scope makes ``execute()``
    return ``reason="scenario_invalid"`` *before* the worker starts tapping.
    Previously the OCR step would log a warning, write to the wrong scope,
    and keep going."""
    import yaml as _yaml
    from conftest import make_actions, patch_dsl

    from tasks import dsl_scenario as dsl

    module_dir = tmp_path / "modules" / "core" / "test_scenarios"
    scen_dir = module_dir / "scenarios"
    scen_dir.mkdir(parents=True)
    (module_dir / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    bad = {
        "name": "bad-scope",
        "enabled": True,
        "device_level": True,
        "steps": [
            {
                "ocr": "page.heroes.unit.name",
                "store": "page.heroes.unit.name",
                "scope": "instnace",  # the typo
            },
        ],
    }
    (scen_dir / "bad_scope.yaml").write_text(_yaml.safe_dump(bad), encoding="utf-8")
    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t-bad-scope",
        player_id="",
        scenario_key="bad_scope",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is False
    md = result.metadata or {}
    assert md["reason"] == "scenario_invalid"
    errs = md.get("errors") or []
    assert len(errs) == 1  # ty: ignore[invalid-argument-type]
    assert "scope" in errs[0]  # ty: ignore[not-subscriptable]
    assert "instnace" in errs[0]  # ty: ignore[not-subscriptable]
