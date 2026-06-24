"""The ``any_of`` navigation tap: one edge, alternative buttons.

Two buttons that both open the same screen can't be two graph edges (edges are
keyed by destination), so they are one edge whose tap is ``{type: any_of,
regions: [...]}``. It presence-checks each candidate (findIcon) and taps the
first one that is actually on screen.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from layout.types import Point
from tests.navigation.conftest_nav import make_navigator

if TYPE_CHECKING:
    from config.loader import Settings
    from ocr.client import OcrClient


def _capture(_instance_id: str) -> np.ndarray:
    return np.zeros((100, 100, 3), dtype=np.uint8)


def _tap(_instance_id: str, _point: Any, **_kw: Any) -> bool:
    return True


@pytest.mark.asyncio
async def test_any_of_taps_first_present_region(
    mocker, redis_async: object, settings: Settings, ocr_client: OcrClient
) -> None:
    nav = make_navigator(
        _capture, _tap, settings=settings, ocr_client=ocr_client, redis_client=redis_async
    )
    mocker.patch.object(nav, "_load_area_doc", new=dict)

    # Only the second candidate is on screen.
    present = {"play.frosty"}

    def fake_match(_inst: str, region_name: str, *, threshold: Any, state_flat: Any):
        del threshold, state_flat
        return Point(1, 1) if region_name in present else None

    mocker.patch.object(nav._tap_executor, "_match_search_region_for_tap", new=fake_match)

    tapped: list[str] = []

    async def fake_tap_region(_inst: str, region_name: str, **_kw: Any) -> bool:
        tapped.append(region_name)
        return True

    mocker.patch.object(nav._tap_executor, "_tap_region_name_async", new=fake_tap_region)

    spec = {"type": "any_of", "regions": ["play.free", "play.frosty"]}
    ok = await nav._tap_any_of_async("bs1", spec)

    assert ok is True
    assert tapped == ["play.frosty"]  # skipped the absent free button


@pytest.mark.asyncio
async def test_any_of_fails_when_none_present(
    mocker, redis_async: object, settings: Settings, ocr_client: OcrClient
) -> None:
    nav = make_navigator(
        _capture, _tap, settings=settings, ocr_client=ocr_client, redis_client=redis_async
    )
    mocker.patch.object(nav, "_load_area_doc", new=dict)
    mocker.patch.object(
        nav._tap_executor,
        "_match_search_region_for_tap",
        new=lambda *_a, **_k: None,  # nothing visible
    )
    tapped: list[str] = []

    async def fake_tap_region(_inst: str, region_name: str, **_kw: Any) -> bool:
        tapped.append(region_name)
        return True

    mocker.patch.object(nav._tap_executor, "_tap_region_name_async", new=fake_tap_region)

    spec = {"type": "any_of", "regions": ["play.free", "play.frosty"]}
    ok = await nav._tap_any_of_async("bs1", spec)

    assert ok is False  # none on screen → reroute, never tap blindly
    assert tapped == []
