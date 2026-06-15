from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2
import pytest

from layout.area_manifest import load_area_doc
from navigation import (  # noqa: F401
    screen_graph,
    tab_identify_resolver,
    tab_index_resolver,
)
from tests.navigation.conftest_nav import make_navigator

if TYPE_CHECKING:
    import numpy as np

    from config.loader import Settings
    from ocr.client import OcrClient

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_navigation_hop_can_click_detected_tab_index(
    mocker,
    settings: Settings,
    ocr_client: OcrClient,
) -> None:
    frame = cv2.imread(str(REPO_ROOT / "games/wos/deals/deals/references/deals.png"))
    assert frame is not None
    taps: list[tuple[str | None, int, int]] = []

    def capture(_instance_id: str) -> np.ndarray:
        return frame

    def tap(_instance_id: str, point: Any, **kwargs: Any) -> bool:
        taps.append((kwargs.get("approval_region"), int(point.x), int(point.y)))
        return True

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client)
    mocker.patch.object(nav, "_load_area_doc", new=lambda: load_area_doc(REPO_ROOT))

    async def wait_for_screen_verified(*_args: Any) -> bool:
        return True

    mocker.patch.object(nav, "_wait_for_screen_verified", new=wait_for_screen_verified)

    result = await nav._execute_hops(
        "bs1",
        [
            (
                "deals.vault_of_enigma",
                [{"type": "tab_index", "region": "deals.tabs_strip", "index": 0}],
            )
        ],
        from_screen="deals",
    )

    assert result == "ok"
    # Y is the centre of the capsule-tight tap bbox (142), not the full strip
    # region centre (132) — the top ~20 px of the strip is padding above the tab.
    assert taps == [("deals.tabs_strip", 134, 142)]


@pytest.mark.asyncio
async def test_tab_identify_clicks_template_matched_shop_tab(
    mocker,
    settings: Settings,
    ocr_client: OcrClient,
) -> None:
    """``tab_identify`` segments the shop strip, identifies the target tab by its
    per-page icon template, and clicks it — no fixed slot index required.

    On ``page.shop.daily_deals.png`` the ``weekly_monthly_cards`` tab is the
    third visible slot; the resolver must land the tap on its centre (~78.1 %),
    matching the segmentation/identification golden in the shop nav suite.
    """
    frame = cv2.imread(
        str(REPO_ROOT / "games/wos/core/shop/references/page.shop.daily_deals.png")
    )
    assert frame is not None
    taps: list[tuple[str | None, int, int]] = []

    def capture(_instance_id: str) -> np.ndarray:
        return frame

    def tap(_instance_id: str, point: Any, **kwargs: Any) -> bool:
        taps.append((kwargs.get("approval_region"), int(point.x), int(point.y)))
        return True

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client)
    mocker.patch.object(nav, "_load_area_doc", new=lambda: load_area_doc(REPO_ROOT))

    ok = await nav._tap_tab_identify_async(
        "bs1",
        {
            "type": "tab_identify",
            "region": "shop.tabs_strip",
            "page": "shop.weekly_monthly_cards",
        },
        from_screen="shop.daily_deals",
        to_screen="shop.weekly_monthly_cards",
    )

    assert ok is True
    assert len(taps) == 1
    region, x, _y = taps[0]
    assert region == "shop.tabs_strip"
    cx_pct = x / 720.0 * 100.0
    assert abs(cx_pct - 78.1) <= 1.5, cx_pct


@pytest.mark.asyncio
async def test_tab_identify_clicks_deals_bank_tab(
    mocker,
    settings: Settings,
    ocr_client: OcrClient,
) -> None:
    """Bank is reached by ``tab_identify`` on the deals strip (it sits at the
    far-right end, so the old fixed ``bank.tab`` tap missed until scrolled).

    On ``bank.collect_ready.png`` the Bank tab is already visible, so the
    resolver identifies and clicks it without advancing.
    """
    frame = cv2.imread(
        str(REPO_ROOT / "games/wos/deals/bank/references/bank.collect_ready.png")
    )
    assert frame is not None
    taps: list[tuple[str | None, int, int]] = []

    def capture(_instance_id: str) -> np.ndarray:
        return frame

    def tap(_instance_id: str, point: Any, **kwargs: Any) -> bool:
        taps.append((kwargs.get("approval_region"), int(point.x), int(point.y)))
        return True

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client)
    mocker.patch.object(nav, "_load_area_doc", new=lambda: load_area_doc(REPO_ROOT))

    ok = await nav._tap_tab_identify_async(
        "bs1",
        {"type": "tab_identify", "region": "deals.tabs_strip", "page": "deals.bank"},
        from_screen="deals",
        to_screen="deals.bank",
    )

    assert ok is True
    assert len(taps) == 1
    assert taps[0][0] == "deals.tabs_strip"


@pytest.mark.asyncio
async def test_deals_vault_routes_to_hall_via_visible_tab_index() -> None:
    screen_graph.invalidate_edge_taps_cache()
    try:
        hops = await screen_graph.route_hops_async(
            "deals.vault_of_enigma",
            "deals.hall_of_heroes",
            instance_id="bs1",
            redis_client=None,
        )
    finally:
        screen_graph.invalidate_edge_taps_cache()

    assert hops == [
        (
            "deals.hall_of_heroes",
            [
                {
                    "type": "tab_index",
                    "region": "deals.tabs_strip",
                    "index": 2,
                }
            ],
        )
    ]


def test_deals_family_route_prefers_direct_tab_over_main_city() -> None:
    screen_graph.invalidate_screen_verify_config()
    try:
        path = screen_graph.bfs_route(
            "deals.vault_of_enigma",
            "deals.hall_of_heroes",
        )
        info = screen_graph.route_explain(
            "deals.vault_of_enigma",
            "deals.hall_of_heroes",
        )
    finally:
        screen_graph.invalidate_screen_verify_config()

    assert path == ["deals.vault_of_enigma", "deals.hall_of_heroes"]
    assert info["same_family"] is True
    assert info["selected_cost"] is not None
    assert info["main_city_cost"] is None or info["selected_cost"] < info["main_city_cost"]


@pytest.mark.asyncio
async def test_navigation_hop_can_click_detected_deals_hall_tab_from_vault(
    mocker,
    settings: Settings,
    ocr_client: OcrClient,
) -> None:
    frame = cv2.imread(
        str(
            REPO_ROOT
            / "games/wos/events/vault_of_enigma/references/page.vault_of_enigma.png"
        )
    )
    assert frame is not None
    taps: list[tuple[str | None, int, int, dict[str, Any]]] = []

    def capture(_instance_id: str) -> np.ndarray:
        return frame

    def tap(_instance_id: str, point: Any, **kwargs: Any) -> bool:
        taps.append(
            (
                kwargs.get("approval_region"),
                int(point.x),
                int(point.y),
                dict(kwargs.get("approval_context") or {}),
            )
        )
        return True

    nav = make_navigator(capture, tap, settings=settings, ocr_client=ocr_client)
    mocker.patch.object(nav, "_load_area_doc", new=lambda: load_area_doc(REPO_ROOT))

    async def wait_for_screen_verified(*_args: Any) -> bool:
        return True

    mocker.patch.object(nav, "_wait_for_screen_verified", new=wait_for_screen_verified)

    result = await nav._execute_hops(
        "bs1",
        [
            (
                "deals.hall_of_heroes",
                [
                    {
                        "type": "tab_index",
                        "region": "deals.tabs_strip",
                        "index": 2,
                    }
                ],
            )
        ],
        from_screen="deals.vault_of_enigma",
    )

    assert result == "ok"
    assert len(taps) == 1
    region, x, y, ctx = taps[0]
    assert region == "deals.tabs_strip"
    assert 500 <= x <= 590
    assert 110 <= y <= 160
    assert ctx["tab_index"] == "2"
