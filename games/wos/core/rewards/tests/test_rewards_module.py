from __future__ import annotations

import asyncio
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from analysis.overlay import load_merged_analyze_yaml, run_overlay_analysis
from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc
from layout.reward_ribbon_detector import detect_reward_ribbon_in_bbox_percent
from navigation.detector import ScreenDetector
from services import get_ocr_client

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]


def _load_reference(name: str):
    frame = cv2.imread(str(MODULE_DIR / "references" / name), cv2.IMREAD_COLOR)
    assert frame is not None, name
    return frame


@pytest.fixture(scope="module")
def area_doc() -> dict:
    return load_area_doc(REPO_ROOT, game="wos")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reference", "expected_screen"),
    [
        ("page.rewards.png", "rewards"),
        ("page.rewards_popup.png", "rewards"),
        ("page.rewards_upgraded.png", "rewards.upgraded"),
        ("page.claimed.png", "rewards"),
        ("chapter_rewards.png", "rewards"),
    ],
)
async def test_rewards_references_detect_expected_screen(
    reference: str,
    expected_screen: str,
) -> None:
    detector = ScreenDetector(get_ocr_client())

    assert await detector.detect_screen(_load_reference(reference)) == expected_screen


def test_reward_ribbon_detector_separates_blue_orange_and_white_popup(
    area_doc: dict,
) -> None:
    rules = [
        {
            "name": "ribbon.blue",
            "action": "reward_ribbon",
            "region": "rewards.ribbon",
            "type": "blue",
            "threshold": 0.35,
        },
        {
            "name": "ribbon.orange",
            "action": "reward_ribbon",
            "region": "rewards.ribbon",
            "type": "orange",
            "threshold": 0.35,
        },
    ]

    blue = asyncio.run(
        evaluate_overlay_rules_async(
            _load_reference("page.claimed.png"),
            area_doc,
            REPO_ROOT,
            rules,
            state_flat={},
        )
    )
    assert blue["ribbon.blue"]["matched"] is True
    assert blue["ribbon.orange"]["matched"] is False

    orange = asyncio.run(
        evaluate_overlay_rules_async(
            _load_reference("page.rewards_upgraded.png"),
            area_doc,
            REPO_ROOT,
            rules,
            state_flat={},
        )
    )
    assert orange["ribbon.blue"]["matched"] is False
    assert orange["ribbon.orange"]["matched"] is True

    white = asyncio.run(
        evaluate_overlay_rules_async(
            _load_reference("page.rewards_popup.png"),
            area_doc,
            REPO_ROOT,
            rules,
            state_flat={},
        )
    )
    assert white["ribbon.blue"]["matched"] is False
    assert white["ribbon.orange"]["matched"] is False


def test_reward_ribbon_detector_rejects_top_aligned_onboarding_band() -> None:
    img = np.zeros((1280, 720, 3), dtype=np.uint8)
    y = int(0.14 * img.shape[0])
    h = int(0.18 * img.shape[0])
    img[y : y + h, 74:646] = (0, 180, 255)

    loose = detect_reward_ribbon_in_bbox_percent(
        img,
        {"x": 0, "y": 14, "width": 100, "height": 18},
        kind="orange",
        min_component_height_ratio=0.7,
    )
    strict = detect_reward_ribbon_in_bbox_percent(
        img,
        {"x": 0, "y": 14, "width": 100, "height": 18},
        kind="orange",
        min_component_y_ratio=0.04,
        min_component_height_ratio=0.7,
    )

    assert loose.present is True
    assert loose.component_y_ratio == 0.0
    assert strict.present is False


def test_rewards_analyzer_pushes_exit_scenarios(area_doc: dict) -> None:
    cfg = load_merged_analyze_yaml(REPO_ROOT, module_scope="rewards")
    rules = {r["name"]: r for r in cfg["overlay"]}
    assert rules["rewards.ribbon.visible"]["device_level"] is True
    assert "screens" not in rules["rewards.ribbon.visible"]
    assert rules["rewards.ribbon.visible"]["steps"] == [
        {"push_scenario": {"name": "exit_rewards_popup", "ttl": "30s"}}
    ]
    assert rules["rewards.upgraded.ribbon.visible"]["device_level"] is True
    assert "screens" not in rules["rewards.upgraded.ribbon.visible"]
    assert rules["rewards.upgraded.ribbon.visible"]["steps"] == [
        {"push_scenario": {"name": "exit_rewards_upgraded_popup", "ttl": "30s"}}
    ]

    claimed_out = asyncio.run(
        run_overlay_analysis(
            _load_reference("chapter_rewards.png"),
            repo_root=REPO_ROOT,
            area_doc=area_doc,
            current_screen="onboarding",
            device_level_only=True,
            module_scope="rewards",
        )
    )
    assert claimed_out["rewards.ribbon.visible"]["matched"] is True
    assert claimed_out["rewards.ribbon.visible"]["pushScenario"] == [
        {
            "type": "exit_rewards_popup",
            "priority": None,
            "ttl": 30,
            "dsl_scenario": None,
        }
    ]

    upgraded_out = asyncio.run(
        run_overlay_analysis(
            _load_reference("page.rewards_upgraded.png"),
            repo_root=REPO_ROOT,
            area_doc=area_doc,
            current_screen="onboarding",
            device_level_only=True,
            module_scope="rewards",
        )
    )
    assert upgraded_out["rewards.ribbon.visible"]["matched"] is False
    assert upgraded_out["rewards.upgraded.ribbon.visible"]["matched"] is True
    assert upgraded_out["rewards.upgraded.ribbon.visible"]["pushScenario"] == [
        {
            "type": "exit_rewards_upgraded_popup",
            "priority": None,
            "ttl": 30,
            "dsl_scenario": None,
        }
    ]


def test_rewards_exit_scenarios_are_device_level() -> None:
    for rel in (
        "scenarios/exit_rewards_popup.yaml",
        "scenarios/exit_rewards_upgraded_popup.yaml",
        "scenarios/exit_claimed_popup.yaml",
    ):
        doc = yaml.safe_load((MODULE_DIR / rel).read_text(encoding="utf-8"))
        assert doc["device_level"] is True


def test_claim_online_rewards_scaffold_ships_disabled() -> None:
    """The main_menu panel dispatch targets claim_online_rewards for the My
    Rewards · Online Rewards row; it ships disabled (self-gate keeps it dormant)
    until the claim flow is labeled on-device."""
    doc = yaml.safe_load(
        (MODULE_DIR / "scenarios/claim_online_rewards.yaml").read_text(encoding="utf-8")
    )
    assert doc["enabled"] is False
    assert doc["steps"][0]["exec"] == "tap_main_menu_panel_row"
