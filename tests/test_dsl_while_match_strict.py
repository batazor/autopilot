"""Strict + retry semantics for top-level ``while_match`` in player-bound scenarios.

Player-bound DSL scenarios (no ``device_level: true`` marker) get:
- Initial-probe retries (default 5 × 500 ms) to absorb screen-settling lag
  after navigation, so a brief no-match after a tap doesn't kill the scenario.
- Strict zero-iteration failure: if the initial probe never matches even after
  retries, the scenario reschedules itself (success=False, next_run_at=+30 s)
  instead of silently yielding to the next queue item.

Device-level scenarios (popup dismissals like ``tap_claim_button``) keep the
legacy "0 iterations = success" behavior because their triggers may have
already been resolved by the time the task runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
import yaml

import tasks.dsl_scenario as dsl


class _FakeActions:
    def __init__(self, frames: list[np.ndarray]) -> None:
        self.frames = frames
        self.capture_count = 0
        self.tapped: list[tuple[str, int, int, str | None]] = []

    def screen_resolution(self, instance_id: str) -> tuple[int, int]:
        assert instance_id == "bs1"
        return 100, 100

    def capture_screen_bgr(self, instance_id: str) -> np.ndarray:
        assert instance_id == "bs1"
        idx = min(self.capture_count, len(self.frames) - 1)
        self.capture_count += 1
        return self.frames[idx]

    def tap(self, instance_id: str, point: Any, *, approval_region: str | None = None) -> bool:
        self.tapped.append((instance_id, point.x, point.y, approval_region))
        return True


def _claim_pattern() -> np.ndarray:
    patch = np.zeros((10, 10, 3), dtype=np.uint8)
    patch[:] = (20, 160, 240)
    patch[2:8, 2:8] = (20, 220, 40)
    patch[4:6, :] = (255, 255, 255)
    return patch


def _write_player_bound_scenario(tmp_path: Path, frame: np.ndarray) -> None:
    """A scenario without ``device_level: true`` — defaults to strict + retry."""
    (tmp_path / "scenarios" / "workers").mkdir(parents=True)
    (tmp_path / "references" / "crop").mkdir(parents=True)
    (tmp_path / "scenarios" / "workers" / "test_assign.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Test assign",
                "steps": [
                    {
                        "while_match": "page.worker.add",
                        "threshold": 0.9,
                        "max": 6,
                        "steps": [
                            {"click": "page.worker.add"},
                            {"wait": 0},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cv2.imwrite(
        str(tmp_path / "references/crop/page.worker.add_page.worker.add.png"),
        frame[20:30, 20:30],
    )
    (tmp_path / "area.json").write_text(
        yaml.dump(
            {
                "screens": [
                    {
                        "id": 1,
                        "ocr": "references/page.worker.add.png",
                        "regions": [
                            {
                                "name": "page.worker.add",
                                "bbox": {"x": 20, "y": 20, "width": 10, "height": 10},
                                "threshold": 0.9,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_player_bound_while_match_zero_iterations_returns_soft_failure(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """All probes miss → scenario reschedules itself instead of silent success."""
    blank = np.zeros((100, 100, 3), dtype=np.uint8)
    _write_player_bound_scenario(tmp_path, _claim_pattern_frame := _frame_with_pattern())
    actions = _FakeActions([blank, blank, blank, blank, blank, blank, blank])
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)
    # Strip retry interval so the test runs instantly.
    real_sleep = dsl.asyncio.sleep

    async def _instant_sleep(_s: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(dsl.asyncio, "sleep", _instant_sleep)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="test_assign",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is False
    assert result.next_run_at is not None  # rescheduled
    assert (result.metadata or {}).get("reason") == "while_match_no_iterations"
    assert (result.metadata or {}).get("attempts") == 5  # default for player-bound
    assert actions.tapped == []  # no clicks happened


@pytest.mark.asyncio
async def test_player_bound_while_match_initial_retry_eventually_matches(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """First two probes miss, third matches → click happens, scenario succeeds."""
    visible = _frame_with_pattern()
    blank = np.zeros((100, 100, 3), dtype=np.uint8)
    _write_player_bound_scenario(tmp_path, visible)
    # Sequence: blank, blank, visible, blank → retry exhausted on 3rd probe (matches),
    # click, then probe again (blank) → exit, iterations=1, success.
    actions = _FakeActions([blank, blank, visible, blank])
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)
    real_sleep = dsl.asyncio.sleep

    async def _instant_sleep(_s: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(dsl.asyncio, "sleep", _instant_sleep)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="test_assign",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert len(actions.tapped) == 1  # one click after retry succeeded


@pytest.mark.asyncio
async def test_player_bound_while_match_honors_explicit_retry_block(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """`retry: {attempts, interval_seconds}` overrides the player-bound defaults."""
    blank = np.zeros((100, 100, 3), dtype=np.uint8)
    _write_player_bound_scenario(tmp_path, _frame_with_pattern())
    yaml_path = tmp_path / "scenarios" / "workers" / "test_assign.yaml"
    raw = yaml.safe_load(yaml_path.read_text())
    # Use the duration-string form ("250ms") to also exercise the parser.
    raw["steps"][0]["retry"] = {"attempts": 3, "interval": "250ms"}
    yaml_path.write_text(yaml.dump(raw), encoding="utf-8")

    actions = _FakeActions([blank, blank, blank, blank, blank])
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)
    real_sleep = dsl.asyncio.sleep

    async def _instant_sleep(_s: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(dsl.asyncio, "sleep", _instant_sleep)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="test_assign",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is False
    md = result.metadata or {}
    assert md.get("attempts") == 3            # honored override
    assert md.get("interval") == 0.25         # "250ms" parsed to seconds
    # Three probe attempts, none matched → exactly 3 captures.
    assert actions.capture_count == 3


@pytest.mark.asyncio
async def test_device_level_while_match_zero_iterations_returns_success(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """Device-level scenarios keep legacy 0-iteration silent success."""
    blank = np.zeros((100, 100, 3), dtype=np.uint8)
    _write_player_bound_scenario(tmp_path, _frame_with_pattern())
    # Mark the scenario as device_level so legacy semantics apply.
    yaml_path = tmp_path / "scenarios" / "workers" / "test_assign.yaml"
    raw = yaml.safe_load(yaml_path.read_text())
    raw["device_level"] = True
    yaml_path.write_text(yaml.dump(raw), encoding="utf-8")

    actions = _FakeActions([blank, blank])
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="test_assign",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert result.next_run_at is None  # no reschedule
    assert actions.tapped == []
    # Only one probe attempt (default for device_level).
    assert actions.capture_count == 1


def _frame_with_pattern() -> np.ndarray:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[20:30, 20:30] = _claim_pattern()
    return frame
