#!/usr/bin/env python3
"""Extra shop findIcon cases: isSearch next_page + tab self-matches."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import cv2

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from analysis import overlay_engine  # noqa: E402
from analysis.overlay_engine import evaluate_overlay_rules_async  # noqa: E402
from config.games import default_game, modules_root_for  # noqa: E402
from layout import template_match as tm  # noqa: E402
from layout.area_manifest import load_area_doc  # noqa: E402

REFERENCES_DIR = modules_root_for(default_game(), repo_root=REPO) / "core" / "shop" / "references"
THRESHOLDS = (0.85, 0.9)

TAB_SELF = [
    ("page.shop.dawn_market.png", "page.shop.dawn_market.title", "shop.dawn_market"),
    ("page.shop.daily_deals.png", "page.shop.daily_deals.title", "shop.daily_deals"),
    ("page.shop.dawn_market.png", "shop.to.training", "shop.dawn_market"),
    ("page.shop.daily_deals.png", "shop.to.construction_queue", "shop.daily_deals"),
]


def _phash64_blur(patch_bgr: Any) -> int:
    import numpy as np

    gray = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.float32(small))
    block = dct[:8, :8].copy()
    block[0, 0] = 0
    med = float(np.median(block))
    bits = (block >= med).astype(np.uint8).reshape(-1)
    out = 0
    for bit in bits:
        out = (out << 1) | int(bit)
    return out


_ORIG = tm._phash64


def _set_blur(on: bool) -> None:
    tm._phash64 = _phash64_blur if on else _ORIG  # type: ignore[attr-defined]
    tm._template_phash_cache.clear()
    overlay_engine._template_cache.clear()


async def count_next_page(threshold: float) -> int:
    frame = cv2.imread(str(REFERENCES_DIR / "page.shop.daily_deals.png"))
    assert frame is not None
    area_doc = load_area_doc(REPO)
    found = 0
    excl: list[tuple[int, int]] = []
    for _ in range(10):
        out = await evaluate_overlay_rules_async(
            frame,
            area_doc,
            REPO,
            [
                {
                    "name": "next_page",
                    "region": "shop.tab.next_page",
                    "action": "exist",
                    "threshold": threshold,
                    "exclude_top_lefts": list(excl),
                    "exclude_radius_px": 24,
                }
            ],
            current_screen="shop.daily_deals",
        )
        row = out["next_page"]
        if not row.get("matched"):
            break
        found += 1
        tl = row.get("top_left") or (0, 0)
        excl.append((int(tl[0]), int(tl[1])))
    return found


async def main() -> int:

    fails = 0
    for thr in THRESHOLDS:
        for blur in (False, True):
            _set_blur(blur)
            n = await count_next_page(thr)
            label = "blur" if blur else "base"
            ok = n == 1
            print(f"next_page thr={thr} {label}: matches={n} {'OK' if ok else 'FAIL'}")
            if not ok and blur:
                fails += 1

    area_doc = load_area_doc(REPO)
    for png, region, screen in TAB_SELF:
        frame = cv2.imread(str(REFERENCES_DIR / png))
        assert frame is not None
        for blur in (False, True):
            _set_blur(blur)
            out = await evaluate_overlay_rules_async(
                frame,
                area_doc,
                REPO,
                [{"name": "t", "region": region, "action": "exist", "threshold": 0.7}],
                current_screen=screen,
            )
            m = out["t"].get("matched")
            label = "blur" if blur else "base"
            print(f"tab {region} @ {png} {label}: matched={m} score={out['t'].get('score')}")
            if blur and m is not True:
                fails += 1

    _set_blur(False)
    return 1 if fails else 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
