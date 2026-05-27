"""Sync ``analyze.yaml`` overlay aux keys from Labeling toggles."""

from __future__ import annotations

from pathlib import Path

import yaml

from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for
from dashboard.overlay_yaml_sync import (
    cascade_aux_region_names,
    detect_region_renames,
    rename_findicon_overlay_primary,
    sync_findicon_overlay_aux_keys,
)


def _write_module_analyze(tmp_path: Path, raw: dict) -> None:
    """Real module layout so :func:`iter_analyze_manifest_paths` finds YAML."""
    mod = _modules_root_for(_default_game(), repo_root=tmp_path) / "zz_overlay_sync_test"
    mod.mkdir(parents=True)
    (mod / "module.yaml").write_text(
        yaml.dump({"id": "zz_overlay_sync_test", "name": "test"}),
        encoding="utf-8",
    )
    (mod / "analyze").mkdir(parents=True)
    (mod / "analyze" / "analyze.yaml").write_text(
        yaml.dump(raw, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def test_sync_sets_and_clears_search_and_tap_keys(tmp_path: Path) -> None:
    raw = {
        "overlay": [
            {
                "name": "foo.visible",
                "region": "foo",
                "action": "findIcon",
                "threshold": 0.9,
            }
        ]
    }
    _write_module_analyze(tmp_path, raw)
    path = _modules_root_for(_default_game(), repo_root=tmp_path) / "zz_overlay_sync_test" / "analyze" / "analyze.yaml"

    assert sync_findicon_overlay_aux_keys(tmp_path, "foo", use_search=True) is True
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    rule = doc["overlay"][0]
    assert "search_region" not in rule

    path.write_text(
        yaml.dump(
            {
                "overlay": [
                    {
                        "name": "foo.visible",
                        "region": "foo",
                        "action": "findIcon",
                        "threshold": 0.9,
                        "search_region": "foo_search",
                    }
                ]
            },
            sort_keys=False,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    assert sync_findicon_overlay_aux_keys(tmp_path, "foo", use_search=False) is True
    doc2 = yaml.safe_load(path.read_text(encoding="utf-8"))
    rule2 = doc2["overlay"][0]
    assert "search_region" not in rule2


def test_detect_region_renames_matches_bbox() -> None:
    bbox = {"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0, "rotation": 0.0}
    old = [{"name": "ads.natalia", "action": "exist", "bbox": bbox}]
    new = [{"name": "ads.natalia.title", "action": "exist", "bbox": bbox}]
    assert detect_region_renames(old, new) == [("ads.natalia", "ads.natalia.title")]


def test_rename_findicon_overlay_primary_updates_region_and_aux_keys(tmp_path: Path) -> None:
    raw = {
        "overlay": [
            {
                "name": "old.visible",
                "region": "old",
                "action": "findIcon",
                "search_region": "old_search",
                "threshold": 0.88,
            }
        ]
    }
    _write_module_analyze(tmp_path, raw)
    path = _modules_root_for(_default_game(), repo_root=tmp_path) / "zz_overlay_sync_test" / "analyze" / "analyze.yaml"

    assert rename_findicon_overlay_primary(tmp_path, "old", "new") is True
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    rule = doc["overlay"][0]
    assert rule["region"] == "new"
    assert "search_region" not in rule

    assert rename_findicon_overlay_primary(tmp_path, "nope", "x") is False


def test_cascade_aux_region_names_returns_existing_helpers() -> None:
    existing = {"mailBox", "mailBox_search", "mailBox_tap", "other"}

    cascade = cascade_aux_region_names("mailBox", existing)

    assert cascade == ["mailBox_tap"]


def test_cascade_aux_region_names_skips_missing_helpers() -> None:
    existing = {"isWorkers", "isWorkers_tap"}

    cascade = cascade_aux_region_names("isWorkers", existing)

    assert cascade == ["isWorkers_tap"]


def test_cascade_aux_region_names_empty_when_primary_is_aux() -> None:
    # Deleting an aux region must NOT cascade to its primary; if user wants to
    # remove the whole group they delete the primary itself.
    existing = {"mailBox", "mailBox_search", "mailBox_tap"}

    assert cascade_aux_region_names("mailBox_search", existing) == []
    assert cascade_aux_region_names("mailBox_tap", existing) == []


def test_cascade_aux_region_names_empty_for_blank_input() -> None:
    assert cascade_aux_region_names("", {"foo_search"}) == []
    assert cascade_aux_region_names("   ", {"foo_search"}) == []


def test_building_furniture_overlay_uses_image_match() -> None:
    repo = Path(__file__).resolve().parents[2]
    doc = yaml.safe_load(
        (repo / "games/wos/core/building/common/analyze/analyze.yaml").read_text(encoding="utf-8")
    )
    rules = doc.get("overlay") or []
    assert not any(
        isinstance(r, dict) and r.get("name") == "building.visible" for r in rules
    )
    rule = next(
        r for r in rules
        if isinstance(r, dict) and r.get("name") == "page.building.furniture.present"
    )

    assert rule["region"] == "page.building.furniture"
    assert rule["action"] == "findIcon"
    assert rule["threshold"] == 0.9
    assert rule["screens"] == ["building"]
    assert rule["set_node"] == "building"
