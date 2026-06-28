"""Structural + behavioural checks for the exit_confirm dialog node.

The exit-game confirm dialog («Подтверждение» / «Выйти из игры?») must be a real
screen node that the bot recognises and leaves by tapping «Отмена» — never the
«Подтвердить» button, which quits the game.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import yaml

from layout.area_manifest import load_area_doc
from layout.crop_paths import exported_crop_png
from layout.template_match import match_crop_1to1_at_bbox_percent
from navigation import screen_graph

MODULE_DIR = Path(__file__).resolve().parents[1]            # games/wos/core/exit_confirm
REPO_ROOT = Path(__file__).resolve().parents[5]
RU_MODULE_DIR = REPO_ROOT / "games" / "wos" / "ru" / "core" / "exit_confirm"
RU_CATALOG = "wos_ru"                                       # com.gof.globalru → wos_ru


def _load_yaml(path: Path) -> dict:
    assert path.exists(), f"missing: {path}"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _region(area: dict, name: str) -> dict:
    for screen in area["screens"]:
        for region in screen.get("regions", []):
            if region.get("name") == name:
                return region
    msg = f"region {name} not found"
    raise AssertionError(msg)


def test_module_manifests() -> None:
    base = _load_yaml(MODULE_DIR / "module.yaml")
    assert base["id"] == "exit_confirm"
    assert base["enabled"] is True
    overlay = _load_yaml(RU_MODULE_DIR / "module.yaml")
    assert overlay["id"] == "exit_confirm-ru-overlay"
    assert overlay["enabled"] is True


def test_screen_node_registered() -> None:
    assert "exit_confirm" in screen_graph.screen_verify_screen_names()


def test_detected_before_main_city_hub() -> None:
    # A modal over main_city must be probed before a main_city sticky hint is
    # confirmed. That happens iff its screen_verify priority is below the hub.
    verify = _load_yaml(MODULE_DIR / "routes" / "screen_verify.yaml")
    prio = verify["screens"]["exit_confirm"]["priority"]
    assert prio < screen_graph.MAIN_CITY_HUB_PRIORITY
    assert "exit_confirm" in screen_graph.screen_verify_modal_preempt_names()


def test_screen_verify_keys_on_unique_body_text() -> None:
    # Detection must key on the body landmark, not the shared «Подтверждение»
    # title (every confirm dialog wears it).
    verify = _load_yaml(MODULE_DIR / "routes" / "screen_verify.yaml")
    rules = verify["screens"]["exit_confirm"]["rules"]
    assert any(r.get("match") == "exit_confirm.body" for r in rules)


def test_edge_leaves_via_cancel_never_confirm() -> None:
    edges = _load_yaml(MODULE_DIR / "routes" / "edge_taps.yaml")["edges"]
    taps = edges["exit_confirm"]["main_city"]
    regions: set[str] = set()
    for tap in taps:
        if isinstance(tap, dict):
            regions.update(tap.get("regions", []))
        else:
            regions.add(tap)
    assert "exit_confirm.cancel" in regions
    assert "exit_confirm.confirm" not in regions


def test_graph_routes_exit_confirm_to_main_city() -> None:
    _static, _dynamic, graph = screen_graph.graph_for_game(game=RU_CATALOG)
    assert "main_city" in graph.get("exit_confirm", set())
    assert screen_graph.bfs_route("exit_confirm", "main_city", game=RU_CATALOG) == [
        "exit_confirm",
        "main_city",
    ]


def test_analyze_auto_cancel_taps_cancel_never_confirm() -> None:
    rules = _load_yaml(MODULE_DIR / "analyze" / "analyze.yaml")["overlay"]
    rule = next(r for r in rules if r["name"] == "exit_confirm.cancel.auto")
    assert rule["screens"] == ["exit_confirm"]
    assert rule.get("device_level") is True
    clicks = [step["click"] for step in rule["steps"] if "click" in step]
    assert clicks == ["exit_confirm.cancel"]
    assert "exit_confirm.confirm" not in clicks


def test_ru_overlay_supplies_the_body_crop() -> None:
    # Under the RU catalog the overlay region wins (first-wins / prepended), so the
    # body crop must resolve into games/wos/ru — RU assets live there, not in core.
    doc = load_area_doc(REPO_ROOT, game=RU_CATALOG)
    ocr = None
    for screen in doc["screens"]:
        for region in screen.get("regions", []):
            if region.get("name") == "exit_confirm.body":
                ocr = screen.get("ocr")
                break
        if ocr:
            break
    assert ocr is not None
    crop_path = exported_crop_png(REPO_ROOT, ocr, "exit_confirm.body")
    assert crop_path.is_file(), crop_path
    assert "games/wos/ru/core/exit_confirm" in crop_path.as_posix()


def test_body_crop_matches_reference_frame() -> None:
    # Regression guard: the crop + bbox must still match the captured dialog frame.
    region = _region(_load_yaml(RU_MODULE_DIR / "area.yaml"), "exit_confirm.body")
    frame = cv2.imread(str(RU_MODULE_DIR / "references" / "exit_confirm.png"))
    crop = cv2.imread(
        str(RU_MODULE_DIR / "references" / "crop" / "exit_confirm_exit_confirm.body.png")
    )
    assert frame is not None and crop is not None
    result = match_crop_1to1_at_bbox_percent(frame, crop, region["bbox"])
    assert result["score"] >= 0.9, result
