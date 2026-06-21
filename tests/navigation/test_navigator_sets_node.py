from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

import navigation.navigator as navigator_module
from navigation.detector import ScreenName
from ocr.client import OcrClient, OCRResult
from tests.navigation.conftest_nav import make_navigator

if TYPE_CHECKING:
    from config.loader import Settings


@pytest.mark.asyncio
async def test_navigator_writes_destination_node_after_route_hop(
    mocker,
    redis_async: object,
    settings: Settings,
    ocr_client: OcrClient,
) -> None:
    redis = redis_async
    taps: list[str] = []

    def capture(_instance_id: str) -> np.ndarray:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    def tap(_instance_id: str, point: Any, *, approval_region: str | None = None) -> bool:
        del approval_region
        taps.append(f"{point.x},{point.y}")
        return True

    detections = [ScreenName.MAIN_CITY, ScreenName.CHIEF_PROFILE]

    async def detect_screen(_image: np.ndarray, **_kwargs) -> ScreenName:
        return detections.pop(0) if detections else ScreenName.CHIEF_PROFILE

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client, redis_client=redis)
    mocker.patch.object(nav._detector, "detect_screen", new=detect_screen)
    mocker.patch.object(
        nav,
        "_load_area_doc",
        new=lambda: {
            "screens": [
                {
                    "id": 1,
                    "ocr": "references/main_city.png",
                    "regions": [
                        {
                            "name": "to_chief_profile",
                                "bbox": {
                                    "x": 10,
                                    "y": 20,
                                    "width": 10,
                                    "height": 10,
                                    "original_width": 100,
                                    "original_height": 100,
                                },
                        }
                    ],
                }
            ]
        },
    )

    await nav.navigate_to(ScreenName.CHIEF_PROFILE, "bs1")

    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert cur == "chief_profile"
    assert taps


@pytest.mark.asyncio
async def test_static_navigation_hop_uses_region_original_size_without_adb_wait(
    mocker,
    redis_async: object,
    settings: Settings,
    ocr_client: OcrClient,
) -> None:
    redis = redis_async
    capture_calls = 0
    taps: list[str] = []

    def capture(_instance_id: str) -> np.ndarray:
        nonlocal capture_calls
        capture_calls += 1
        return np.zeros((100, 100, 3), dtype=np.uint8)

    def tap(_instance_id: str, point: Any, *, approval_region: str | None = None) -> bool:
        taps.append(f"{approval_region}:{point.x},{point.y}")
        return True

    nav = make_navigator(
        capture,
        tap,
        settings=settings,
        ocr_client=ocr_client,
        redis_client=redis,
    )

    async def wait_for_screen_verified(*_args: Any) -> bool:
        return True

    mocker.patch.object(nav, "_wait_for_screen_verified", new=wait_for_screen_verified)
    mocker.patch.object(
        nav,
        "_load_area_doc",
        new=lambda: {
            "screens": [
                {
                    "id": 1,
                    "ocr": "references/main_city.png",
                    "regions": [
                        {
                            "name": "isWorkers",
                            "bbox": {
                                "x": 40,
                                "y": 2,
                                "width": 5,
                                "height": 5,
                                "original_width": 720,
                                "original_height": 1280,
                            },
                        }
                    ],
                }
            ]
        },
    )

    result = await nav._execute_hops(
        "bs1",
        [("survivor_status", ["isWorkers"])],
        from_screen="main_city",
    )

    assert result == "ok"
    assert taps and taps[0].startswith("isWorkers:")
    assert capture_calls == 0


@pytest.mark.asyncio
async def test_navigation_hop_can_execute_system_back_action(
    mocker,
    redis_async: object,
    settings: Settings,
    ocr_client: OcrClient,
) -> None:
    redis = redis_async
    back_calls: list[str] = []

    def capture(_instance_id: str) -> np.ndarray:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    def tap(_instance_id: str, _point: Any, **_kwargs: Any) -> bool:
        msg = "system_back route action must not use region tap"
        raise AssertionError(msg)

    def system_back(instance_id: str) -> bool:
        back_calls.append(instance_id)
        return True

    nav = make_navigator(
        capture,
        tap,
        system_back_fn=system_back,
        settings=settings,
        ocr_client=ocr_client,
        redis_client=redis,
    )

    async def wait_for_screen_verified(*_args: Any) -> bool:
        return True

    mocker.patch.object(nav, "_wait_for_screen_verified", new=wait_for_screen_verified)

    result = await nav._execute_hops(
        "bs1",
        [("main_city", [{"type": "system_back"}])],
        from_screen="rewards",
    )

    assert result == "ok"
    assert back_calls == ["bs1"]


@pytest.mark.asyncio
async def test_navigation_accepts_final_tab_when_parent_hop_opens_it(
    mocker,
    redis_async: object,
    settings: Settings,
    ocr_client: OcrClient,
) -> None:
    redis = redis_async
    taps: list[str | None] = []

    def capture(_instance_id: str) -> np.ndarray:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    def tap(_instance_id: str, point: Any, *, approval_region: str | None = None) -> bool:
        del point
        taps.append(approval_region)
        return True

    async def detect_screen(_image: np.ndarray, **_kwargs) -> ScreenName:
        return ScreenName("survivor_status.status")

    async def wait_for_screen_verified(*_args: Any) -> bool:
        msg = "parent hop should accept the final tab without retrying"
        raise AssertionError(msg)

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client, redis_client=redis)
    mocker.patch.object(nav._detector, "detect_screen", new=detect_screen)
    mocker.patch.object(nav, "_wait_for_screen_verified", new=wait_for_screen_verified)
    mocker.patch.object(
        nav,
        "_load_area_doc",
        new=lambda: {
            "screens": [
                {
                    "id": 1,
                    "ocr": "references/main_city.png",
                    "regions": [
                        {
                            "name": "isWorkers",
                            "bbox": {
                                "x": 40,
                                "y": 2,
                                "width": 5,
                                "height": 5,
                                "original_width": 720,
                                "original_height": 1280,
                            },
                        },
                        {
                            "name": "survivor_status.status",
                            "bbox": {
                                "x": 16,
                                "y": 91,
                                "width": 20,
                                "height": 6,
                                "original_width": 720,
                                "original_height": 1280,
                            },
                        },
                    ],
                }
            ]
        },
    )

    result = await nav._execute_hops(
        "bs1",
        [
            ("survivor_status", ["isWorkers"]),
            ("survivor_status.status", ["survivor_status.status"]),
        ],
        from_screen="main_city",
    )

    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert result == "ok"
    assert cur == "survivor_status.status"
    assert taps == ["isWorkers"]


@pytest.mark.asyncio
async def test_navigator_verifies_destination_with_match_rule(
    mocker,
    redis_async: object,
    settings: Settings,
    ocr_client: OcrClient,
) -> None:
    redis = redis_async

    def capture(_instance_id: str) -> np.ndarray:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    def tap(_instance_id: str, point: Any, *, approval_region: str | None = None) -> bool:
        del point, approval_region
        return True

    detections = [ScreenName.MAIN_CITY]

    async def detect_screen(_image: np.ndarray, **_kwargs) -> ScreenName:
        return detections.pop(0) if detections else ScreenName.UNKNOWN

    async def evaluate_overlay_rules_async(
        _image: np.ndarray,
        _area_doc: dict[str, Any],
        _repo_root: Any,
        rules: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        name = str(rules[0]["name"])
        return {name: {"matched": True, "region": "chief_profile_title"}}

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client, redis_client=redis)
    mocker.patch.object(nav._detector, "detect_screen", new=detect_screen)
    mocker.patch.object(
        navigator_module,
        "screen_verify_rules",
        new=lambda _target: [{"match": "chief_profile_title", "threshold": 0.92}],
    )
    mocker.patch.object(navigator_module, "screen_verify_retry", new=lambda _target: (1, 0.0))
    mocker.patch(
        "navigation.screen_verifier.evaluate_overlay_rules_async",
        new=evaluate_overlay_rules_async,
    )
    mocker.patch.object(
        nav,
        "_load_area_doc",
        new=lambda: {
            "screens": [
                {
                    "id": 1,
                    "regions": [
                        {
                            "name": "to_chief_profile",
                                "bbox": {
                                    "x": 10,
                                    "y": 20,
                                    "width": 10,
                                    "height": 10,
                                    "original_width": 100,
                                    "original_height": 100,
                                },
                        },
                        {
                            "name": "chief_profile_title",
                            "bbox": {"x": 10, "y": 10, "width": 20, "height": 5},
                        },
                    ],
                }
            ]
        },
    )

    await nav.navigate_to(ScreenName.CHIEF_PROFILE, "bs1")

    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert cur == "chief_profile"


@pytest.mark.asyncio
async def test_navigator_verifies_destination_with_ocr_contains(
    mocker,
    redis_async: object,
    settings: Settings,
    ocr_client: OcrClient,
) -> None:
    redis = redis_async

    def capture(_instance_id: str) -> np.ndarray:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    def tap(_instance_id: str, point: Any, *, approval_region: str | None = None) -> bool:
        del point, approval_region
        return True

    detections = [ScreenName.MAIN_CITY]

    async def detect_screen(_image: np.ndarray, **_kwargs) -> ScreenName:
        return detections.pop(0) if detections else ScreenName.UNKNOWN

    class _FakeOcr:
        async def ocr_region(self, _image: np.ndarray, _region: Any, **_kwargs: Any) -> OCRResult:
            return OCRResult(region_id="r0", text="Chief Profile", confidence=0.99)

    async def evaluate_overlay_rules_async(
        _image: np.ndarray,
        _area_doc: dict[str, Any],
        _repo_root: Any,
        rules: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        name = str(rules[0]["name"])
        return {name: {"matched": False}}

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client, redis_client=redis)
    nav._ocr = _FakeOcr()  # ty: ignore[invalid-assignment]
    mocker.patch.object(nav._detector, "detect_screen", new=detect_screen)
    mocker.patch.object(
        navigator_module,
        "screen_verify_rules",
        new=lambda _target: [
            {"match": "chief_profile_title", "threshold": 0.92},
            {"ocr": "page_title", "contains": "Chief Profile"},
        ],
    )
    mocker.patch.object(navigator_module, "screen_verify_retry", new=lambda _target: (1, 0.0))
    mocker.patch(
        "navigation.screen_verifier.evaluate_overlay_rules_async",
        new=evaluate_overlay_rules_async,
    )
    mocker.patch.object(
        nav,
        "_load_area_doc",
        new=lambda: {
            "screens": [
                {
                    "id": 1,
                    "regions": [
                        {
                            "name": "to_chief_profile",
                                "bbox": {
                                    "x": 10,
                                    "y": 20,
                                    "width": 10,
                                    "height": 10,
                                    "original_width": 100,
                                    "original_height": 100,
                                },
                        },
                        {
                            "name": "page_title",
                            "bbox": {"x": 10, "y": 10, "width": 20, "height": 5},
                        },
                        {
                            "name": "chief_profile_title",
                            "bbox": {"x": 10, "y": 10, "width": 20, "height": 5},
                        },
                    ],
                }
            ]
        },
    )

    await nav.navigate_to(ScreenName.CHIEF_PROFILE, "bs1")

    cur = await redis_async.hget("wos:instance:bs1:state", "current_screen")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert cur == "chief_profile"
