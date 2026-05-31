"""Regression coverage for real WOS ad popups."""

from __future__ import annotations

from pathlib import Path

import cv2  # type: ignore[import-untyped]
import pytest

from ocr.client import OCRResult
from popup.detector import PopupDetector
from popup.models import PopupKind

MODULE_DIR = Path(__file__).resolve().parents[1]
REFERENCES_DIR = MODULE_DIR / "references"


class _PriceOcr:
    async def ocr_region(
        self,
        _image: object,
        _bbox: object,
        *,
        region_id: str,
    ) -> OCRResult:
        return OCRResult(region_id=region_id, text="$4.99", confidence=1.0)


@pytest.mark.parametrize(
    ("screenshot", "close_x", "close_y"),
    [
        ("craftsmans_treasure.png", (640, 690), (205, 255)),
        ("ads_rookie_value_pack.png", (585, 635), (105, 155)),
        ("ads.legend_transcend_pack.png", (585, 635), (105, 155)),
    ],
)
async def test_purchase_ad_popup_detected_from_reference(
    screenshot: str,
    close_x: tuple[int, int],
    close_y: tuple[int, int],
) -> None:
    """Purchase ad popups are detected with a tappable top-right X."""
    frame = cv2.imread(str(REFERENCES_DIR / screenshot))
    assert frame is not None

    state = await PopupDetector(_PriceOcr()).detect(frame)

    assert state.kind == PopupKind.PURCHASE
    assert state.signals.overlay_present is True
    assert state.bbox is not None
    assert state.close_point is not None
    assert state.primary_point is None

    # Pin the generic close locator to the visible white X, not the broad
    # top-right fallback slice.
    assert close_x[0] <= state.close_point.x <= close_x[1]
    assert close_y[0] <= state.close_point.y <= close_y[1]
