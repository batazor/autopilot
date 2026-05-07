from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
import yaml

import tasks.dsl_scenario as dsl


class _FakeRedis:
    async def hset(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def hget(self, *_args: Any, **_kwargs: Any) -> None:
        return None


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


def _write_claim_repo(tmp_path: Path, frame: np.ndarray) -> None:
    (tmp_path / "scenarios" / "overlay").mkdir(parents=True)
    (tmp_path / "references" / "crop").mkdir(parents=True)
    (tmp_path / "scenarios" / "overlay" / "tap_claim_button.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Claim",
                "steps": [
                    {
                        "while_match": "claim_button",
                        "threshold": 0.98,
                        "max": 5,
                        "steps": [
                            {"click": "claim_button"},
                            {"wait": 0},
                        ],
                    },
                    {"click": "claim_button_close"},
                ],
            }
        ),
        encoding="utf-8",
    )
    cv2.imwrite(str(tmp_path / "references/crop/claim_claim_button.png"), frame[20:30, 20:30])
    (tmp_path / "area.json").write_text(
        yaml.dump(
            {
                "screens": [
                    {
                        "id": 1,
                        "ocr": "references/claim.png",
                        "regions": [
                            {
                                "name": "claim_button",
                                "bbox": {"x": 20, "y": 20, "width": 10, "height": 10},
                            },
                            {
                                "name": "claim_button_close",
                                "bbox": {"x": 80, "y": 10, "width": 10, "height": 10},
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_dsl_while_match_clicks_until_region_disappears_then_closes(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    visible = np.zeros((100, 100, 3), dtype=np.uint8)
    visible[20:30, 20:30] = _claim_pattern()
    gone = np.zeros((100, 100, 3), dtype=np.uint8)
    _write_claim_repo(tmp_path, visible)
    actions = _FakeActions([visible, visible, gone])
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="tap_claim_button",
        redis_client=_FakeRedis(),
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tapped == [
        ("bs1", 25, 25, "claim_button"),
        ("bs1", 25, 25, "claim_button"),
        ("bs1", 85, 15, "claim_button_close"),
    ]


def test_tap_claim_button_while_match_has_nested_steps() -> None:
    repo = Path(__file__).resolve().parents[1]
    doc = yaml.safe_load((repo / "scenarios/overlay/tap_claim_button.yaml").read_text())
    loop = doc["steps"][0]

    assert loop["while_match"] == "claim_button"
    assert loop["steps"] == [{"click": "claim_button"}, {"wait": "3s"}]
    assert doc["steps"][1:] == [{"click": "claim_button_close"}, {"wait": "2s"}]
