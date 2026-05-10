"""``loop`` DSL primitive: cond / until_cond / ttl with full step support inside body."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

import tasks.dsl_scenario as dsl


class _FakeActions:
    def __init__(self, frame: np.ndarray | None = None) -> None:
        self.frame = frame if frame is not None else np.zeros((100, 100, 3), dtype=np.uint8)
        self.tapped: list[tuple[str, int, int, str | None]] = []

    def screen_resolution(self, instance_id: str) -> tuple[int, int]:
        return 100, 100

    def capture_screen_bgr(self, instance_id: str) -> np.ndarray:
        return self.frame

    def tap(self, instance_id: str, point: Any, *, approval_region: str | None = None) -> bool:
        self.tapped.append((instance_id, point.x, point.y, approval_region))
        return True


def _write_scenario(tmp_path: Path, body: dict[str, Any]) -> None:
    (tmp_path / "scenarios" / "main_city").mkdir(parents=True)
    (tmp_path / "scenarios" / "main_city" / "loop_test.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Loop test",
                "device_level": True,
                "steps": [body],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(yaml.dump({"screens": []}), encoding="utf-8")


@pytest.mark.asyncio
async def test_loop_until_cond_breaks_when_state_field_matches(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """``until_cond`` re-evaluates each iteration; once the instance hash matches, loop exits."""
    _write_scenario(
        tmp_path,
        {
            "loop": {
                "until_cond": 'squad_status ~= "victory|defeat"',
                "max": 5,
                "steps": [{"wait": 0}],
            }
        },
    )
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: _FakeActions())

    # Pre-seed the field so until_cond is True on the first probe and the loop exits.
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"squad_status": "Victory!"},
    )

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="loop_test",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")
    assert result.success is True


@pytest.mark.asyncio
async def test_loop_cond_continues_until_inner_steps_change_state(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """`cond` keeps the loop alive; an inner ``exec`` step flips state and exits."""
    _write_scenario(
        tmp_path,
        {
            "loop": {
                "cond": 'progress != "done"',
                "max": 10,
                "steps": [
                    {"exec": "advance_progress"},
                    {"wait": 0},
                ],
            }
        },
    )
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: _FakeActions())

    iterations = {"n": 0}

    async def _advance(ctx: Any) -> None:
        iterations["n"] += 1
        if iterations["n"] >= 3:
            await ctx.redis_client.hset(
                f"wos:instance:{ctx.instance_id}:state",
                mapping={"progress": "done"},
            )
        else:
            await ctx.redis_client.hset(
                f"wos:instance:{ctx.instance_id}:state",
                mapping={"progress": f"step{iterations['n']}"},
            )

    import tasks.dsl_exec as dsl_exec

    monkeypatch.setitem(dsl_exec.DSL_EXEC_REGISTRY, "advance_progress", _advance)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="loop_test",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    # 3 iterations (step1, step2, exec writes "done"; cond False → exit before iter 4).
    assert iterations["n"] == 3
    final = await redis_async.hget("wos:instance:bs1:state", "progress")  # type: ignore[attr-defined]
    assert final == "done"


@pytest.mark.asyncio
async def test_loop_max_caps_iteration_count(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """Without a satisfying cond, ``max`` caps the loop and the scenario still finishes."""
    _write_scenario(
        tmp_path,
        {
            "loop": {
                "until_cond": 'never_set ~= "ok"',  # never matches → loop runs to max
                "max": 4,
                "steps": [{"exec": "tick"}],
            }
        },
    )
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: _FakeActions())

    ticks = {"n": 0}

    async def _tick(ctx: Any) -> None:
        ticks["n"] += 1

    import tasks.dsl_exec as dsl_exec

    monkeypatch.setitem(dsl_exec.DSL_EXEC_REGISTRY, "tick", _tick)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="loop_test",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")
    assert result.success is True
    assert ticks["n"] == 4


@pytest.mark.asyncio
async def test_loop_break_repeat_exits_loop(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """``break: repeat`` doubles as "exit loop"."""
    _write_scenario(
        tmp_path,
        {
            "loop": {
                "max": 100,
                "steps": [
                    {"exec": "tick"},
                    {"break": "repeat"},
                ],
            }
        },
    )
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: _FakeActions())

    ticks = {"n": 0}

    async def _tick(ctx: Any) -> None:
        ticks["n"] += 1

    import tasks.dsl_exec as dsl_exec

    monkeypatch.setitem(dsl_exec.DSL_EXEC_REGISTRY, "tick", _tick)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="loop_test",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")
    assert result.success is True
    assert ticks["n"] == 1


@pytest.mark.asyncio
async def test_loop_inner_step_cond_skips_individual_step(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """Step-level ``cond:`` is evaluated each iteration so inner steps can be conditionally skipped."""
    _write_scenario(
        tmp_path,
        {
            "loop": {
                "max": 3,
                "steps": [
                    {"exec": "always"},
                    {"cond": 'flag == "yes"', "exec": "only_when_yes"},
                ],
            }
        },
    )
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: _FakeActions())

    counts = {"always": 0, "only_when_yes": 0}

    async def _always(ctx: Any) -> None:
        counts["always"] += 1
        if counts["always"] == 2:
            await ctx.redis_client.hset(
                f"wos:instance:{ctx.instance_id}:state",
                mapping={"flag": "yes"},
            )

    async def _only_when_yes(ctx: Any) -> None:
        counts["only_when_yes"] += 1

    import tasks.dsl_exec as dsl_exec

    monkeypatch.setitem(dsl_exec.DSL_EXEC_REGISTRY, "always", _always)
    monkeypatch.setitem(dsl_exec.DSL_EXEC_REGISTRY, "only_when_yes", _only_when_yes)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="loop_test",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")
    assert result.success is True
    # always runs 3 times (max=3); only_when_yes runs only after flag set on iter 2.
    assert counts["always"] == 3
    assert counts["only_when_yes"] == 2  # iter 2 (after flag flip) + iter 3
