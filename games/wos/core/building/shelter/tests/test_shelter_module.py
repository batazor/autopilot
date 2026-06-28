from __future__ import annotations

import asyncio
from pathlib import Path

import cv2
import pytest
import yaml

from analysis.overlay import load_merged_analyze_yaml, run_overlay_analysis
from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc
from navigation.detector import ScreenDetector
from navigation.screen_graph import route_taps, screen_verify_rules
from services import get_ocr_client

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[4]


def _load_reference(name: str):
    frame = cv2.imread(str(MODULE_DIR / "references" / name), cv2.IMREAD_COLOR)
    assert frame is not None, name
    return frame


def test_shelter_title_and_next_match_reference() -> None:
    area_doc = load_area_doc(REPO_ROOT, game="wos")
    rules = [
        {
            "name": "shelter.title",
            "action": "text",
            "region": "shelter.title",
            "expected": ["Shelter"],
            "exact": True,
            "threshold": 0.8,
        },
        {
            "name": "shelter.next",
            "action": "cta_button",
            "color": "blue",
            "region": "shelter.next",
            "threshold": 0.5,
        },
    ]

    out = asyncio.run(
        evaluate_overlay_rules_async(
            _load_reference("main.png"),
            area_doc,
            REPO_ROOT,
            rules,
            state_flat={},
            ocr_client=get_ocr_client(),
        )
    )

    assert out["shelter.title"]["matched"] is True
    assert out["shelter.next"]["matched"] is True


def test_shelter_routes_and_verify_rule() -> None:
    assert route_taps("main_city", "shelter") == [["chapter.task"]]
    assert route_taps("shelter", "main_city") == [["from.building.to.main_city"]]
    assert screen_verify_rules("shelter") == [
        {"ocr": "shelter.title", "contains": ["Shelter", "Барак"], "threshold": 0.8}
    ]


def test_shelter_analyzer_pushes_upgrade_scenario() -> None:
    cfg = load_merged_analyze_yaml(REPO_ROOT)
    rule = next(r for r in cfg["overlay"] if r.get("name") == "shelter.next.visible")
    assert rule["region"] == "shelter.next"
    assert rule["screens"] == ["shelter"]
    assert rule["steps"] == [{"push_scenario": {"name": "shelter.upgrade", "ttl": "30s"}}]

    out = asyncio.run(
        run_overlay_analysis(
            _load_reference("main.png"),
            repo_root=REPO_ROOT,
            area_doc=load_area_doc(REPO_ROOT, game="wos"),
            current_screen="shelter",
        )
    )

    assert out["shelter.next.visible"]["matched"] is True
    assert out["shelter.next.visible"]["pushScenario"] == [
        {"type": "shelter.upgrade", "priority": None, "ttl": 30, "dsl_scenario": None}
    ]


def test_shelter_upgrade_scenario_wires_title_sync_and_upgrade_loop() -> None:
    doc = yaml.safe_load(
        (MODULE_DIR / "scenarios" / "shelter.upgrade.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert doc["node"] == "shelter"
    assert doc["device_level"] is True
    assert doc["steps"][:3] == [
        # threshold 0.4: RU «Барак … Ур. N» OCRs ~0.73; the 0.8 floor dropped it.
        {"ocr": "shelter.title", "store": "building.name", "threshold": 0.4},
        {"exec": "sync_building_name"},
        {
            "while_match": "shelter.next",
            "action": "cta_button",
            "color": "blue",
            "threshold": 0.5,
            "max": 1,
            "steps": [{"click": "shelter.next"}, {"wait": "2s"}],
        },
    ]
    loop = doc["steps"][3]["loop"]["steps"]
    for step in loop:
        assert step["action"] == "cta_button"
        assert step["color"] == "blue"
        assert step["threshold"] == 0.5
    # Re-probes BOTH alternating «Улучшить» pills each iteration (building + furniture)
    # so the blue button is re-searched between taps, plus the confirm-dialog button.
    probed = [s.get("while_match") for s in loop]
    assert "upgrade_button_top" in probed and "upgrade_button" in probed
    assert all(s.get("max") == 1 for s in loop), "max:1 = one tap per re-probe"


@pytest.mark.asyncio
async def test_shelter_reference_detects_shelter_screen() -> None:
    detector = ScreenDetector(get_ocr_client())

    assert await detector.detect_screen(_load_reference("main.png")) == "shelter"
