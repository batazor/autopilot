"""Rejecting a navigation tap (click approval) aborts navigate_to instead of retrying."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from navigation.detector import ScreenName
from navigation.navigator import Navigator
from tests.navigation.conftest_nav import make_navigator

if TYPE_CHECKING:
    from config.loader import Settings
    from ocr.client import OcrClient


@pytest.mark.asyncio
async def test_navigate_to_returns_false_immediately_when_navigation_tap_rejected(
    mocker,
    redis_async: object, settings: Settings, ocr_client: OcrClient) -> None:
    redis = redis_async

    def capture(_instance_id: str) -> np.ndarray:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    tap_calls = {"n": 0}

    def tap(_instance_id: str, point: Any, **kw: Any) -> bool:
        del point, kw
        tap_calls["n"] += 1
        return False

    async def detect_survivor(_image: np.ndarray, **_kwargs) -> ScreenName:
        return ScreenName.MAIL

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client, redis_client=redis)
    mocker.patch.object(nav._detector, "detect_screen", new=detect_survivor)
    mocker.patch.object(
        nav,
        "_load_area_doc",
        new=lambda: {
            "screens": [
                {
                    "id": 1,
                    "regions": [
                        {
                            "name": "icon.page.back",
                                "bbox": {
                                    "x": 10.0,
                                    "y": 10.0,
                                    "width": 5.0,
                                    "height": 5.0,
                                    "original_width": 100,
                                    "original_height": 100,
                                },
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
    mocker,
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

    async def detect_survivor(_image: np.ndarray, **_kwargs) -> ScreenName:
        return ScreenName.MAIL

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client, redis_client=redis)
    mocker.patch.object(nav._detector, "detect_screen", new=detect_survivor)
    mocker.patch.object(
        nav,
        "_load_area_doc",
        new=lambda: {
            "screens": [
                {
                    "id": 1,
                    "regions": [
                        {
                            "name": "icon.page.back",
                                "bbox": {
                                    "x": 10.0,
                                    "y": 10.0,
                                    "width": 5.0,
                                    "height": 5.0,
                                    "original_width": 100,
                                    "original_height": 100,
                                },
                            "action": "exist",
                        },
                    ],
                },
            ],
        },
    )

    # Seed an empty current_screen the way a prior UNKNOWN tick would have.
    await redis.hset("wos:instance:bs1:state", "current_screen", "")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

    await nav.navigate_to(ScreenName.MAIN_CITY, "bs1")

    current = await redis.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    current_s = current.decode() if isinstance(current, bytes) else str(current or "")
    assert current_s == "mail", (
        f"expected current_screen='mail', got {current_s!r}"
    )


@pytest.mark.asyncio
async def test_navigate_to_aborts_when_page_back_rejected_on_unknown_screen(
    mocker,
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

    async def detect_unknown(_image: np.ndarray, **_kwargs) -> ScreenName:
        return ScreenName.UNKNOWN

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client, redis_client=redis)
    mocker.patch.object(nav._detector, "detect_screen", new=detect_unknown)
    # Force the "page back visible" branch deterministically.
    async def _back_visible(_self, _img):
        return True

    mocker.patch.object(Navigator, "_ui_page_back_visible", new=_back_visible)
    mocker.patch.object(
        nav,
        "_load_area_doc",
        new=lambda: {
            "screens": [
                {
                    "id": 1,
                    "regions": [
                        {
                            "name": "icon.page.back",
                                "bbox": {
                                    "x": 5.0,
                                    "y": 5.0,
                                    "width": 5.0,
                                    "height": 5.0,
                                    "original_width": 100,
                                    "original_height": 100,
                                },
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
    mocker,
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

    async def detect_unknown(_image: np.ndarray, **_kwargs) -> ScreenName:
        return ScreenName.UNKNOWN

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client, redis_client=redis)
    mocker.patch.object(nav._detector, "detect_screen", new=detect_unknown)

    async def _no_back(_self, _img):
        return False

    mocker.patch.object(Navigator, "_ui_page_back_visible", new=_no_back)
    mocker.patch.object(nav, "_load_area_doc", new=lambda: {"screens": []})
    # Skip the sleep so the test runs fast.
    async def _no_sleep(_secs: float) -> None:
        return None

    import navigation.navigator as navmod
    mocker.patch.object(navmod.asyncio, "sleep", new=_no_sleep)

    ok = await nav.navigate_to(ScreenName.MAIN_CITY, "bs1")

    assert ok is False
    # Counter-bail at 2 consecutive UNKNOWN-no-back ticks → loop runs 2 iters,
    # not the legacy 10. ``tap`` is never called: nothing to tap on a blocker.
    assert tap_calls["n"] == 0


@pytest.mark.asyncio
async def test_navigate_to_aborts_when_page_back_rejected_on_unrouted_screen(
    mocker,
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
        def __str__(self) -> str:
            return "screen_with_no_edges"

    unrouted = _UnroutedScreen()

    async def detect_unrouted(_image: np.ndarray, **_kwargs):
        return unrouted

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client, redis_client=redis)
    mocker.patch.object(nav._detector, "detect_screen", new=detect_unrouted)
    async def _back_visible(_self, _img):
        return True

    mocker.patch.object(Navigator, "_ui_page_back_visible", new=_back_visible)
    mocker.patch.object(
        nav,
        "_load_area_doc",
        new=lambda: {
            "screens": [
                {
                    "id": 1,
                    "regions": [
                        {
                            "name": "icon.page.back",
                                "bbox": {
                                    "x": 5.0,
                                    "y": 5.0,
                                    "width": 5.0,
                                    "height": 5.0,
                                    "original_width": 100,
                                    "original_height": 100,
                                },
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


@pytest.mark.asyncio
async def test_same_family_route_via_main_city_tries_local_advance_first(
    mocker,
    redis_async: object,
    settings: Settings,
    ocr_client: OcrClient,
) -> None:
    state = {"advanced": False}

    def capture(_instance_id: str) -> np.ndarray:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    def tap(_instance_id: str, point: Any, **kw: Any) -> bool:
        del point, kw
        return True

    async def detect_screen(_image: np.ndarray, **_kwargs) -> str:
        return "deals.hall_of_heroes" if state["advanced"] else "deals.vault_of_enigma"

    async def fake_route_hops_async(
        src: str,
        dst: str,
        **_kwargs: Any,
    ) -> list[tuple[str, list[str]]] | None:
        if src == "deals.vault_of_enigma" and dst == "deals.hall_of_heroes":
            return [
                ("main_city", ["icon.page.back"]),
                ("deals.hall_of_heroes", ["main_city.to.deals"]),
            ]
        return None

    async def local_advance(*_args: Any, **_kwargs: Any) -> bool:
        state["advanced"] = True
        return True

    async def fail_execute_hops(*_args: Any, **_kwargs: Any) -> str:
        pytest.fail("navigator should try local family advance before main_city hops")

    async def no_sleep(_seconds: float) -> None:
        return None

    import navigation.navigator as navmod

    nav = make_navigator(
        capture,
        tap,
        settings=settings,
        ocr_client=ocr_client,
        redis_client=redis_async,
    )
    mocker.patch.object(nav._detector, "detect_screen", new=detect_screen)
    mocker.patch.object(nav, "_try_family_tab_advance", new=local_advance)
    mocker.patch.object(nav, "_execute_hops", new=fail_execute_hops)
    mocker.patch.object(navmod, "route_hops_async", new=fake_route_hops_async)
    mocker.patch.object(navmod.asyncio, "sleep", new=no_sleep)

    ok = await nav._navigate_to_impl("deals.hall_of_heroes", "bs1")  # type: ignore[arg-type]

    assert ok is True
    assert state["advanced"] is True
