from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from analysis import overlay_engine
from layout.area_manifest import load_area_doc

REPO_ROOT = Path(__file__).resolve().parents[5]
# The post-battle "Defeat!" screen carries the blue "Return to City" button.
# It is the same button labelled on `page.exploration.defeat`, so the
# `button.to_main_city` crop must also fire on this matchup-result frame —
# guarding the `squad_fight` scenario's `click: button.to_main_city` exit tap.
_DEFEAT_REF = (
    REPO_ROOT
    / "games/wos/core/exploration/references/page.squad_settings.status.defeat.png"
)


@pytest.mark.asyncio
async def test_to_main_city_button_detected_on_defeat_screen() -> None:
    image = cv2.imread(str(_DEFEAT_REF))
    if image is None:
        pytest.skip(f"defeat fixture missing: {_DEFEAT_REF}")

    area_doc = load_area_doc(REPO_ROOT)

    out = await overlay_engine.evaluate_overlay_rules_async(
        image,
        area_doc,
        REPO_ROOT,
        [
            {
                "name": "to_main_city.visible",
                "region": "button.to_main_city",
                "action": "findIcon",
                "threshold": 0.9,
            }
        ],
    )

    row = out["to_main_city.visible"]
    assert row["matched"] is True, (
        "button.to_main_city not detected on the defeat result frame: "
        f"{row!r}"
    )
    assert isinstance(row["top_left"], list)
