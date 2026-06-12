"""Structural checks for troop training screen modules."""
from __future__ import annotations

from pathlib import Path

import cv2
import pytest
import yaml

from config.paths import repo_root
from layout.crop_paths import exported_crop_png
from layout.template_match import match_crop_1to1_at_bbox_percent
from navigation import screen_graph
from services import bind_active_game

TROOP_TYPES = ("infantry", "lancer", "marksman")
TROOPS_DIR = Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> dict:
    assert path.exists(), f"missing: {path}"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@pytest.mark.parametrize("troop_type", TROOP_TYPES)
def test_troop_module_manifest(troop_type: str) -> None:
    module_dir = TROOPS_DIR / troop_type
    manifest = _load_yaml(module_dir / "module.yaml")

    assert manifest["id"] == troop_type
    assert manifest["enabled"] is True
    assert manifest["area"] == "area.yaml"
    assert manifest["analyze"] == "analyze/analyze.yaml"
    assert manifest["references"] == "references"
    assert manifest["scenarios"] == "scenarios"


@pytest.mark.parametrize("troop_type", TROOP_TYPES)
def test_troop_module_declares_screen_and_routes(troop_type: str) -> None:
    module_dir = TROOPS_DIR / troop_type
    area = _load_yaml(module_dir / "area.yaml")
    screen_ids = {screen["screen_id"] for screen in area["screens"]}
    regions = {
        region["name"]: region
        for screen in area["screens"]
        for region in screen.get("regions", [])
    }

    assert troop_type in screen_ids
    complete = regions[f"{troop_type}.complete"]
    assert complete["action"] in {"click", "exist"}
    assert complete["bbox"]["original_width"] == 720
    assert complete["bbox"]["original_height"] == 1280

    edges = _load_yaml(module_dir / "routes" / "edge_taps.yaml")["edges"]
    assert edges[troop_type]["main_menu"] == ["icon.page.back"]

    verify = _load_yaml(module_dir / "routes" / "screen_verify.yaml")["screens"]
    assert verify[troop_type]["rules"] == [{"from_screen": "main_menu"}]


@pytest.mark.parametrize("troop_type", TROOP_TYPES)
def test_troop_complete_overlay_pushes_training_scenario(troop_type: str) -> None:
    module_dir = TROOPS_DIR / troop_type
    analyze = _load_yaml(module_dir / "analyze" / "analyze.yaml")
    rules = {rule["name"]: rule for rule in analyze["overlay"]}

    rule = rules[f"{troop_type}.complete.visible"]
    assert rule["region"] == f"{troop_type}.complete"
    assert rule["action"] == "findIcon"
    assert troop_type in rule["screens"]
    assert {"push_scenario": {"name": f"troops.{troop_type}.train", "ttl": "1m"}} in rule[
        "steps"
    ]


@pytest.mark.parametrize("troop_type", TROOP_TYPES)
def test_troop_training_scenario_clicks_complete(troop_type: str) -> None:
    module_dir = TROOPS_DIR / troop_type
    scenario = _load_yaml(
        module_dir / "scenarios" / f"troops.{troop_type}.train.yaml"
    )

    assert scenario["enabled"] is True
    assert scenario["node"] == troop_type
    assert scenario["cond"] == "active_player != null"
    expected_steps = [{"click": f"{troop_type}.complete"}, {"wait": "1s"}]
    if troop_type == "infantry":
        expected_steps.extend(
            [
                {"click": "infantry.train"},
                {"wait": "1s"},
                {
                    "while_match": "infantry.tap_anywhere",
                    "max": 1,
                    "steps": [
                        {"click": "infantry.tap_anywhere"},
                        {"wait": "1s"},
                    ],
                },
                {
                    "while_match": "train.upgrade",
                    "max": 1,
                    "steps": [
                        {"click": "train.upgrade"},
                        {"wait": "1s"},
                        {"click": "troops.upgrade.start"},
                        {"wait": "1s"},
                        {"click": "troops.promotion"},
                        {"wait": "1s"},
                    ],
                    "else": [
                        {"click": "troops.train.start"},
                        {"wait": "1s"},
                    ],
                },
            ]
        )
    assert scenario["steps"] == expected_steps


def test_infantry_options_declares_action_regions_and_crops() -> None:
    module_dir = TROOPS_DIR / "infantry"
    area = _load_yaml(module_dir / "area.yaml")
    options_screen = next(
        screen for screen in area["screens"] if screen.get("ocr") == "references/options.png"
    )
    regions = {region["name"]: region for region in options_screen["regions"]}
    frame = cv2.imread(str(module_dir / "references" / "options.png"), cv2.IMREAD_COLOR)
    assert frame is not None

    for action in ("details", "upgrade", "train"):
        region_name = f"infantry.{action}"
        region = regions[region_name]
        assert region["action"] == "click"
        assert region["bbox"]["original_width"] == 720
        assert region["bbox"]["original_height"] == 1280

        crop = exported_crop_png(
            repo_root(),
            "games/wos/troops/infantry/references/options.png",
            region_name,
        )
        assert crop.is_file(), f"missing crop: {crop}"
        template = cv2.imread(str(crop), cv2.IMREAD_COLOR)
        assert template is not None
        result = match_crop_1to1_at_bbox_percent(frame, template, region["bbox"])
        assert result["score"] >= 0.99


def test_infantry_new_troop_popup_declares_tap_anywhere_region_and_crop() -> None:
    module_dir = TROOPS_DIR / "infantry"
    area = _load_yaml(module_dir / "area.yaml")
    popup_screen = next(
        screen
        for screen in area["screens"]
        if screen.get("ocr") == "references/tap_anywhere.png"
    )
    regions = {region["name"]: region for region in popup_screen["regions"]}
    region = regions["infantry.tap_anywhere"]
    assert region["action"] == "click"

    frame = cv2.imread(
        str(module_dir / "references" / "tap_anywhere.png"),
        cv2.IMREAD_COLOR,
    )
    assert frame is not None
    crop = exported_crop_png(
        repo_root(),
        "games/wos/troops/infantry/references/tap_anywhere.png",
        "infantry.tap_anywhere",
    )
    assert crop.is_file(), f"missing crop: {crop}"
    template = cv2.imread(str(crop), cv2.IMREAD_COLOR)
    assert template is not None
    result = match_crop_1to1_at_bbox_percent(frame, template, region["bbox"])
    assert result["score"] >= 0.99


def test_train_screen_declares_troop_switch_tabs_and_crops() -> None:
    module_dir = TROOPS_DIR / "infantry"
    area = _load_yaml(module_dir / "area.yaml")
    train_screen = next(
        screen for screen in area["screens"] if screen.get("ocr") == "references/train.png"
    )
    regions = {region["name"]: region for region in train_screen["regions"]}
    frame = cv2.imread(str(module_dir / "references" / "train.png"), cv2.IMREAD_COLOR)
    assert frame is not None

    for troop_type in TROOP_TYPES:
        region_name = f"train.to.{troop_type}"
        region = regions[region_name]
        assert region["action"] == "click"
        assert region["bbox"]["original_width"] == 720
        assert region["bbox"]["original_height"] == 1280

        crop = exported_crop_png(
            repo_root(),
            "games/wos/troops/infantry/references/train.png",
            region_name,
        )
        assert crop.is_file(), f"missing crop: {crop}"
        template = cv2.imread(str(crop), cv2.IMREAD_COLOR)
        assert template is not None
        result = match_crop_1to1_at_bbox_percent(frame, template, region["bbox"])
        assert result["score"] >= 0.99


def test_troop_analyzer_keeps_upgrade_inside_training_scenario() -> None:
    module_dir = TROOPS_DIR / "infantry"
    analyze = _load_yaml(module_dir / "analyze" / "analyze.yaml")
    rules = {rule["name"]: rule for rule in analyze["overlay"]}

    assert "train.upgrade.visible" not in rules
    assert not (module_dir / "scenarios" / "troops.upgrade.yaml").exists()


@pytest.mark.parametrize(
    ("reference", "region_name"),
    [
        ("train.png", "train.upgrade"),
        ("train.png", "troops.train.start"),
        ("upgrade.png", "troops.upgrade.start"),
        ("promotion.png", "troops.promotion"),
    ],
)
def test_troop_upgrade_regions_are_clickable_and_match_crops(
    reference: str,
    region_name: str,
) -> None:
    module_dir = TROOPS_DIR / "infantry"
    area = _load_yaml(module_dir / "area.yaml")
    screen = next(
        screen for screen in area["screens"] if screen.get("ocr") == f"references/{reference}"
    )
    regions = {region["name"]: region for region in screen["regions"]}
    region = regions[region_name]
    assert region["action"] == "click"

    frame = cv2.imread(str(module_dir / "references" / reference), cv2.IMREAD_COLOR)
    assert frame is not None
    crop = exported_crop_png(
        repo_root(),
        f"games/wos/troops/infantry/references/{reference}",
        region_name,
    )
    assert crop.is_file(), f"missing crop: {crop}"
    template = cv2.imread(str(crop), cv2.IMREAD_COLOR)
    assert template is not None
    result = match_crop_1to1_at_bbox_percent(frame, template, region["bbox"])
    assert result["score"] >= 0.99


def test_screen_graph_exposes_troop_routes() -> None:
    bind_active_game("wos")
    screen_graph.invalidate_edge_taps_cache()
    screen_graph.invalidate_screen_verify_config()

    static, _dynamic, _graph = screen_graph.graph_for_game("wos")
    for troop_type in TROOP_TYPES:
        assert static[("main_menu", troop_type)] == [f"main_menu.to.{troop_type}"]
        assert static[(troop_type, "main_menu")] == ["icon.page.back"]
        for target_type in TROOP_TYPES:
            if target_type == troop_type:
                continue
            assert static[(troop_type, target_type)] == [f"train.to.{target_type}"]
            assert screen_graph.route_taps(troop_type, target_type) == [
                [f"train.to.{target_type}"]
            ]
        assert screen_graph.route_taps("main_menu", troop_type) == [
            [f"main_menu.to.{troop_type}"]
        ]
        assert screen_graph.route_taps(troop_type, "main_city") == [
            ["icon.page.back"],
            ["icon.page.back"],
        ]
