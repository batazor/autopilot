from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]
REFERENCES_DIR = MODULE_DIR / "references"


def _load_reference_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference screenshot: {path}"
    return frame


@pytest.mark.asyncio
async def test_main_city_to_shop_red_dot_detected() -> None:
    frame = _load_reference_bgr("main_city.png")
    area_doc = load_area_doc(REPO_ROOT)

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        [
            {
                "name": "main_city.to.shop.has_red_dot",
                "region": "main_city.to.shop",
                "isRedDot": True,
            },
        ],
        current_screen="main_city",
    )

    row = out["main_city.to.shop.has_red_dot"]
    assert row["matched"] is True, f"expected red dot on main_city.to.shop, got: {row}"
    assert row["action"] == "red_dot"
    assert row["red_dot_present"] is True
