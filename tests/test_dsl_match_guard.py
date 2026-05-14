from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
import yaml

import tasks.dsl_scenario as dsl


class _FakeActions:
    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame
        self.tapped: list[tuple[str, int, int, str | None]] = []

    def screen_resolution(self, instance_id: str) -> tuple[int, int]:
        assert instance_id == "bs1"
        return 100, 100

    def capture_screen_bgr(self, instance_id: str) -> np.ndarray:
        assert instance_id == "bs1"
        return self.frame

    def tap(self, instance_id: str, point: Any, *, approval_region: str | None = None) -> bool:
        self.tapped.append((instance_id, point.x, point.y, approval_region))
        return True


def _write_skip_text_repo(tmp_path: Path, frame: np.ndarray) -> None:
    (tmp_path / "scenarios" / "onboarding").mkdir(parents=True)
    (tmp_path / "references" / "crop").mkdir(parents=True)
    (tmp_path / "scenarios" / "onboarding" / "skip_text_button.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Skip",
                "steps": [
                    {"match": "skip_text_button", "threshold": 0.95},
                    {"click": "skip_text_button"},
                ],
            }
        ),
        encoding="utf-8",
    )
    crop = frame[80:90, 80:90]
    cv2.imwrite(str(tmp_path / "references/crop/skip_text_skip_text_button.png"), crop)
    (tmp_path / "area.json").write_text(
        yaml.dump(
            {
                "screens": [
                    {
                        "id": 1,
                        "ocr": "references/skip_text.png",
                        "regions": [
                            {
                                "name": "skip_text_button",
                                "threshold": 0.9,
                                "bbox": {"x": 80, "y": 80, "width": 10, "height": 10},
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _skip_pattern() -> np.ndarray:
    patch = np.zeros((10, 10, 3), dtype=np.uint8)
    patch[:] = (0, 220, 255)
    patch[2:8, 2:8] = (0, 0, 255)
    patch[4:6, :] = (255, 255, 255)
    return patch


@pytest.mark.asyncio
async def test_dsl_match_guard_clicks_when_region_still_visible(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[80:90, 80:90] = _skip_pattern()
    _write_skip_text_repo(tmp_path, frame)
    actions = _FakeActions(frame)
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="skip_text_button",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tapped == [("bs1", 85, 85, "skip_text_button")]


@pytest.mark.asyncio
async def test_dsl_match_guard_skips_click_when_region_is_stale(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    reference_frame = np.zeros((100, 100, 3), dtype=np.uint8)
    reference_frame[80:90, 80:90] = _skip_pattern()
    _write_skip_text_repo(tmp_path, reference_frame)
    stale_frame = np.zeros((100, 100, 3), dtype=np.uint8)
    actions = _FakeActions(stale_frame)
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="skip_text_button",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    # Failed guard reports ``success=False`` so queue history surfaces it as
    # a failure (not a silent ok with ``reason=match_guard_failed``).
    assert result.success is False
    assert result.metadata["reason"] == "match_guard_failed"
    assert actions.tapped == []


def _write_match_with_steps_repo(tmp_path: Path, frame: np.ndarray) -> None:
    """Scenario where ``match: + steps:`` is the soft, guarded-block form.

    Two regions: the primary ``skip_text_button`` (probed by ``match:``) and
    a fallback ``backup_button`` reached only via ``else:``.
    """
    (tmp_path / "scenarios" / "onboarding").mkdir(parents=True)
    (tmp_path / "references" / "crop").mkdir(parents=True)
    (tmp_path / "scenarios" / "onboarding" / "match_with_steps.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "device_level": True,
                "name": "Match with steps",
                "steps": [
                    {
                        "match": "skip_text_button",
                        "threshold": 0.95,
                        "steps": [{"click": "skip_text_button"}],
                        "else": [{"click": "backup_button"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    crop = frame[80:90, 80:90]
    cv2.imwrite(
        str(tmp_path / "references/crop/match_with_steps_skip_text_button.png"), crop
    )
    (tmp_path / "area.json").write_text(
        yaml.dump(
            {
                "screens": [
                    {
                        "id": 1,
                        "ocr": "references/match_with_steps.png",
                        "regions": [
                            {
                                "name": "skip_text_button",
                                "threshold": 0.9,
                                "bbox": {"x": 80, "y": 80, "width": 10, "height": 10},
                            },
                            {
                                "name": "backup_button",
                                "bbox": {"x": 10, "y": 10, "width": 10, "height": 10},
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_dsl_match_with_steps_runs_steps_on_match(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    """match + steps + match succeeds → steps run."""
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[80:90, 80:90] = _skip_pattern()
    _write_match_with_steps_repo(tmp_path, frame)
    actions = _FakeActions(frame)
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="match_with_steps",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tapped == [("bs1", 85, 85, "skip_text_button")]


@pytest.mark.asyncio
async def test_dsl_match_with_steps_runs_else_on_miss(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    """match + steps + match fails → else branch runs, scenario doesn't abort."""
    reference = np.zeros((100, 100, 3), dtype=np.uint8)
    reference[80:90, 80:90] = _skip_pattern()
    _write_match_with_steps_repo(tmp_path, reference)
    blank = np.zeros((100, 100, 3), dtype=np.uint8)
    actions = _FakeActions(blank)
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="match_with_steps",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    # Soft form: success=True, no match_guard_failed; else clicked backup.
    assert result.success is True
    assert result.metadata.get("reason") != "match_guard_failed"
    assert actions.tapped == [("bs1", 15, 15, "backup_button")]
