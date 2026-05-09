from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pytest

from navigation.detector import ScreenName
from navigation.navigator import Navigator


@pytest.mark.asyncio
async def test_missing_navigation_path_is_not_logged_as_error(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    def capture(_instance_id: str) -> np.ndarray:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    def tap(_instance_id: str, _point: Any, *, approval_region: str | None = None) -> bool:
        del approval_region
        return True

    nav = Navigator(capture, tap, redis_client=redis_async)

    async def detect_screen(_image: np.ndarray) -> ScreenName:
        return ScreenName.MAIN_CITY

    monkeypatch.setattr(nav._detector, "detect_screen", detect_screen)

    with caplog.at_level(logging.INFO, logger="navigation.navigator"):
        ok = await nav.navigate_to(ScreenName.BUILDING, "bs1")

    assert ok is False
    assert any("No navigation path" in row.message for row in caplog.records)
    assert not any(
        row.levelno >= logging.ERROR and "No navigation path" in row.message
        for row in caplog.records
    )
