from __future__ import annotations

from pathlib import Path

import cv2

from analysis.overlay import run_overlay_analysis_sync
from layout.area_manifest import load_area_doc
from navigation.screen_graph import route_taps

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]


def test_kingshot_rewards_popup_matches_title_and_tap_anywhere() -> None:
    image_bgr = cv2.imread(str(MODULE_DIR / "references" / "rewards.png"))
    assert image_bgr is not None

    out = run_overlay_analysis_sync(
        image_bgr,
        repo_root=REPO_ROOT,
        analyze_yaml=MODULE_DIR / "analyze" / "analyze.yaml",
        area_doc=load_area_doc(REPO_ROOT, game="kingshot"),
        current_screen="rewards",
    )

    title = out.get("rewards.visible")
    tap_anywhere = out.get("rewards.tap_anywhere.visible")
    assert isinstance(title, dict), out
    assert isinstance(tap_anywhere, dict), out
    assert title["matched"] is True
    assert tap_anywhere["matched"] is True


def test_kingshot_rewards_exits_by_tap_anywhere() -> None:
    assert route_taps("rewards", "main_city", game="kingshot") == [
        ["button.tap_anywhere_to_exit"]
    ]
