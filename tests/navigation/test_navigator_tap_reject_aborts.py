"""Rejecting a navigation tap (click approval) aborts navigate_to instead of retrying."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from tests.navigation.conftest_nav import make_navigator

from config.loader import Settings
from navigation.detector import ScreenName
from navigation.navigator import Navigator
from ocr.client import OcrClient


@pytest.mark.asyncio
async def test_navigate_to_returns_false_immediately_when_navigation_tap_rejected(
    monkeypatch: Any,
    redis_async: object, settings: Settings, ocr_client: OcrClient) -> None:
    redis = redis_async

    def capture(_instance_id: str) -> np.ndarray:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    tap_calls = {"n": 0}

    def tap(_instance_id: str, point: Any, **kw: Any) -> bool:
        del point, kw
        tap_calls["n"] += 1
        return False

    async def detect_survivor(_image: np.ndarray) -> ScreenName:
        return ScreenName.MAIL

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client, redis_client=redis)
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
                            "name": "icon.page.back",
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


@pytest.mark.asyncio
async def test_navigate_to_persists_intermediate_screen_identity(
    monkeypatch: Any,
    redis_async: object, settings: Settings, ocr_client: OcrClient) -> None:
    """``navigate_to`` must write ``current_screen`` for any recognised
    screen, not only the target.

    Regression: previously the only writes happened on ``current == target``
    and ``current == UNKNOWN``. A single transient UNKNOWN tick blanked
    ``current_screen``, and every later iteration that recognised the real
    screen silently left the empty value in Redis. The approvals UI then
    rendered "no identity" while the device was plainly on a known page
    (e.g. ``from_screen: heroes`` in the approval payload but
    ``current_screen=""`` in the state hash).
    """
    redis = redis_async

    def capture(_instance_id: str) -> np.ndarray:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    def tap(_instance_id: str, point: Any, **kw: Any) -> bool:
        del point, kw
        return False

    async def detect_survivor(_image: np.ndarray) -> ScreenName:
        return ScreenName.MAIL

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client, redis_client=redis)
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
                            "name": "icon.page.back",
                            "bbox": {"x": 10.0, "y": 10.0, "width": 5.0, "height": 5.0},
                            "action": "exist",
                        },
                    ],
                },
            ],
        },
    )

    # Seed an empty current_screen the way a prior UNKNOWN tick would have.
    await redis.hset("wos:instance:bs1:state", "current_screen", "")

    await nav.navigate_to(ScreenName.MAIN_CITY, "bs1")

    current = await redis.hget("wos:instance:bs1:state", "current_screen")
    current_s = current.decode() if isinstance(current, bytes) else str(current or "")
    assert current_s == "mail", (
        f"expected current_screen='mail', got {current_s!r}"
    )


@pytest.mark.asyncio
async def test_navigate_to_aborts_when_page_back_rejected_on_unknown_screen(
    monkeypatch: Any,
    redis_async: object, settings: Settings, ocr_client: OcrClient) -> None:
    """UNKNOWN-screen recovery taps ``icon.page.back``. If the user rejects that tap
    in approval mode, ``navigate_to`` must abort instead of looping 10 times."""
    redis = redis_async

    def capture(_instance_id: str) -> np.ndarray:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    tap_calls = {"n": 0}

    def tap(_instance_id: str, point: Any, **kw: Any) -> bool:
        del point, kw
        tap_calls["n"] += 1
        return False

    async def detect_unknown(_image: np.ndarray) -> ScreenName:
        return ScreenName.UNKNOWN

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client, redis_client=redis)
    monkeypatch.setattr(nav._detector, "detect_screen", detect_unknown)
    # Force the "page back visible" branch deterministically.
    async def _back_visible(_self, _img):  # noqa: ANN001
        return True

    monkeypatch.setattr(Navigator, "_ui_page_back_visible", _back_visible)
    monkeypatch.setattr(
        nav,
        "_load_area_doc",
        lambda: {
            "screens": [
                {
                    "id": 1,
                    "regions": [
                        {
                            "name": "icon.page.back",
                            "bbox": {"x": 5.0, "y": 5.0, "width": 5.0, "height": 5.0},
                            "action": "exist",
                        },
                    ],
                },
            ],
        },
    )

    ok = await nav.navigate_to(ScreenName.MAIN_CITY, "bs1")
    assert ok is False
    assert tap_calls["n"] == 1, "rejected page back must not retry"


@pytest.mark.asyncio
async def test_navigate_to_fast_fails_when_unknown_screen_without_page_back(
    monkeypatch: Any,
    redis_async: object, settings: Settings, ocr_client: OcrClient) -> None:
    """When the screen detector returns UNKNOWN for several consecutive ticks
    AND no ``icon.page.back`` is visible (typical for a full-screen ad / popup
    covering the UI), the navigator must bail quickly so the worker frees up
    for the overlay scanner to push the popup-dismissal scenario. Previously
    it looped 10 × ~1.5s, blocking the worker for ~15s and starving the
    higher-priority ad-dismiss task.
    """
    redis = redis_async

    def capture(_instance_id: str) -> np.ndarray:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    tap_calls = {"n": 0}

    def tap(_instance_id: str, point: Any, **kw: Any) -> bool:
        del point, kw
        tap_calls["n"] += 1
        return True

    async def detect_unknown(_image: np.ndarray) -> ScreenName:
        return ScreenName.UNKNOWN

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client, redis_client=redis)
    monkeypatch.setattr(nav._detector, "detect_screen", detect_unknown)

    async def _no_back(_self, _img):  # noqa: ANN001
        return False

    monkeypatch.setattr(Navigator, "_ui_page_back_visible", _no_back)
    monkeypatch.setattr(nav, "_load_area_doc", lambda: {"screens": []})
    # Skip the sleep so the test runs fast.
    async def _no_sleep(_secs: float) -> None:
        return None

    import navigation.navigator as navmod
    monkeypatch.setattr(navmod.asyncio, "sleep", _no_sleep)

    ok = await nav.navigate_to(ScreenName.MAIN_CITY, "bs1")

    assert ok is False
    # Counter-bail at 2 consecutive UNKNOWN-no-back ticks → loop runs 2 iters,
    # not the legacy 10. ``tap`` is never called: nothing to tap on a blocker.
    assert tap_calls["n"] == 0


@pytest.mark.asyncio
async def test_navigate_to_aborts_when_page_back_rejected_on_unrouted_screen(
    monkeypatch: Any,
    redis_async: object, settings: Settings, ocr_client: OcrClient) -> None:
    """If current screen has no route to main_city, navigator falls back to
    ``icon.page.back``. A rejected tap there must abort, not silently continue."""
    redis = redis_async

    def capture(_instance_id: str) -> np.ndarray:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    tap_calls = {"n": 0}

    def tap(_instance_id: str, point: Any, **kw: Any) -> bool:
        del point, kw
        tap_calls["n"] += 1
        return False

    # Screen with no edge in edge_taps.yaml → ``route_hops(...)`` returns None.
    class _UnroutedScreen:
        def __str__(self) -> str:  # noqa: D401
            return "screen_with_no_edges"

    unrouted = _UnroutedScreen()

    async def detect_unrouted(_image: np.ndarray):  # noqa: ANN202
        return unrouted

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client, redis_client=redis)
    monkeypatch.setattr(nav._detector, "detect_screen", detect_unrouted)
    async def _back_visible(_self, _img):  # noqa: ANN001
        return True

    monkeypatch.setattr(Navigator, "_ui_page_back_visible", _back_visible)
    monkeypatch.setattr(
        nav,
        "_load_area_doc",
        lambda: {
            "screens": [
                {
                    "id": 1,
                    "regions": [
                        {
                            "name": "icon.page.back",
                            "bbox": {"x": 5.0, "y": 5.0, "width": 5.0, "height": 5.0},
                            "action": "exist",
                        },
                    ],
                },
            ],
        },
    )

    ok = await nav.navigate_to(ScreenName.MAIN_CITY, "bs1")
    assert ok is False
    assert tap_calls["n"] == 1, "rejected fallback page back must not retry"
