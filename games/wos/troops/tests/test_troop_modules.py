"""Structural checks for troop training screen modules.

The shapes below were validated on a live device walk (lancer + marksman):
collect bubble → options ring → Train dialog → upgrade/promotion branch →
training countdown pill. Reference screenshots under each module's
``references/`` come from that walk.
"""
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


def test_only_infantry_has_bubble_overlay_rule() -> None:
    """The lancer/marksman ready-bubbles are covered by the main_menu panel
    scanner (animated hand hint makes a findIcon template unreliable)."""
    infantry = _load_yaml(TROOPS_DIR / "infantry" / "analyze" / "analyze.yaml")
    rules = {rule["name"]: rule for rule in infantry["overlay"]}
    rule = rules["infantry.complete.visible"]
    assert rule["region"] == "infantry.complete"
    assert {"push_scenario": {"name": "troops.infantry.train", "ttl": "1m"}} in rule[
        "steps"
    ]

    for troop_type in ("lancer", "marksman"):
        analyze = _load_yaml(TROOPS_DIR / troop_type / "analyze" / "analyze.yaml")
        assert analyze["overlay"] == []


@pytest.mark.parametrize("troop_type", TROOP_TYPES)
def test_troop_training_scenario_shape(troop_type: str) -> None:
    """Collect-or-options double tap → Train → unlock splash guard →
    promotion-first branch → conveyor re-push from the countdown pill."""
    module_dir = TROOPS_DIR / troop_type
    scenario = _load_yaml(
        module_dir / "scenarios" / f"troops.{troop_type}.train.yaml"
    )

    assert scenario["enabled"] is True
    assert scenario["node"] == troop_type
    assert scenario["cond"] == "active_player != null"
    assert scenario["steps"] == [
        {"click": f"{troop_type}.complete"},
        {"wait": "1s"},
        {
            "while_match": "troops.options.train",
            "max": 1,
            "steps": [
                {"click": "troops.options.train"},
                {"wait": "1.5s"},
            ],
            "else": [
                {"click": f"{troop_type}.complete"},
                {"wait": "1s"},
                {"click": "troops.options.train"},
                {"wait": "1.5s"},
            ],
        },
        {
            "while_match": "troops.tap_anywhere",
            "max": 1,
            "steps": [
                {"click": "troops.tap_anywhere"},
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
                {"wait": "1.5s"},
            ],
            "else": [
                {"click": "troops.train.start"},
                {"wait": "1.5s"},
            ],
        },
        {"ocr": "troops.train.timer", "event_timer": f"troops.{troop_type}.training"},
        {
            "push_scenario": {
                "name": f"troops.{troop_type}.train",
                "delay": f"troops.{troop_type}.training + 5m",
            }
        },
    ]


@pytest.mark.parametrize("troop_type", TROOP_TYPES)
def test_accept_troops_scenario_chains_into_training(troop_type: str) -> None:
    scenario = _load_yaml(
        Path(repo_root())
        / "games"
        / "wos"
        / "core"
        / "main_menu"
        / "scenarios"
        / f"accept_troops_{troop_type}.yaml"
    )
    assert scenario["steps"][0] == {"exec": "tap_training_accept", "troop": troop_type}
    assert scenario["steps"][-1] == {"push_scenario": f"troops.{troop_type}.train"}


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
        region_name = f"troops.options.{action}"
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


def test_new_troop_popup_declares_tap_anywhere_region_and_crop() -> None:
    module_dir = TROOPS_DIR / "infantry"
    area = _load_yaml(module_dir / "area.yaml")
    popup_screen = next(
        screen
        for screen in area["screens"]
        if screen.get("ocr") == "references/tap_anywhere.png"
    )
    regions = {region["name"]: region for region in popup_screen["regions"]}
    region = regions["troops.tap_anywhere"]
    assert region["action"] == "click"

    frame = cv2.imread(
        str(module_dir / "references" / "tap_anywhere.png"),
        cv2.IMREAD_COLOR,
    )
    assert frame is not None
    crop = exported_crop_png(
        repo_root(),
        "games/wos/troops/infantry/references/tap_anywhere.png",
        "troops.tap_anywhere",
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reference", "expected_seconds"),
    [
        ("lancer/references/training_in_progress.png", 9 * 3600 + 29 * 60 + 38),
        ("marksman/references/promoting_in_progress.png", 81),
    ],
)
async def test_train_timer_region_reads_countdown_pill(
    reference: str, expected_seconds: int
) -> None:
    """One region covers both pill variants (Training row sits ~14px above
    Promoting); the trailing detail line never breaks the hms parse."""
    from layout.types import Region
    from services import get_ocr_client
    from tasks.dsl_scenario_helpers import _parse_hms_to_seconds

    area = _load_yaml(TROOPS_DIR / "infantry" / "area.yaml")
    region = next(
        r
        for s in area["screens"]
        for r in s.get("regions", [])
        if r["name"] == "troops.train.timer"
    )
    b = region["bbox"]
    frame = cv2.imread(str(TROOPS_DIR / reference))
    assert frame is not None
    h, w = frame.shape[:2]
    res = await get_ocr_client().ocr_region(
        frame,
        Region(
            int(b["x"] / 100 * w),
            int(b["y"] / 100 * h),
            int(b["width"] / 100 * w),
            int(b["height"] / 100 * h),
        ),
    )
    assert _parse_hms_to_seconds(res.text) == expected_seconds


def test_screen_graph_exposes_troop_nodes() -> None:
    bind_active_game("wos")
    screen_graph.invalidate_edge_taps_cache()
    screen_graph.invalidate_screen_verify_config()

    static, _dynamic, _graph = screen_graph.graph_for_game("wos")
    for troop_type in TROOP_TYPES:
        assert static[("main_menu", troop_type)] == [f"main_menu.to.{troop_type}"]
        assert static[(troop_type, "main_menu")] == ["icon.page.back"]


# --- Live-walk guard regression ----------------------------------------------
# Every frame below was captured on a real device while walking the accept /
# train flow. Each row asserts the scenario guard fires exactly when it should.

_GUARD_CASES = [
    # (module-relative frame, region, should_match)
    ("lancer/references/main.png", "troops.options.train", False),
    ("lancer/references/options.png", "troops.options.train", True),
    ("infantry/references/options.png", "troops.options.train", True),
    ("lancer/references/tap_anywhere.png", "troops.tap_anywhere", True),
    ("marksman/references/tap_anywhere.png", "troops.tap_anywhere", True),
    ("lancer/references/train.png", "troops.tap_anywhere", False),
    ("lancer/references/train.png", "train.upgrade", True),
    ("marksman/references/train.png", "train.upgrade", True),
    ("lancer/references/training_in_progress.png", "train.upgrade", False),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("frame_rel", "region", "should_match"), _GUARD_CASES)
async def test_training_scenario_guards_on_live_frames(
    frame_rel: str, region: str, should_match: bool
) -> None:
    from analysis.overlay_engine import evaluate_overlay_rules_async
    from layout.area_manifest import load_area_doc

    root = Path(repo_root())
    frame = cv2.imread(str(TROOPS_DIR / frame_rel))
    assert frame is not None, frame_rel
    area = load_area_doc(root, game="wos")
    rule = {"name": "probe", "action": "findIcon", "region": region, "threshold": 0.9}
    out = await evaluate_overlay_rules_async(frame, area, root, [rule], state_flat={})
    assert bool(out["probe"].get("matched")) is should_match, (
        frame_rel,
        region,
        out["probe"].get("score"),
    )
