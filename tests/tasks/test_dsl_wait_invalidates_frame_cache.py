"""``wait:`` step drops the per-instance framebuffer cache.

Without this, an OCR / ``match`` step that runs right after a deliberate pause
would still see the pre-wait frame (the cache only gets dropped by tap-style
actions today). For most steps that's fine — siblings are reading the same
screen — but a ``wait:`` is the author saying "the screen will change during
this pause", so the next probe must re-capture.

Companion to the ``max_age_ms`` gate on ``capture_screen_bgr_cached``:
``max_age_ms`` covers passive staleness (no explicit pause), ``wait:`` covers
the explicit case.
"""

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
    scenario_root = mod / "scenarios"
    (scenario_root / "test").mkdir(parents=True)
    (mod / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    (scenario_root / "test" / "wait_demo.yaml").write_text(
        yaml.dump({"enabled": True, "steps": steps}),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(
        yaml.dump(
            {
                "screens": [
                    {
                        "id": 1,
                        "ocr": "references/x.png",
                        "regions": [
                            {
                                "name": "dummy",
                                "bbox": {"x": 10, "y": 10, "width": 10, "height": 10},
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_wait_step_invalidates_frame_cache(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    _write_scenario(tmp_path, [{"wait": "10ms"}])
    invalidations: list[str] = []
    actions = make_actions(resolution=(1000, 1000))
    actions.invalidate_frame_cache.side_effect = lambda instance_id=None: invalidations.append(
        instance_id or "*"
    )
    actions.tap.side_effect = AssertionError("tap() must not run in this test")
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="wait_demo",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    res = await task.execute("bs1")
    assert res.success is True
    assert invalidations == ["bs1"], (
        f"expected one frame-cache invalidation for instance 'bs1', got {invalidations}"
    )


@pytest.mark.asyncio
async def test_zero_duration_wait_does_not_invalidate(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """A ``wait: 0`` (parsed to ``<= 0`` seconds) is a no-op annotation, not a
    real pause — the cache should survive so the next ``match`` keeps reusing
    the warmed frame."""
    _write_scenario(tmp_path, [{"wait": "0ms"}])
    invalidations: list[str] = []
    actions = make_actions(resolution=(1000, 1000))
    actions.invalidate_frame_cache.side_effect = lambda instance_id=None: invalidations.append(
        instance_id or "*"
    )
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="wait_demo",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    res = await task.execute("bs1")
    assert res.success is True
    assert invalidations == []
