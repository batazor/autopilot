"""Tundra Trek module wiring."""
from __future__ import annotations

from pathlib import Path

import cv2
import pytest
import yaml

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc
from navigation import screen_graph, template_icon_resolver  # noqa: F401

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]
TEMPLATE = (
    "games/wos/events/tundra_trek/references/crop/main_city_main_city.to.tundra_trek.png"
)
WORLD_TEMPLATE = (
    "games/wos/events/tundra_trek/references/crop/main_city_main_world.to.tundra_trek.png"
)


def _load_yaml(rel: str) -> dict:
    path = MODULE_DIR / rel
    assert path.exists(), f"missing: {path}"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_tundra_trek_manifest_declares_working_module() -> None:
    manifest = _load_yaml("module.yaml")

    assert manifest["id"] == "tundra_trek"
    assert manifest["title"] == "Tundra Trek"
    assert manifest["scenarios"] == "scenarios"
    assert manifest["analyze"] == "analyze/analyze.yaml"
    assert manifest["area"] == "area.yaml"
    assert manifest["references"] == "references"


def test_tundra_trek_declares_icon_entry_regions() -> None:
    area = _load_yaml("area.yaml")
    regions = {
        region["name"]: region
        for screen in area["screens"]
        for region in screen.get("regions", [])
    }

    assert regions["main_city.to.tundra_trek"]["action"] == "exist"
    assert regions["main_world.to.tundra_trek"]["action"] == "exist"
    assert regions["main_city.tundra_trek.icon_search"]["overlay_auxiliary"] is True
    assert regions["main_world.tundra_trek.icon_search"]["overlay_auxiliary"] is True
    assert regions["main_city.tundra_trek.icon_search"]["bbox"] == {
        "x": 0,
        "y": 43.75,
        "width": 100,
        "height": 56.25,
        "rotation": 0,
        "original_width": 720,
        "original_height": 1280,
    }
    assert regions["tundra_trek.title"]["action"] == "click"
    # next_stop + fight are template-matched buttons the scenario probes/clicks.
    assert regions["tundra_trek.next_stop"]["action"] == "exist"
    assert regions["tundra_trek.fight"]["action"] == "exist"


def test_tundra_trek_analyzer_uses_icon_and_main_menu_state() -> None:
    analyze = _load_yaml("analyze/analyze.yaml")
    rules = {rule["name"]: rule for rule in analyze["overlay"]}
    city_rule = rules["tundra_trek.main_city.icon.visible"]
    world_rule = rules["tundra_trek.main_world.icon.visible"]
    menu_rule = rules["tundra_trek.main_menu.claimable"]

    assert city_rule["region"] == "main_city.to.tundra_trek"
    assert city_rule["template"] == TEMPLATE
    assert city_rule["search_region"] == "main_city.tundra_trek.icon_search"
    assert city_rule["screens"] == ["main_city"]
    assert world_rule["region"] == "main_world.to.tundra_trek"
    assert world_rule["template"] == WORLD_TEMPLATE
    assert world_rule["search_region"] == "main_world.tundra_trek.icon_search"
    assert world_rule["screens"] == ["main_world"]
    assert menu_rule["region"] == "main_menu.city"
    assert menu_rule["screens"] == ["main_menu"]
    assert menu_rule["cond"] == "main_menu.panel.trek.tundra_trek.isClaimable == true"
    assert {"push_scenario": {"name": "event.tundra_trek", "ttl": "1m"}} in menu_rule[
        "steps"
    ]


def test_tundra_trek_scenario_invokes_fsm() -> None:
    scenario = _load_yaml("scenarios/event.tundra_trek.yaml")

    assert scenario["enabled"] is True
    assert scenario["node"] == "event.tundra_trek"
    # player-bound scenario: no redundant active_player guard
    assert "cond" not in scenario

    # The multi-state mini-game is driven by the Python FSM, not a YAML loop:
    # the scenario navigates to the node, then hands off to drive_tundra_trek.
    assert {"exec": "drive_tundra_trek"} in scenario["steps"]


def test_tundra_trek_fsm_handler_is_registered() -> None:
    from games.wos.events.tundra_trek import exec as tt_exec

    assert "drive_tundra_trek" in tt_exec.DSL_EXEC_HANDLERS
    assert callable(tt_exec.DSL_EXEC_HANDLERS["drive_tundra_trek"])


def test_tundra_trek_screen_verify_uses_title() -> None:
    screen_graph.load_screen_verify_config.cache_clear()
    try:
        expected = [{"ocr": "tundra_trek.title", "contains": "Tundra"}]
        assert screen_graph.screen_verify_rules("event.tundra_trek") == expected
        assert screen_graph.screen_landmark_rules("event.tundra_trek") == expected
    finally:
        screen_graph.load_screen_verify_config.cache_clear()


@pytest.mark.asyncio
async def test_tundra_trek_icon_matches_inside_lower_search_region() -> None:
    frame = cv2.imread(str(MODULE_DIR / "references" / "main_city.png"))
    assert frame is not None
    rule = {
        "name": "tundra_trek.main_city.icon.visible",
        "region": "main_city.to.tundra_trek",
        "action": "findIcon",
        "template": TEMPLATE,
        "search_region": "main_city.tundra_trek.icon_search",
        "threshold": 0.9,
    }

    out = await evaluate_overlay_rules_async(
        frame,
        load_area_doc(REPO_ROOT),
        REPO_ROOT,
        [rule],
        current_screen="main_city",
    )

    hit = out[rule["name"]]
    assert hit["matched"] is True
    assert hit["search_region"] == "main_city.tundra_trek.icon_search"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "region", "search_region", "template"),
    [
        (
            "main_city",
            "main_city.to.tundra_trek",
            "main_city.tundra_trek.icon_search",
            TEMPLATE,
        ),
        (
            "main_world",
            "main_world.to.tundra_trek",
            "main_world.tundra_trek.icon_search",
            WORLD_TEMPLATE,
        ),
    ],
)
async def test_tundra_trek_routes_from_city_and_world(
    source: str,
    region: str,
    search_region: str,
    template: str,
) -> None:
    screen_graph.invalidate_edge_taps_cache()
    try:
        hops = await screen_graph.route_hops_async(
            source,
            "event.tundra_trek",
            instance_id="bs1",
            redis_client=None,
            game="wos",
        )
        assert hops == [
            (
                "event.tundra_trek",
                [
                    {
                        "type": "template_icon",
                        "region": region,
                        "template": template,
                        "search_region": search_region,
                        "threshold": 0.9,
                    }
                ],
            )
        ]
    finally:
        screen_graph.invalidate_edge_taps_cache()


def test_fsm_button_detector_finds_next_stop_on_idle_frame() -> None:
    """The FSM's colour finder must locate the orange Next Stop pill on the real
    stop frame — otherwise the trek can never drive. Guards the detector gates
    (`_BTN_MIN_H`/`_BTN_MIN_W`/saturation) against regressions."""
    from games.wos.events.tundra_trek import exec as tt_exec

    frame = cv2.imread(str(MODULE_DIR / "references" / "idle.png"))
    assert frame is not None
    btn = tt_exec._find_button(frame)
    assert btn is not None, "Next Stop pill not detected on idle.png"
    cx, cy, colour = btn
    assert colour == "orange"
    assert 0.4 < cx < 0.6      # horizontally centred
    assert 0.85 < cy < 0.98    # near the bottom (the button row)


def test_fsm_has_button_stuck_guard() -> None:
    """A non-advancing button tap must not spin the whole step budget (the bug
    that burned exactly 300 s/run)."""
    from games.wos.events.tundra_trek import exec as tt_exec

    assert getattr(tt_exec, "_MAX_BTN_STUCK", 0) >= 1


def test_next_stop_loop_is_bounded() -> None:
    """The declarative Next Stop loop must carry a `max` so a tap that fails to
    advance can't spin until the task timeout."""
    scenario = _load_yaml("scenarios/event.tundra_trek.yaml")
    loops = [
        s
        for s in scenario["steps"]
        if s.get("while_match") == "tundra_trek.next_stop"
    ]
    assert loops, "next_stop drive loop missing"
    assert all(isinstance(s.get("max"), int) and s["max"] >= 1 for s in loops)


def test_continue_rule_repushes_trek_from_its_own_screens() -> None:
    """When parked on the trek hub / stop sub-screen with Next Stop visible, an
    overlay rule must re-push event.tundra_trek so the bot resumes instead of
    idling (the entry rules only fire on main_city/world/menu)."""
    analyze = _load_yaml("analyze/analyze.yaml")
    rules = {rule["name"]: rule for rule in analyze["overlay"]}
    cont = rules["tundra_trek.stop.continue"]

    assert cont["region"] == "tundra_trek.next_stop"
    assert cont["action"] == "findIcon"
    assert set(cont["screens"]) == {"event.tundra_trek", "event.tundra_trek.stop"}
    assert {"push_scenario": {"name": "event.tundra_trek", "ttl": "1m"}} in cont["steps"]
    assert cont.get("ttl") == "1m"

    # the battle sub-screen resumes the trek too
    fight = rules["tundra_trek.fight.continue"]
    assert fight["region"] == "tundra_trek.fight"
    assert fight["screens"] == ["event.tundra_trek.stop"]
    assert {"push_scenario": {"name": "event.tundra_trek", "ttl": "1m"}} in fight["steps"]
