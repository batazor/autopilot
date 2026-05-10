"""Rejecting a navigation tap (click approval) aborts navigate_to instead of retrying."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from navigation.detector import ScreenName
from navigation.navigator import Navigator


@pytest.mark.asyncio
async def test_navigate_to_returns_false_immediately_when_navigation_tap_rejected(
    monkeypatch: Any,
    redis_async: object,
) -> None:
    redis = redis_async

    def capture(_instance_id: str) -> np.ndarray:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    tap_calls = {"n": 0}

    def tap(_instance_id: str, point: Any, **kw: Any) -> bool:
        del point, kw
        tap_calls["n"] += 1
        return False

    async def detect_survivor(_image: np.ndarray) -> ScreenName:
        return ScreenName.SURVIVOR_STATUS

    nav = Navigator(capture, tap, redis_client=redis)
    monkeypatch.setattr(nav._detector, "detect_screen", detect_survivor)
    monkeypatch.setattr(
        nav,
        "_load_area_doc",
        lambda: {
            "screens": [
                {
                    "id": 1,
                    "regions": [
                        {
                            "name": "from.survivor_status.to.main_city",
                            "bbox": {"x": 10.0, "y": 10.0, "width": 5.0, "height": 5.0},
                            "action": "exist",
                        },
                    ],
                },
            ],
        },
    )

    ok = await nav.navigate_to(ScreenName.MAIN_CITY, "bs1")
    assert ok is False
    assert tap_calls["n"] == 1
