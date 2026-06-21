from __future__ import annotations

from pathlib import Path

import cv2
import yaml

from analysis.overlay import run_overlay_analysis_sync
from layout.area_manifest import load_area_doc
from navigation.screen_graph import route_taps

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]


def test_kingshot_vip_claim_button_matches_reference() -> None:
    image_bgr = cv2.imread(str(MODULE_DIR / "references" / "vip.png"))
    assert image_bgr is not None

    out = run_overlay_analysis_sync(
        image_bgr,
        repo_root=REPO_ROOT,
        analyze_yaml=MODULE_DIR / "analyze" / "analyze.yaml",
        area_doc=load_area_doc(REPO_ROOT, game="kingshot"),
        current_screen="vip",
    )

    claim = out.get("vip.claim.visible")
    assert isinstance(claim, dict), out
    assert claim["matched"] is True


def test_kingshot_increase_level_use_button_matches_reference() -> None:
    image_bgr = cv2.imread(str(MODULE_DIR / "references" / "increase_level.png"))
    assert image_bgr is not None

    out = run_overlay_analysis_sync(
        image_bgr,
        repo_root=REPO_ROOT,
        analyze_yaml=MODULE_DIR / "analyze" / "analyze.yaml",
        area_doc=load_area_doc(REPO_ROOT, game="kingshot"),
        current_screen="increase_level",
    )

    use = out.get("increase_level.use.visible")
    assert isinstance(use, dict), out
    assert use["matched"] is True


def test_kingshot_vip_red_dot_regions_push_daily_scenario() -> None:
    image_bgr = cv2.imread(str(MODULE_DIR / "references" / "vip.png"))
    assert image_bgr is not None

    out = run_overlay_analysis_sync(
        image_bgr,
        repo_root=REPO_ROOT,
        analyze_yaml=MODULE_DIR / "analyze" / "analyze.yaml",
        area_doc=load_area_doc(REPO_ROOT, game="kingshot"),
        current_screen="vip",
    )

    for name in ("vip.box.has_red_dot", "vip.add.has_red_dot", "vip.unlock.has_red_dot"):
        row = out.get(name)
        assert isinstance(row, dict), out
        assert row["matched"] is True
        assert row["pushScenario"] == [
            {
                "type": "vip.daily",
                "priority": None,
                "ttl": None,
                "dsl_scenario": None,
            }
        ]


def test_kingshot_vip_routes_to_and_from_main_city() -> None:
    assert route_taps("main_city", "vip", game="kingshot") == [["page.vip"]]
    assert route_taps("vip", "main_city", game="kingshot") == [["icon.page.back"]]
    assert route_taps("vip", "increase_level", game="kingshot") == [["page.vip.add"]]
    assert route_taps("increase_level", "vip", game="kingshot") == [
        ["increase_level.icon.close"]
    ]


def test_kingshot_vip_daily_scenario_claims_current_rewards() -> None:
    scenario = yaml.safe_load(
        (MODULE_DIR / "scenarios" / "by_cron" / "vip.daily.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert scenario["node"] == "vip"
    assert scenario["steps"][0]["while_match"] == "page.vip.box"
    assert scenario["steps"][0]["steps"][2]["while_match"] == (
        "button.tap_anywhere_to_exit"
    )
    assert scenario["steps"][1]["while_match"] == "button.claim"
    assert scenario["steps"][1]["steps"][2]["while_match"] == (
        "button.tap_anywhere_to_exit"
    )
    assert scenario["steps"][2]["while_match"] == "page.vip.add"
    use_step = scenario["steps"][2]["steps"][2]
    assert use_step["while_match"] == "button.use"
    assert use_step["max"] == 12
    assert use_step["steps"] == [{"long_click": "button.use"}, {"wait": "1s"}]
    assert scenario["steps"][2]["steps"][3]["while_match"] == (
        "increase_level.icon.close"
    )
