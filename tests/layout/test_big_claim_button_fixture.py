from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async


@pytest.mark.asyncio
async def test_big_claim_button_matches_on_mail_fixture() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    fixture = repo_root / "references" / "big_claim_button.png"
    assert fixture.is_file()

    image_bgr = cv2.imread(str(fixture))
    assert image_bgr is not None

    area_doc: dict[str, Any] = json.loads((repo_root / "area.json").read_text(encoding="utf-8"))
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

