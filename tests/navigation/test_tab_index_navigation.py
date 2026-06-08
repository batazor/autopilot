from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2
import pytest

from layout.area_manifest import load_area_doc
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
    assert taps == [("deals.tabs_strip", 134, 132)]
