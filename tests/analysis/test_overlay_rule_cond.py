"""Overlay YAML ``cond`` gates expensive findIcon before ``active_player`` is set."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import cv2
import numpy as np
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_overlay_cond_skips_findicon_when_active_player_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path
    ref = repo / "references" / "ads.png"
    ref.parent.mkdir(parents=True)
    crop = repo / "references" / "crop" / "ads_icon.png"
    crop.parent.mkdir(parents=True)
    icon = np.zeros((20, 20, 3), dtype=np.uint8)
    icon[:, :] = (40, 40, 40)
    cv2.imwrite(str(ref), np.zeros((128, 72, 3), dtype=np.uint8))
    cv2.imwrite(str(crop), icon)

    area_doc = {
        "screens": [
            {
                "ocr": "references/ads.png",
                "regions": [
                    {
                        "name": "ads_icon",
                        "action": "exist",
                        "threshold": 0.9,
                        "bbox": {
                            "x": 10,
                            "y": 10,
                            "width": 20,
                            "height": 20,
                            "original_width": 72,
                            "original_height": 128,
                        },
                    }
                ],
            }
        ]
    }
    rules = [
        {
            "name": "ads.visible",
            "region": "ads_icon",
            "action": "findIcon",
            "cond": 'active_player == ""',
            "threshold": 0.5,
        }
    ]
    frame = cv2.imread(str(ref))
    assert frame is not None

    mock_redis = AsyncMock()
    mock_redis.hget = AsyncMock(return_value=b"12345")

    async def _cond_false(*_a, **_k):
        return False

    monkeypatch.setattr(
        "tasks.dsl_scenario_helpers._dsl_cond_allows_step",
        _cond_false,
    )

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        repo,
        rules,
        instance_id="emu-1",
        redis_async=mock_redis,
    )
    assert out == {}


@pytest.mark.asyncio
async def test_overlay_cond_runs_when_active_player_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path
    ref = repo / "references" / "ads.png"
    ref.parent.mkdir(parents=True)
    crop = repo / "references" / "crop" / "ads_icon.png"
    crop.parent.mkdir(parents=True)
    icon = np.zeros((20, 20, 3), dtype=np.uint8)
    icon[:, :] = (40, 40, 40)
    cv2.imwrite(str(ref), np.zeros((128, 72, 3), dtype=np.uint8))
    cv2.imwrite(str(crop), icon)

    area_doc = {
        "screens": [
            {
                "ocr": "references/ads.png",
                "regions": [
                    {
                        "name": "ads_icon",
                        "action": "exist",
                        "threshold": 0.9,
                        "bbox": {
                            "x": 10,
                            "y": 10,
                            "width": 20,
                            "height": 20,
                            "original_width": 72,
                            "original_height": 128,
                        },
                    }
                ],
            }
        ]
    }
    rules = [
        {
            "name": "ads.visible",
            "region": "ads_icon",
            "action": "findIcon",
            "cond": 'active_player == ""',
            "threshold": 0.5,
        }
    ]
    frame = cv2.imread(str(ref))
    assert frame is not None

    async def _cond_true(*_a, **_k):
        return True

    monkeypatch.setattr(
        "tasks.dsl_scenario_helpers._dsl_cond_allows_step",
        _cond_true,
    )

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        repo,
        rules,
        instance_id="emu-1",
        redis_async=AsyncMock(),
    )
    assert "ads.visible" in out
