from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc


@pytest.mark.asyncio
async def test_big_claim_button_matches_on_mail_fixture() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    fixture = (
        repo_root
        / "modules/core/common/references/big_claim_button.png"
    )
    assert fixture.is_file()

    image_bgr = cv2.imread(str(fixture))
    assert image_bgr is not None

    area_doc = load_area_doc(repo_root)
    rule = {
        "name": "test.button.claim.big.visible",
        "region": "button.claim.big",
        "action": "findIcon",
        "threshold": 0.9,
    }
    out = await evaluate_overlay_rules_async(image_bgr, area_doc, repo_root, [rule])

    big_row = out.get(str(rule["name"]))
    assert isinstance(big_row, dict)
    assert big_row.get("matched") is True, big_row
    assert float(big_row.get("score") or 0.0) >= 0.9, big_row

