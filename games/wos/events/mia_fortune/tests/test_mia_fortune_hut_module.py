"""Mia's Fortune Hut: deals child page registration."""
from __future__ import annotations

from pathlib import Path

import cv2
import yaml
from games.wos.events.mia_fortune.reward_wish_items import detect_reward_wish_items

from layout.area_manifest import load_area_doc
from layout.crop_paths import exported_crop_png
from layout.red_dot_detector import has_red_dot_in_bbox_percent
from layout.template_match import match_crop_1to1_at_bbox_percent
from navigation import screen_graph

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]
SCREEN = "deals.mia_fortune_hut"
PACK_SCREEN = "deals.mia_fortune_hut.fortune_token_pack"
REWARD_WISH_SCREEN = "deals.mia_fortune_hut.reward_wish"
TITLE_REGION = "mia_fortune_hut.title"
PACK_TITLE_REGION = "fortune_token_pack.title"
PACK_FREE_REGION = "fortune_token_pack.free"
REWARD_WISH_BUTTON_REGION = "mia_fortune_hut.reward_wish.free_button"
REWARD_WISH_ITEM_REGIONS = (
    "mia_fortune_hut.reward_wish.item_1",
    "mia_fortune_hut.reward_wish.item_2",
    "mia_fortune_hut.reward_wish.item_3",
    "mia_fortune_hut.reward_wish.item_4",
)


def _screen_entry(area_doc: dict, screen_id: str) -> dict:
    entry = next(
        (screen for screen in area_doc["screens"] if screen.get("screen_id") == screen_id),
        None,
    )
    assert entry is not None
    return entry


def test_mia_fortune_hut_area_entry_and_title_crop_match() -> None:
    area_doc = load_area_doc(REPO_ROOT)
    entry = _screen_entry(area_doc, SCREEN)
    assert entry["screen_region"] == TITLE_REGION

    ref_rel = entry["ocr"]
    frame = cv2.imread(str(REPO_ROOT / ref_rel))
    assert frame is not None, f"missing fixture: {ref_rel}"

    title = next(region for region in entry["regions"] if region["name"] == TITLE_REGION)
    crop = exported_crop_png(REPO_ROOT, ref_rel, TITLE_REGION)
    template = cv2.imread(str(crop))
    assert template is not None, f"missing crop: {crop}"

    result = match_crop_1to1_at_bbox_percent(frame, template, title["bbox"])
    assert result["score"] >= title["threshold"]


def test_fortune_token_pack_box_has_red_dot_on_reference() -> None:
    area_doc = load_area_doc(REPO_ROOT)
    entry = _screen_entry(area_doc, SCREEN)
    frame = cv2.imread(str(REPO_ROOT / entry["ocr"]))
    assert frame is not None, f"missing fixture: {entry['ocr']}"

    box = next(
        region
        for region in entry["regions"]
        if region["name"] == "mia_fortune_hut.fortune_token_pack_box"
    )
    assert box["has_red_dot"] is True
    assert has_red_dot_in_bbox_percent(frame, box["bbox"]) is True


def test_fortune_token_pack_page_regions_match_fresh_reference() -> None:
    area_doc = load_area_doc(REPO_ROOT)
    entry = _screen_entry(area_doc, PACK_SCREEN)
    assert entry["screen_region"] == PACK_TITLE_REGION

    ref_rel = entry["ocr"]
    frame = cv2.imread(str(REPO_ROOT / ref_rel))
    assert frame is not None, f"missing fixture: {ref_rel}"

    for region_name in (PACK_TITLE_REGION, PACK_FREE_REGION):
        region = next(r for r in entry["regions"] if r["name"] == region_name)
        crop = exported_crop_png(REPO_ROOT, ref_rel, region_name)
        template = cv2.imread(str(crop))
        assert template is not None, f"missing crop: {crop}"
        result = match_crop_1to1_at_bbox_percent(frame, template, region["bbox"])
        assert result["score"] >= region["threshold"]


def test_reward_wish_page_has_four_item_regions() -> None:
    area_doc = load_area_doc(REPO_ROOT)
    entry = _screen_entry(area_doc, REWARD_WISH_SCREEN)
    assert entry["screen_region"] == REWARD_WISH_BUTTON_REGION

    ref_rel = entry["ocr"]
    frame = cv2.imread(str(REPO_ROOT / ref_rel))
    assert frame is not None, f"missing fixture: {ref_rel}"

    for region_name in (*REWARD_WISH_ITEM_REGIONS, REWARD_WISH_BUTTON_REGION):
        region = next(r for r in entry["regions"] if r["name"] == region_name)
        crop = exported_crop_png(REPO_ROOT, ref_rel, region_name)
        template = cv2.imread(str(crop))
        assert template is not None, f"missing crop: {crop}"
        result = match_crop_1to1_at_bbox_percent(frame, template, region["bbox"])
        assert result["score"] >= region["threshold"]


def test_reward_wish_item_detector_reads_icons_and_amounts() -> None:
    frame = cv2.imread(str(MODULE_DIR / "references" / "page.reward_wish.png"))
    assert frame is not None

    out = detect_reward_wish_items(frame, repo_root=REPO_ROOT)

    assert [item.slot for item in out] == [1, 2, 3, 4]
    assert [item.amount for item in out] == [150, 18, 120, 40]
    assert [item.item_id for item in out] == [
        "fire_crystal",
        "essence_stones",
        None,
        "gems",
    ]
    assert [item.name for item in out] == [
        "Fire Crystal",
        "Essence Stones",
        "1h General Speedup",
        "Gems",
    ]
    assert all(item.confidence >= 0.95 for item in out)
    assert all(item.amount_confidence >= 0.70 for item in out)


def test_mia_fortune_hut_screen_verify_parent_and_routes() -> None:
    screen_graph.invalidate_screen_verify_config()
    try:
        assert screen_graph.screen_verify_parent(SCREEN) == "deals"
        assert screen_graph.screen_verify_rules(SCREEN) == [
            {"match": TITLE_REGION, "threshold": 0.85}
        ]
        assert screen_graph.screen_verify_parent(PACK_SCREEN) is None
        assert screen_graph.screen_verify_rules(PACK_SCREEN) == [
            {"match": PACK_TITLE_REGION, "threshold": 0.85}
        ]
        assert screen_graph.screen_verify_parent(REWARD_WISH_SCREEN) is None
        assert screen_graph.screen_verify_rules(REWARD_WISH_SCREEN) == [
            {"match": REWARD_WISH_BUTTON_REGION, "threshold": 0.9}
        ]
        assert screen_graph.route_taps(SCREEN, PACK_SCREEN) == [
            ["mia_fortune_hut.fortune_token_pack_box"]
        ]
        assert screen_graph.route_taps(PACK_SCREEN, SCREEN) == [["icon.page.back"]]
        assert screen_graph.route_taps(REWARD_WISH_SCREEN, SCREEN) == [["icon.page.back"]]
        assert screen_graph.route_taps(SCREEN, "deals") == [["icon.page.back"]]
        assert screen_graph.route_taps(SCREEN, "main_city") == [["icon.page.back"]]
    finally:
        screen_graph.invalidate_screen_verify_config()


def test_mia_fortune_hut_scenario_opens_pack_and_clicks_free() -> None:
    scenario = yaml.safe_load(
        (MODULE_DIR / "scenarios" / "deals.mia_fortune_hut.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert scenario["enabled"] is True
    assert scenario["node"] == SCREEN
    first_step = scenario["steps"][0]
    assert first_step["while_match"] == "mia_fortune_hut.fortune_token_pack_box"
    assert first_step["isRedDot"] is True
    inner = first_step["steps"]
    assert inner[0] == {"click": "mia_fortune_hut.fortune_token_pack_box"}
    assert inner[2]["while_match"] == PACK_FREE_REGION
    assert inner[2]["steps"][0] == {"click": PACK_FREE_REGION}
    assert inner[2]["steps"][2] == {"click": "tapanywhereyoexit"}
    assert inner[3] == {"click": "icon.page.back"}
    assert inner[5] == {"click": "mia_fortune_hut.select_reward_wish"}
