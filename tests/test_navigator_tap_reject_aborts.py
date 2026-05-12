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


@pytest.mark.asyncio
async def test_navigate_to_aborts_when_back_button_rejected_on_unknown_screen(
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """UNKNOWN-screen recovery taps ``back_button``. If the user rejects that tap
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

    nav = Navigator(capture, tap, redis_client=redis)
    monkeypatch.setattr(nav._detector, "detect_screen", detect_unknown)
    # Force the "back_button visible" branch deterministically.
    async def _back_visible(_self, _img):  # noqa: ANN001
        return True

    monkeypatch.setattr(Navigator, "_ui_back_button_visible", _back_visible)
    monkeypatch.setattr(
        nav,
        "_load_area_doc",
        lambda: {
            "screens": [
                {
                    "id": 1,
                    "regions": [
                        {
                            "name": "back_button",
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
    assert tap_calls["n"] == 1, "rejected back_button must not retry"


@pytest.mark.asyncio
async def test_navigate_to_fast_fails_when_unknown_screen_without_back_button(
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """When the screen detector returns UNKNOWN for several consecutive ticks
    AND no ``back_button`` is visible (typical for a full-screen ad / popup
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

    nav = Navigator(capture, tap, redis_client=redis)
    monkeypatch.setattr(nav._detector, "detect_screen", detect_unknown)

    async def _no_back(_self, _img):  # noqa: ANN001
        return False

    monkeypatch.setattr(Navigator, "_ui_back_button_visible", _no_back)
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
async def test_navigate_to_aborts_when_back_button_rejected_on_unrouted_screen(
    monkeypatch: Any,
    redis_async: object,
) -> None:
    """If current screen has no route to main_city, navigator falls back to
    ``back_button``. A rejected tap there must abort, not silently continue."""
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

    nav = Navigator(capture, tap, redis_client=redis)
    monkeypatch.setattr(nav._detector, "detect_screen", detect_unrouted)
    async def _back_visible(_self, _img):  # noqa: ANN001
        return True

    monkeypatch.setattr(Navigator, "_ui_back_button_visible", _back_visible)
    monkeypatch.setattr(
        nav,
        "_load_area_doc",
        lambda: {
            "screens": [
                {
                    "id": 1,
                    "regions": [
                        {
                            "name": "back_button",
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
    assert tap_calls["n"] == 1, "rejected fallback back_button must not retry"
