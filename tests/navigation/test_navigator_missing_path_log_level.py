from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from navigation.detector import ScreenName
from tests.navigation.conftest_nav import make_navigator

if TYPE_CHECKING:
    from config.loader import Settings
    from ocr.client import OcrClient


@pytest.mark.asyncio
async def test_missing_navigation_path_is_not_logged_as_error(
    caplog: pytest.LogCaptureFixture,
    mocker,
    redis_async: object, settings: Settings, ocr_client: OcrClient) -> None:
    def capture(_instance_id: str) -> np.ndarray:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    def tap(_instance_id: str, _point: Any, *, approval_region: str | None = None) -> bool:
        del approval_region
        return True

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client, redis_client=redis_async)

    async def detect_screen(_image: np.ndarray) -> ScreenName:
        return ScreenName.MAIN_CITY

    mocker.patch.object(nav._detector, "detect_screen", new=detect_screen)

    # LOADING is in ``ScreenName`` (it's a real splash screen) but has no
    # incoming edge in edge_taps.yaml — you can't navigate *to* loading. This
    # exercises the "no route" branch deterministically.
    with caplog.at_level(logging.INFO, logger="navigation.navigator"):
        ok = await nav.navigate_to(ScreenName.LOADING, "bs1")

    assert ok is False
    assert any("No navigation path" in row.message for row in caplog.records)
    assert not any(
        row.levelno >= logging.ERROR and "No navigation path" in row.message
        for row in caplog.records
    )
