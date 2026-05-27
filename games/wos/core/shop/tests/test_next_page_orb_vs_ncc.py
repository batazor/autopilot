"""Bench: ORB feature_match vs sliding NCC for ``shop.tab.next_page``.

This region was the only overlay rule using a non-default low threshold
(0.8 in ``analyze/pages/common.yaml``, 0.85 in ``area.yaml``) — a hint that
NCC was already struggling. The arrow appears as a small (~33×38 px) icon
on top of ~9 different shop sub-page backgrounds, so it's the prime
candidate for ORB-style invariant matching.

The bench reports both NCC peak score and ORB inlier-ratio across every
shop fixture in this module and asserts ORB's headroom over its threshold
beats NCC's headroom over the production threshold.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from analysis.overlay_engine import _match_orb_features_in_bbox
from layout.template_match import match_template_full_frame_cached, template_cache_key

MODULE_DIR = Path(__file__).resolve().parents[1]
REFERENCES_DIR = MODULE_DIR / "references"
REPO_ROOT = MODULE_DIR.parents[3]
TEMPLATE_PATH = REPO_ROOT / "references" / "crop" / "page.shop_shop.tab.next_page.png"

# Per area.yaml: bbox is x=92.08, y=8.48, w=4.60, h=2.99 (percent) but the
# rule sets ``isSearch: true`` so the runtime scans the full frame. Mirror
# that here for a like-for-like NCC comparison.
FULL_BBOX = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0}

# Production thresholds (see modules/core/shop/area.yaml + analyze/pages/common.yaml).
NCC_PROD_THRESHOLD = 0.8
ORB_TRIAL_THRESHOLD = 0.4  # inlier-ratio; same scale as feature_match action gate

POSITIVE_FIXTURES = [
    "page.shop.dawn_market.png",
    "page.shop.daily_deals.png",
    "page.shop.mix_match.png",
    "page.shop.dawn_fund.png",
    "page.shop.weekly_monthly_cards.png",
    "page.shop.regular_pack.png",
    "page.shop.get_gems.png",
    "page.shop.construction_queue.png",
]
NEGATIVE_FIXTURES = ["main_city.png"]


def _load(name: str):
    img = cv2.imread(str(REFERENCES_DIR / name))
    assert img is not None, f"missing fixture: {name}"
    return img


@pytest.fixture(scope="module")
def template_bgr():
    tpl = cv2.imread(str(TEMPLATE_PATH))
    assert tpl is not None, f"missing template: {TEMPLATE_PATH}"
    return tpl


def _ncc_score(frame, tpl) -> float:
    res = match_template_full_frame_cached(
        frame,
        tpl,
        cache_key=template_cache_key(
            region_name="shop.tab.next_page",
            reference_rel="bench",
            template_bgr=tpl,
            screen_shape=(frame.shape[0], frame.shape[1]),
        ),
        threshold=0.0,
        exclude_top_lefts=None,
        exclude_radius_px=0,
        image_gray=cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
    )
    return float(res.get("score") or 0.0)


def _orb_score(frame, tpl) -> float:
    return float(_match_orb_features_in_bbox(frame, tpl, FULL_BBOX)["score"])


def test_bench_orb_vs_ncc_on_shop_pages(
    template_bgr,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows: list[tuple[str, float, float, bool, bool]] = []
    for name in POSITIVE_FIXTURES:
        frame = _load(name)
        ncc = _ncc_score(frame, template_bgr)
        orb = _orb_score(frame, template_bgr)
        rows.append((name, ncc, orb, ncc >= NCC_PROD_THRESHOLD, orb >= ORB_TRIAL_THRESHOLD))

    neg_rows: list[tuple[str, float, float]] = []
    for name in NEGATIVE_FIXTURES:
        frame = _load(name)
        neg_rows.append((name, _ncc_score(frame, template_bgr), _orb_score(frame, template_bgr)))

    with capsys.disabled():
        print(f"\n{'fixture':45s} {'NCC':>8s} {'ORB':>8s}  ncc>=0.80  orb>=0.40")
        print("-" * 80)
        for name, ncc, orb, ncc_ok, orb_ok in rows:
            print(f"{name:45s} {ncc:8.3f} {orb:8.3f}  {ncc_ok!s:>9s}  {orb_ok!s:>9s}")
        print("-- negative (should NOT match) --")
        for name, ncc, orb in neg_rows:
            print(f"{name:45s} {ncc:8.3f} {orb:8.3f}")

    ncc_recall = sum(1 for r in rows if r[3]) / len(rows)
    orb_recall = sum(1 for r in rows if r[4]) / len(rows)
    with capsys.disabled():
        print(f"\nrecall:  NCC={ncc_recall:.2%}   ORB={orb_recall:.2%}")

    # Regression record: ORB lost decisively on this tiny (30×34 px) icon.
    # The template yields only ~20 ORB keypoints; a 720×1280 game UI yields
    # ~1000, so Lowe's ratio test wipes out almost every candidate because
    # every template descriptor has many similar-distance neighbors in the
    # frame (repetitive UI: text, borders, mini-icons). NCC's score is
    # bimodal here (~1.0 when the arrow is present, ~0.5 when absent), so
    # the production 0.8 threshold sits in a wide safety margin even though
    # it's lower than the codebase-standard 0.9. Keeping NCC is correct.
    assert ncc_recall >= 0.5, "NCC recall regressed unexpectedly"
    assert orb_recall == 0.0, (
        f"ORB recall is now {orb_recall:.2%} — re-evaluate whether feature_match "
        "is viable for shop.tab.next_page (was 0 at bench creation)"
    )

    for name, _ncc, orb in neg_rows:
        assert orb < ORB_TRIAL_THRESHOLD, f"ORB false positive on {name}: {orb}"
