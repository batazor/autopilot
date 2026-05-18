"""Operator reject during navigation must not blank ``current_screen``.

Regression: pressing Reject on a navigation approval used to land in the
same ``nav_ok == False`` branch as a real route/verify failure, which then
overwrote ``wos:instance:<id>:state.current_screen`` to ``""``. The identity
of the screen was still valid (no tap fired), so the empty value just forced
``screen_verify`` to redetect on the next tick. The fix records
``last_approval_reject_at`` in the approval gate and skips the
``current_screen = ""`` write when that stamp lands inside the nav attempt.
"""
from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import pytest
import yaml  # used to author the scenario YAML; area.json must be real JSON
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl

if TYPE_CHECKING:
    from pathlib import Path


def _scenario_root(tmp_path: Path) -> Path:
    mod = tmp_path / "modules" / "core" / "test_scenarios"
    scenario_root = mod / "scenarios"
    scenario_root.mkdir(parents=True, exist_ok=True)
    (mod / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    return scenario_root


def _write_nav_fail_scenario(tmp_path: Path) -> None:
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "chapters").mkdir(parents=True)
    (scenario_root / "chapters" / "nav_fail.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Nav fail",
                "node": "event.trials",
                "steps": [{"wait": "0s"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(json.dumps({"screens": []}), encoding="utf-8")


@pytest.mark.asyncio
async def test_operator_reject_during_nav_preserves_current_screen(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    _write_nav_fail_scenario(tmp_path)
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state", "current_screen", "popup.claim"
    )

    async def _navfail_with_reject(self: Any, instance_id: str, *_args: Any, **_kwargs: Any) -> bool:
        # Simulate ``_require_approval`` stamping the reject side-channel
        # inside the nav attempt, then ``Navigator.navigate_to`` returning False.
        await self.redis_client.hset(
            f"wos:instance:{instance_id}:state",
            "last_approval_reject_at",
            str(time.time()),
        )
        return False

    patch_dsl(mocker, make_actions(), repo_root=tmp_path)
    mocker.patch.object(dsl.DslScenarioTask, "_navigate_to_node", new=_navfail_with_reject)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="nav_fail",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is False
    assert result.metadata["reason"] == "navigation_failed"

    state = await redis_async.hgetall("wos:instance:bs1:state")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert state["current_screen"] == "popup.claim", (
        "operator reject must not blank current_screen — no tap fired, identity is still valid"
    )
    assert "operator rejected approval" in state["nav_error"]
    # One-shot signal: cleared after the executor consumes it so a later
    # genuine nav failure doesn't silently inherit the reject semantics.
    assert state.get("last_approval_reject_at", "") == ""


@pytest.mark.asyncio
async def test_real_nav_failure_still_blanks_current_screen(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Counter-test: when reject side-channel is absent, the existing
    nav-failed behavior (blank ``current_screen``) must still apply — the
    detector/verify path relies on that to re-acquire identity."""
    _write_nav_fail_scenario(tmp_path)
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state", "current_screen", "popup.claim"
    )

    async def _navfail(*_args: Any, **_kwargs: Any) -> bool:
        return False

    patch_dsl(mocker, make_actions(), repo_root=tmp_path)
    mocker.patch.object(dsl.DslScenarioTask, "_navigate_to_node", new=_navfail)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="nav_fail",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is False
    state = await redis_async.hgetall("wos:instance:bs1:state")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert state["current_screen"] == ""
    assert "navigation_failed" in state["nav_error"]


@pytest.mark.asyncio
async def test_stale_reject_stamp_does_not_preserve_current_screen(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """A reject stamp from a previous scenario must not bleed into the next
    nav failure — only stamps inside this attempt's window count."""
    _write_nav_fail_scenario(tmp_path)
    stale_ts = time.time() - 3600  # one hour ago
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state",
        mapping={
            "current_screen": "popup.claim",
            "last_approval_reject_at": str(stale_ts),
        },
    )

    async def _navfail(*_args: Any, **_kwargs: Any) -> bool:
        return False

    patch_dsl(mocker, make_actions(), repo_root=tmp_path)
    mocker.patch.object(dsl.DslScenarioTask, "_navigate_to_node", new=_navfail)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="nav_fail",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is False
    state = await redis_async.hgetall("wos:instance:bs1:state")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert state["current_screen"] == "", (
        "stale reject stamp must not suppress the real-nav-fail blank"
    )
    assert "navigation_failed" in state["nav_error"]
