"""Regression: ``ocr: store: <field>`` is scenario-step scoped.

The runner wipes every store target enumerated by ``_collect_ocr_store_targets``
at the start of a *fresh* scenario run (``start_step_index <= 0``). Resumed
tasks must keep whatever earlier steps wrote so cooperative preemption +
resume works.

Locked-in by this regression because the squad_fight scenario hit the exact
opposite — its 12-hour cron tick was running with a 21-hour-old ``squad_status``
from the previous fight, and the ``loop`` exited on that stale value before
its inner OCR even ran.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

import tasks.dsl_scenario as dsl
from tasks.dsl_scenario_helpers import (
    _collect_ocr_store_targets,
    _ocr_store_redis_fields,
)


class _FakeActions:
    def screen_resolution(self, instance_id: str) -> tuple[int, int]:
        return 720, 1280

    def capture_screen_bgr(self, instance_id: str) -> np.ndarray:
        return np.zeros((1280, 720, 3), dtype=np.uint8)

    def tap(self, *_args: Any, **_kwargs: Any) -> bool:
        return True


def _write_minimal_scenario(tmp_path: Path, doc: dict[str, Any]) -> None:
    (tmp_path / "scenarios").mkdir(exist_ok=True)
    (tmp_path / "scenarios" / "scn.yaml").write_text(
        yaml.dump({"enabled": True, "name": "scn", **doc}),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(yaml.dump({"screens": []}), encoding="utf-8")


# ---------------------------------------------------------------------------
# Pure helper coverage — no async, no Redis
# ---------------------------------------------------------------------------


def test_collect_targets_explicit_store_field() -> None:
    steps = [{"ocr": "page.squad_settings.status", "store": "squad_status"}]
    assert _collect_ocr_store_targets(steps) == [("player", "squad_status")]


def test_collect_targets_default_store_is_region_name() -> None:
    """No ``store:`` and no ``state:`` → legacy default uses region name."""
    steps = [{"ocr": "exploration.level"}]
    assert _collect_ocr_store_targets(steps) == [("player", "exploration.level")]


def test_collect_targets_state_only_step_is_not_a_store_target() -> None:
    """``ocr: ... state: ...`` writes to ``db/state.yaml`` (long-lived), not to
    Redis store — must NOT be wiped at scenario start."""
    steps = [{"ocr": "exploration.level", "state": "exploration.level"}]
    assert _collect_ocr_store_targets(steps) == []


def test_collect_targets_respects_explicit_instance_scope() -> None:
    steps = [{"ocr": "x", "store": "y", "scope": "instance"}]
    assert _collect_ocr_store_targets(steps) == [("instance", "y")]


def test_collect_targets_recurses_into_nested_steps() -> None:
    """Store writes inside ``loop:`` / ``repeat:`` / bare ``steps:`` groups
    all get pre-cleared. Mirrors the squad_fight YAML shape."""
    steps = [
        {
            "loop": {
                "cond": '...',
                "steps": [
                    {"ocr": "banner", "store": "squad_status"},
                    {"wait": "1s"},
                ],
            }
        },
        {
            "steps": [
                {"ocr": "other", "store": "deep_field", "scope": "instance"},
            ]
        },
    ]
    targets = _collect_ocr_store_targets(steps)
    assert ("player", "squad_status") in targets
    assert ("instance", "deep_field") in targets


def test_ocr_store_redis_fields_expands_to_four_siblings() -> None:
    assert _ocr_store_redis_fields("squad_status") == [
        "squad_status",
        "squad_status_text",
        "squad_status_confidence",
        "squad_status_at",
    ]


# ---------------------------------------------------------------------------
# End-to-end: stale store value must NOT leak into a fresh scenario run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_start_wipes_stale_store_field(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """Squad-fight-style regression: a previous run left ``squad_status``
    populated in Redis. The new scenario, which OCRs into the same field,
    must see EMPTY state at the top of its loop — otherwise the loop's
    ``cond`` would short-circuit before any OCR happens."""
    # Mirror the squad_fight shape: a loop with an exit ``cond`` and an
    # inner OCR step that declares ``store: squad_status``. The auto-clear
    # at scenario start is driven by ``store:`` writes the YAML *declares*,
    # so the OCR step must be present in the tree even if it never executes
    # at runtime (here it doesn't, because of ``max: 1`` + already-true cond
    # — but cleanup runs *before* the loop).
    _write_minimal_scenario(
        tmp_path,
        {
            "steps": [
                {
                    "loop": {
                        "cond": 'squad_status ~= "victory|defeat"',
                        "max": 1,
                        "steps": [
                            {"ocr": "page.squad_settings.status", "store": "squad_status"},
                            {"wait": 0},
                        ],
                    }
                },
            ],
        },
    )
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: _FakeActions())

    # Pre-seed the *exact* shape the OCR step would have written, including
    # all four sibling fields. All must be gone after scenario start.
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:player:p1:state",
        mapping={
            "squad_status": "Defeat!",
            "squad_status_text": "Defeat!",
            "squad_status_confidence": "0.9995",
            "squad_status_at": "1.0",
            "unrelated": "must-stay",
        },
    )

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="scn",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    await task.execute("bs1")

    # Stale ``squad_status`` and its 3 siblings were wiped at scenario start.
    assert await redis_async.hget("wos:player:p1:state", "squad_status") is None  # type: ignore[attr-defined]
    assert await redis_async.hget("wos:player:p1:state", "squad_status_text") is None  # type: ignore[attr-defined]
    assert await redis_async.hget("wos:player:p1:state", "squad_status_confidence") is None  # type: ignore[attr-defined]
    assert await redis_async.hget("wos:player:p1:state", "squad_status_at") is None  # type: ignore[attr-defined]
    # Unrelated fields on the same hash survive.
    assert await redis_async.hget("wos:player:p1:state", "unrelated") == "must-stay"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_resumed_scenario_does_not_wipe_store(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """When a scenario is resumed after preemption (``start_step_index > 0``),
    earlier steps may have already written to ``store:`` fields — wiping them
    would lose progress. Cleanup is gated on a *fresh* start."""
    _write_minimal_scenario(
        tmp_path,
        {
            "steps": [
                {"ocr": "x", "store": "progress"},
                {"wait": 0},
            ],
        },
    )
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: _FakeActions())

    await redis_async.hset("wos:player:p1:state", "progress", "step3")  # type: ignore[attr-defined]

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="scn",
        start_step_index=1,  # resumed run
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    await task.execute("bs1")

    assert await redis_async.hget("wos:player:p1:state", "progress") == "step3"  # type: ignore[attr-defined]
