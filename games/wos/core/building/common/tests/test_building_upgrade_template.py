"""Coverage for the ``building`` template axis + ``building.upgrade_{building}``.

The targeted upgrade scenario is bound to one concrete building node via the
``building`` axis in ``dsl/template_resolver.py``; the captured id is validated
against ``games/wos/db/buildings/index.yaml`` (the same file the navigation
screen graph generates per-building nodes from).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import cv2
import yaml

from analysis.overlay_engine import evaluate_overlay_rules_async
from dsl import template_resolver as _tmpl
from layout.area_manifest import load_area_doc

REPO_ROOT = Path(__file__).resolve().parents[6]
COMMON_DIR = Path(__file__).resolve().parents[1]


def test_resolves_known_building() -> None:
    resolved = _tmpl.resolve(REPO_ROOT, "building.upgrade_furnace")
    assert resolved is not None
    assert resolved.path.name == "building.upgrade_{building}.yaml"
    assert resolved.context == {"building": "furnace", "building_name": "Furnace"}


def test_resolves_multiword_building() -> None:
    """Underscored ids (``fire_crystal_furnace``) capture as one axis value."""
    resolved = _tmpl.resolve(REPO_ROOT, "building.upgrade_fire_crystal_furnace")
    assert resolved is not None
    assert resolved.context["building"] == "fire_crystal_furnace"


def test_rejects_unknown_building() -> None:
    """An id absent from ``db/buildings/index.yaml`` is not a valid fill."""
    assert _tmpl.resolve(REPO_ROOT, "building.upgrade_not_a_building") is None


def test_literal_building_upgrade_is_untouched() -> None:
    """The bare ``building.upgrade`` literal still wins (no axis suffix)."""
    resolved = _tmpl.resolve(REPO_ROOT, "building.upgrade")
    assert resolved is not None
    assert resolved.path.name == "building.upgrade.yaml"
    assert resolved.context == {}


def test_load_doc_renders_node_and_name() -> None:
    loaded = _tmpl.load_doc(REPO_ROOT, "building.upgrade_cookhouse")
    assert loaded is not None
    _path, doc = loaded
    assert doc["node"] == "cookhouse"
    assert doc["navigate"] is False
    assert "Cookhouse" in doc["name"]
    # Body shares the canonical upgrade loop (upgrade → next → build → big).
    loop = doc["steps"][-2]["loop"]["steps"]
    assert [s["while_match"] for s in loop] == [
        "upgrade_button",
        "button.next",
        "build_building_item",
        "upgrade_big_button",
    ]
    for step in loop:
        assert step["action"] == "cta_button"
        assert step["color"] == "blue"
        assert step["threshold"] == 0.5


def test_building_upgrade_scenarios_use_blue_button_mask() -> None:
    for rel in (
        "scenarios/building.upgrade.yaml",
        "scenarios/building.upgrade_{building}.yaml",
    ):
        doc = yaml.safe_load((COMMON_DIR / rel).read_text())
        loop = next(step["loop"]["steps"] for step in doc["steps"] if "loop" in step)
        masked = {
            step["while_match"]: step
            for step in loop
            if step.get("while_match")
            in {"upgrade_button", "button.next", "build_building_item", "upgrade_big_button"}
        }
        assert set(masked) == {
            "upgrade_button",
            "button.next",
            "build_building_item",
            "upgrade_big_button",
        }
        for step in masked.values():
            assert step["action"] == "cta_button"
            assert step["color"] == "blue"
            assert step["threshold"] == 0.5


def test_main_menu_building_queue_scenarios_use_blue_button_mask() -> None:
    for rel in (
        "../../main_menu/scenarios/building_queue_1_empty.yaml",
        "../../main_menu/scenarios/building_queue_2_empty.yaml",
    ):
        doc = yaml.safe_load((COMMON_DIR / rel).read_text())
        masked = {
            step["while_match"]: step
            for step in doc["steps"]
            if step.get("while_match") in {"upgrade_button", "upgrade_blue_button"}
        }
        assert set(masked) == {"upgrade_button", "upgrade_blue_button"}
        for step in masked.values():
            assert step["action"] == "cta_button"
            assert step["color"] == "blue"
            assert step["threshold"] == 0.5
            assert "min_match_saturation" not in step


def _load_reference_bgr(rel: str):
    frame = cv2.imread(str(REPO_ROOT / rel))
    assert frame is not None, rel
    return frame


def _eval_single_rule(region: str, reference: str) -> dict:
    area_doc = load_area_doc(REPO_ROOT, game="wos")
    rule = {
        "name": f"test.{region}",
        "region": region,
        "action": "cta_button",
        "color": "blue",
        "threshold": 0.5,
    }
    out = asyncio.run(
        evaluate_overlay_rules_async(
            _load_reference_bgr(reference),
            area_doc,
            REPO_ROOT,
            [rule],
            state_flat={},
        )
    )
    return out[rule["name"]]


def test_blue_button_mask_matches_building_reference_ctas() -> None:
    cases = [
        (
            "upgrade_button",
            "games/wos/core/building/common/references/upgrade_button.png",
            [464, 474],
        ),
        (
            "button.next",
            "games/wos/core/common/references/button.next.png",
            [466, 573],
        ),
        (
            "build_building_item",
            "games/wos/core/building/common/references/build_building_item.png",
            [466, 595],
        ),
        (
            "build_button",
            "games/wos/core/building/common/references/build_button.png",
            [227, 1135],
        ),
        (
            "upgrade_big_button",
            "games/wos/core/building/common/references/upgrade_big_button.png",
            [214, 1166],
        ),
        (
            "upgrade_building",
            "games/wos/core/building/common/references/upgrade_building.png",
            [470, 594],
        ),
        (
            "upgrade_blue_button",
            "games/wos/core/building/common/references/building.lancer_camp.upgrade_dialog.png",
            [378, 999],
        ),
        (
            "shelter.next",
            "games/wos/core/building/shelter/references/main.png",
            [487, 560],
        ),
    ]
    for region, reference, top_left in cases:
        row = _eval_single_rule(region, reference)
        assert row["matched"] is True, region
        assert row["action"] == "cta_button"
        assert row["color"] == "blue"
        assert row["detector_action"] == "blue_button"
        assert row["top_left"] == top_left
        assert row["score"] >= 0.5
        assert row["candidate_count"] >= 1
        assert row["excluded_count"] == 0
        assert row["min_fill_ratio"] == 0.3


def test_building_cta_overlay_rules_are_screen_gated() -> None:
    """The blue-CTA mask is permissive, so the building overlay rules that push
    build/upgrade scenarios MUST be gated to building screens — otherwise a large
    blue blob on an unrelated screen (the mail screen's bottom-center button)
    false-matches the bottom-center anchor and self-pushes a building scenario
    (the original bs3 ``tap_upgrade_big_button`` on the mail screen bug).
    """
    from analysis.overlay_compile import compile_overlay_rule

    doc = yaml.safe_load((COMMON_DIR / "analyze/analyze.yaml").read_text())
    rules = {r["name"]: r for r in doc["overlay"]}
    gated = (
        "upgrade_building.visible",
        "upgrade_big_button.visible",
        "build_button.visible",
    )
    for name in gated:
        rule = rules[name]
        screens = {s.lower() for s in rule.get("screens", [])}
        assert "building" in screens, name
        assert "mail" not in screens, name
        # ScreenGate enforces it: building screens pass, mail is blocked.
        compiled = compile_overlay_rule(rule)
        assert compiled is not None, name
        assert compiled.screen.allows("building") is True, name
        assert compiled.screen.allows("furnace") is True, name
        assert compiled.screen.allows("shelter") is True, name
        assert compiled.screen.allows("mail") is False, name


def test_iter_resolved_keys_expands_per_building() -> None:
    keys = _tmpl.iter_resolved_keys(REPO_ROOT)
    by_key = {rk.key: rk for rk in keys}
    assert "building.upgrade_furnace" in by_key
    assert "building.upgrade_sawmill" in by_key
    assert by_key["building.upgrade_furnace"].context == {
        "building": "furnace",
        "building_name": "Furnace",
    }
    # All building keys share the one template path.
    assert (
        by_key["building.upgrade_furnace"].path
        == by_key["building.upgrade_sawmill"].path
    )
