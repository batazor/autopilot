#!/usr/bin/env python3
"""Simulate Gaussian blur before pHash on shop module title matrix (no code change)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from analysis import overlay_engine  # noqa: E402
from analysis.overlay_engine import evaluate_overlay_rules_async  # noqa: E402
from config.games import default_game, modules_root_for  # noqa: E402
from layout import template_match as tm  # noqa: E402
from layout.area_manifest import load_area_doc  # noqa: E402

MODULE_DIR = modules_root_for(default_game(), repo_root=REPO) / "core" / "shop"
REFERENCES_DIR = MODULE_DIR / "references"
THRESHOLD = 0.9

PAGES: list[tuple[str, str, str]] = [
    ("shop.dawn_market", "page.shop.dawn_market.title", "page.shop.dawn_market.png"),
    ("shop.daily_deals", "page.shop.daily_deals.title", "page.shop.daily_deals.png"),
    ("shop.mix_match", "page.shop.mix_match.title", "page.shop.mix_match.png"),
    ("shop.dawn_fund", "page.shop.dawn_fund.title", "page.shop.dawn_fund.png"),
    (
        "shop.construction_queue",
        "page.shop.construction_queue.title",
        "page.shop.construction_queue.png",
    ),
    (
        "shop.weekly_monthly_cards",
        "page.shop.weekly_monthly_cards.title",
        "page.shop.weekly_monthly_cards.png",
    ),
    ("shop.get_gems", "page.shop.get_gems.title", "page.shop.get_gems.png"),
    ("shop.regular_pack", "page.shop.regular_pack.title", "page.shop.regular_pack.png"),
]


async def _title_match_score(
    frame: np.ndarray,
    area_doc: dict[str, Any],
    title_region: str,
    screen_id: str,
) -> float:
    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO,
        [{"name": "t", "region": title_region, "action": "findIcon", "threshold": 0.0}],
        current_screen=screen_id,
    )
    return float(out["t"].get("score") or 0.0)


def _phash64_blur(patch_bgr: np.ndarray) -> int:
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


def _install_blur_phash() -> None:
    tm._phash64 = _phash64_blur  # type: ignore[attr-defined]
    tm._template_phash_cache.clear()
    overlay_engine._template_cache.clear()


def _restore_phash() -> None:
    # Re-import original by reloading module is heavy; save ref at start
    tm._phash64 = _ORIG_PHASH64  # type: ignore[attr-defined]
    tm._template_phash_cache.clear()
    overlay_engine._template_cache.clear()


_ORIG_PHASH64 = tm._phash64


async def _eval_matrix(
    area_doc: dict[str, Any],
    *,
    label: str,
) -> tuple[int, int, list[str]]:
    """Return (self_failures, cross_false_positives, detail lines)."""
    self_fail = 0
    cross_fp = 0
    lines: list[str] = []

    for screen_id, title_region, screenshot in PAGES:
        frame = cv2.imread(str(REFERENCES_DIR / screenshot))
        assert frame is not None
        out = await evaluate_overlay_rules_async(
            frame,
            area_doc,
            REPO,
            [
                {
                    "name": "t",
                    "region": title_region,
                    "action": "findIcon",
                    "threshold": THRESHOLD,
                }
            ],
            current_screen=screen_id,
        )
        row = out["t"]
        if row.get("matched") is not True:
            self_fail += 1
            lines.append(
                f"SELF FAIL [{label}] {screenshot} {title_region}: "
                f"score={row.get('score')} ncc={row.get('score_ncc')} matched={row.get('matched')}"
            )

    for source_screen, _source_title, source_png, other_screen, other_title in [
        (sn, st, sp, on, ot)
        for sn, st, sp in PAGES
        for on, ot, _ in PAGES
        if sn != on
    ]:
        frame = cv2.imread(str(REFERENCES_DIR / source_png))
        assert frame is not None
        out = await evaluate_overlay_rules_async(
            frame,
            area_doc,
            REPO,
            [
                {
                    "name": "t",
                    "region": other_title,
                    "action": "findIcon",
                    "threshold": THRESHOLD,
                }
            ],
            current_screen=source_screen,
        )
        row = out["t"]
        if row.get("matched") is True:
            cross_fp += 1
            lines.append(
                f"CROSS FP [{label}] on {source_png} matched {other_title} "
                f"(from {other_screen}) score={row.get('score')} ncc={row.get('score_ncc')}"
            )

    return self_fail, cross_fp, lines


async def main() -> int:

    area_doc = load_area_doc(REPO)

    _restore_phash()
    base_self, base_cross, base_lines = await _eval_matrix(area_doc, label="baseline")
    _install_blur_phash()
    blur_self, blur_cross, blur_lines = await _eval_matrix(area_doc, label="blur3x3")
    _restore_phash()

    print("Shop page title matrix (threshold=0.9, same as test_shop_page_title_detection)")
    print(f"  baseline: self_fail={base_self} cross_fp={base_cross}")
    print(f"  blur3x3:  self_fail={blur_self} cross_fp={blur_cross}")
    print()

    # Score deltas on diagonal self-match
    print("Diagonal self-match scores (baseline -> blur):")
    for screen_id, title_region, screenshot in PAGES:
        frame = cv2.imread(str(REFERENCES_DIR / screenshot))
        assert frame is not None

        _restore_phash()
        s0 = await _title_match_score(frame, area_doc, title_region, screen_id)
        _install_blur_phash()
        s1 = await _title_match_score(frame, area_doc, title_region, screen_id)
        _restore_phash()
        delta = s1 - s0
        print(f"  {title_region:<42} {s0:.4f} -> {s1:.4f} ({delta:+.4f})")

    if base_lines or blur_lines:
        print("\n--- Failures ---")
        for ln in base_lines + blur_lines:
            print(ln)

    ok = blur_self == 0 and blur_cross == 0
    print()
    print("PASS blur regression" if ok else "FAIL blur regression")
    return 0 if ok else 1


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
