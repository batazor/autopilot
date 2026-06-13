from __future__ import annotations

from pathlib import Path

import cv2
import yaml

from analysis.overlay import run_overlay_analysis_sync
from layout.area_manifest import load_area_doc
from layout.area_lookup import screen_region_by_name
from layout.tab_active_detector import is_tab_active_in_bbox_percent

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]


def test_kingshot_mail_system_tab_red_dot_pushes_claim_scenario() -> None:
    image_bgr = cv2.imread(str(MODULE_DIR / "references" / "mail_page.png"))
    assert image_bgr is not None

    out = run_overlay_analysis_sync(
        image_bgr,
        repo_root=REPO_ROOT,
        analyze_yaml=MODULE_DIR / "analyze" / "analyze.yaml",
        area_doc=load_area_doc(REPO_ROOT, game="kingshot"),
        current_screen="mail.system",
    )

    row = out.get("mail.tab.system.has_red_dot")
    assert isinstance(row, dict), out
    assert row["matched"] is True
    assert row["pushScenario"] == [
        {
            "type": "mail.claim.system",
            "priority": None,
            "ttl": 300,
            "dsl_scenario": None,
        }
    ]


def test_kingshot_mail_tab_active_thresholds_identify_system_only() -> None:
    image_bgr = cv2.imread(str(MODULE_DIR / "references" / "mail_page.png"))
    assert image_bgr is not None
    area_doc = load_area_doc(REPO_ROOT, game="kingshot")

    active = []
    for tab in ("wars", "alliance", "system", "reports", "starred"):
        pair = screen_region_by_name(area_doc, f"mail.tab.{tab}")
        assert pair is not None
        if is_tab_active_in_bbox_percent(
            image_bgr,
            pair[1]["bbox"],
            max_mean_saturation=60,
            min_mean_value=210,
            min_yellow_ratio=0.9,
        ):
            active.append(tab)

    assert active == ["system"]


def test_kingshot_mail_letter_claim_matches_inside_window() -> None:
    image_bgr = cv2.imread(str(MODULE_DIR / "references" / "mail_letter.png"))
    assert image_bgr is not None

    out = run_overlay_analysis_sync(
        image_bgr,
        repo_root=REPO_ROOT,
        analyze_yaml=MODULE_DIR / "analyze" / "analyze.yaml",
        area_doc=load_area_doc(REPO_ROOT, game="kingshot"),
        current_screen="mail.letter",
    )

    row = out.get("mail.letter.claim.visible")
    assert isinstance(row, dict), out
    assert row["matched"] is True
    assert row["search_region"] == "mail.letter.window"


def test_kingshot_mail_claim_scenario_scrolls_letter_for_hidden_claim() -> None:
    scenario = yaml.safe_load(
        (MODULE_DIR / "scenarios" / "mail.claim.{tab}.yaml").read_text(
            encoding="utf-8"
        )
    )

    gift_loop = scenario["steps"][0]
    assert gift_loop["while_match"] == "mail.gift"

    letter_loop = gift_loop["steps"][2]["loop"]
    claim_step = letter_loop["steps"][0]
    assert claim_step["while_match"] == "mail.letter.claim"
    assert claim_step["search_region"] == "mail.letter.window"

    scroll_step = letter_loop["steps"][1]["swipe_direction"]
    assert scroll_step == {
        "direction": "up",
        "delta": 640,
        "duration_ms": 650,
    }
    assert gift_loop["steps"][3]["while_match"] == "mail.letter.back"


def test_kingshot_mail_claim_all_closes_rewards_popup() -> None:
    scenario = yaml.safe_load(
        (MODULE_DIR / "scenarios" / "mail.claim.{tab}.yaml").read_text(
            encoding="utf-8"
        )
    )

    claim_all_step = scenario["steps"][1]
    assert claim_all_step["while_match"] == "mail.claim.all"
    rewards_step = claim_all_step["steps"][2]
    assert rewards_step["while_match"] == "button.tap_anywhere_to_exit"
    assert rewards_step["retry"] == {"attempts": 3, "interval": "500ms"}
    assert rewards_step["steps"][0]["click"] == "button.tap_anywhere_to_exit"


def test_kingshot_mail_delete_confirm_popup_matches_confirm_button() -> None:
    image_bgr = cv2.imread(str(MODULE_DIR / "references" / "mail_delete_confirm.png"))
    assert image_bgr is not None

    out = run_overlay_analysis_sync(
        image_bgr,
        repo_root=REPO_ROOT,
        analyze_yaml=MODULE_DIR / "analyze" / "analyze.yaml",
        area_doc=load_area_doc(REPO_ROOT, game="kingshot"),
        current_screen="mail.delete_confirm",
    )

    row = out.get("mail.delete.confirm.visible")
    assert isinstance(row, dict), out
    assert row["matched"] is True


def test_kingshot_mail_delete_all_confirms_popup() -> None:
    scenario = yaml.safe_load(
        (MODULE_DIR / "scenarios" / "mail.claim.{tab}.yaml").read_text(
            encoding="utf-8"
        )
    )

    delete_step = scenario["steps"][2]
    assert delete_step["while_match"] == "mail.delete.all"
    assert delete_step["steps"][0]["click"] == "mail.delete.all"

    confirm_step = delete_step["steps"][2]
    assert confirm_step["while_match"] == "mail.delete.confirm"
    assert confirm_step["retry"] == {"attempts": 3, "interval": "500ms"}
    assert confirm_step["steps"][0]["click"] == "mail.delete.confirm"
