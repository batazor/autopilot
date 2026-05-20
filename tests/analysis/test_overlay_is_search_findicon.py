from __future__ import annotations

import copy
import json
from pathlib import Path

import cv2
import pytest

from analysis import overlay_engine

REPO_ROOT = Path(__file__).resolve().parents[2]
_SKIP_FULL = REPO_ROOT / "references" / "skip_button.png"
_AREA_JSON = REPO_ROOT / "area.json"


@pytest.mark.asyncio
async def test_findicon_uses_full_frame_cache_for_is_search_region() -> None:
    image = cv2.imread(str(_SKIP_FULL))
    if image is None or not _AREA_JSON.is_file():
        pytest.skip("skip_button fixture missing")

    area_doc = json.loads(_AREA_JSON.read_text(encoding="utf-8"))
    area_doc = copy.deepcopy(area_doc)
    screen = next(
        s
        for s in area_doc.get("screens") or []
        if Path(str(s.get("ocr") or "")).stem == _SKIP_FULL.stem
    )
    region = next(
        r for r in screen.get("regions") or [] if str(r.get("name")) == "skip_button"
    )
    region["isSearch"] = True

    out = await overlay_engine.evaluate_overlay_rules_async(
        image,
        area_doc,
        REPO_ROOT,
        [
            {
                "name": "skip.visible",
                "region": "skip_button",
                "action": "findIcon",
                "threshold": 0.99,
            }
        ],
    )

    row = out["skip.visible"]
    assert row["matched"] is True
    assert row["search_region"] == "full_frame_cache"
    assert row["match_source"] in {"cache", "full_frame_ncc_phash"}
    assert isinstance(row["top_left"], list)

