"""Sync ``analyze.yaml`` overlay aux keys from Labeling toggles."""

from __future__ import annotations

from pathlib import Path

import yaml

from ui.overlay_yaml_sync import (
    cascade_aux_region_names,
    rename_findicon_overlay_primary,
    sync_findicon_overlay_aux_keys,
)


def test_sync_sets_and_clears_search_and_tap_keys(tmp_path: Path) -> None:
    ref_dir = tmp_path / "analyze"
    ref_dir.mkdir(parents=True)
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
    path = ref_dir / "analyze.yaml"
    path.write_text(yaml.dump(raw, sort_keys=False, default_flow_style=False), encoding="utf-8")

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


def test_rename_findicon_overlay_primary_updates_region_and_aux_keys(tmp_path: Path) -> None:
    ref_dir = tmp_path / "analyze"
    ref_dir.mkdir(parents=True)
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
    path = ref_dir / "analyze.yaml"
    path.write_text(yaml.dump(raw, sort_keys=False, default_flow_style=False), encoding="utf-8")

    assert rename_findicon_overlay_primary(tmp_path, "old", "new") is True
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    rule = doc["overlay"][0]
    assert rule["region"] == "new"
    assert "search_region" not in rule

    assert rename_findicon_overlay_primary(tmp_path, "nope", "x") is False


def test_cascade_aux_region_names_returns_existing_helpers() -> None:
    existing = {"mailBox", "mailBox_search", "mailBox_tap", "other"}

    cascade = cascade_aux_region_names("mailBox", existing)

    assert cascade == ["mailBox_search", "mailBox_tap"]


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
    repo = Path(__file__).resolve().parents[1]
    doc = yaml.safe_load(
        (repo / "analyze/analyze_pages/analyze_building.yaml").read_text(encoding="utf-8")
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
