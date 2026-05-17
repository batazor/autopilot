from __future__ import annotations

from typing import Any

import numpy as np
import pytest

import navigation.navigator as navigator_module
from config.loader import Settings
from navigation.detector import ScreenName
from ocr.client import OcrClient, OCRResult
from tests.navigation.conftest_nav import make_navigator


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

    async def detect_screen(_image: np.ndarray) -> ScreenName:
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
                            "bbox": {"x": 10, "y": 20, "width": 10, "height": 10},
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

    async def detect_screen(_image: np.ndarray) -> ScreenName:
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
    mocker.patch.object(
        navigator_module,
        "evaluate_overlay_rules_async",
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
                            "bbox": {"x": 10, "y": 20, "width": 10, "height": 10},
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

    async def detect_screen(_image: np.ndarray) -> ScreenName:
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
    nav._ocr = _FakeOcr()
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
    mocker.patch.object(
        navigator_module,
        "evaluate_overlay_rules_async",
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
                            "bbox": {"x": 10, "y": 20, "width": 10, "height": 10},
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
