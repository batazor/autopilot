"""Fast paths in overlay evaluation (empty plan, screen gate)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import numpy as np
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_evaluate_overlay_skips_grayscale_when_plan_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = np.zeros((64, 48, 3), dtype=np.uint8)
    cvt = AsyncMock(side_effect=AssertionError("cvtColor should not run"))
    monkeypatch.setattr("analysis.overlay_engine.cv2.cvtColor", cvt)

    out = await evaluate_overlay_rules_async(
        frame,
        {},
        tmp_path,
        [],
        current_screen="main_city",
    )
    assert out == {}
    cvt.assert_not_called()


@pytest.mark.asyncio
async def test_evaluate_overlay_skips_grayscale_when_screen_gate_blocks_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = np.zeros((64, 48, 3), dtype=np.uint8)
    cvt = AsyncMock(side_effect=AssertionError("cvtColor should not run"))
    monkeypatch.setattr("analysis.overlay_engine.cv2.cvtColor", cvt)

    rules = [
        {
            "name": "only.main_city",
            "region": "btn",
            "action": "findIcon",
            "screens": ["main_city"],
            "threshold": 0.9,
        }
    ]
    out = await evaluate_overlay_rules_async(
        frame,
        {},
        tmp_path,
        rules,
        current_screen="myriad_bazaar",
    )
    assert out == {}
    cvt.assert_not_called()
