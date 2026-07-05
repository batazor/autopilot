from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import yaml
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl
from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for

if TYPE_CHECKING:
    from pathlib import Path


def _write_scenario(tmp_path: Path, steps: list[dict[str, Any]]) -> None:
    mod = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "test_scenarios"
    scenario_root = mod / "scenarios" / "test"
    scenario_root.mkdir(parents=True)
    (mod / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    (scenario_root / "wait_screen_demo.yaml").write_text(
        yaml.dump({"enabled": True, "steps": steps}),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(
        yaml.dump({"screens": [{"id": 1, "ocr": "references/x.png", "regions": []}]}),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_wait_screen_detects_target_and_persists_current_screen(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    _write_scenario(
        tmp_path,
        [
            {
                "wait_screen": ["exploration.victory", "exploration.defeat"],
                "max": 2,
                "interval": "0ms",
            }
        ],
    )
    actions = make_actions()
    patch_dsl(mocker, actions, repo_root=tmp_path)

    detections = iter(["squad_settings", "exploration.victory"])

    async def detect_screen(_self: object, _image: object, **_kwargs: object) -> str:
        return next(detections)

    mocker.patch("navigation.detector.ScreenDetector.detect_screen", new=detect_screen)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="wait_screen_demo",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    res = await task.execute("bs1")

    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]
    assert res.success is True
    assert cur == "exploration.victory"
    assert actions.capture_screen_bgr.call_count == 2


@pytest.mark.asyncio
async def test_wait_screen_optional_timeout_completes_scenario(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """``optional: true`` tolerates the timeout: step ok, scenario completes.

    The intel rescue path relies on this — rescue targets dispatch instantly
    with no deploy screen, so the post-click wait for heroes.deploy may
    legitimately never match.
    """
    _write_scenario(
        tmp_path,
        [
            {
                "wait_screen": {
                    "any": ["heroes.deploy"],
                    "max": 2,
                    "interval": "0ms",
                    "optional": True,
                }
            },
            {"wait": "0ms"},
        ],
    )
    actions = make_actions()
    patch_dsl(mocker, actions, repo_root=tmp_path)

    async def detect_screen(_self: object, _image: object, **_kwargs: object) -> str:
        return "main_world"

    mocker.patch("navigation.detector.ScreenDetector.detect_screen", new=detect_screen)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="wait_screen_demo",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    res = await task.execute("bs1")

    assert res.success is True
    assert (res.metadata or {}).get("scenario_completed") is True
    # The optional timeout must not clobber current_screen with a false match.
    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]
    assert cur != "heroes.deploy"


@pytest.mark.asyncio
async def test_wait_screen_optional_nested_timeout_completes_scenario(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """The nested (inside a cond group) wait_screen honours optional too —
    mirrors intel_run's modal flow where the deploy wait sits under
    ``cond: currentNode == intel.fight``."""
    _write_scenario(
        tmp_path,
        [
            {
                "cond": "currentNode != main_city",
                "steps": [
                    {
                        "wait_screen": {
                            "any": ["heroes.deploy"],
                            "max": 2,
                            "interval": "0ms",
                            "optional": True,
                        }
                    },
                ],
            },
        ],
    )
    actions = make_actions()
    patch_dsl(mocker, actions, repo_root=tmp_path)

    async def detect_screen(_self: object, _image: object, **_kwargs: object) -> str:
        return "main_world"

    mocker.patch("navigation.detector.ScreenDetector.detect_screen", new=detect_screen)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="wait_screen_demo",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    res = await task.execute("bs1")

    assert res.success is True
    assert (res.metadata or {}).get("scenario_completed") is True


@pytest.mark.asyncio
async def test_wait_screen_times_out(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    _write_scenario(
        tmp_path,
        [{"wait_screen": {"any": ["exploration.victory"], "max": 1, "interval": "0ms"}}],
    )
    actions = make_actions()
    patch_dsl(mocker, actions, repo_root=tmp_path)

    async def detect_screen(_self: object, _image: object, **_kwargs: object) -> str:
        return "squad_settings"

    mocker.patch("navigation.detector.ScreenDetector.detect_screen", new=detect_screen)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="wait_screen_demo",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    res = await task.execute("bs1")

    assert res.success is False
    assert (res.metadata or {}).get("reason") == "wait_screen_timeout"
