"""Sync ``analyze.yaml`` overlay aux keys from Labeling toggles."""

from __future__ import annotations

from pathlib import Path

import yaml

from ui.overlay_yaml_sync import (
    rename_findicon_overlay_primary,
    sync_findicon_overlay_aux_keys,
)


def test_sync_sets_and_clears_search_and_tap_keys(tmp_path: Path) -> None:
    ref_dir = tmp_path / "references"
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

    assert sync_findicon_overlay_aux_keys(tmp_path, "foo", use_search=True, use_tap=True) is True
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    rule = doc["overlay"][0]
    assert rule["search_region"] == "foo_search"
    assert rule["tap_region"] == "foo_tap"

    assert sync_findicon_overlay_aux_keys(tmp_path, "foo", use_search=False, use_tap=False) is True
    doc2 = yaml.safe_load(path.read_text(encoding="utf-8"))
    rule2 = doc2["overlay"][0]
    assert "search_region" not in rule2
    assert "tap_region" not in rule2


def test_rename_findicon_overlay_primary_updates_region_and_aux_keys(tmp_path: Path) -> None:
    ref_dir = tmp_path / "references"
    ref_dir.mkdir(parents=True)
    raw = {
        "overlay": [
            {
                "name": "old.visible",
                "region": "old",
                "action": "findIcon",
                "search_region": "old_search",
                "tap_region": "old_tap",
                "tap_offset_from_match": True,
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
    assert rule["search_region"] == "new_search"
    assert rule["tap_region"] == "new_tap"
    assert rule["tap_offset_from_match"] is True

    assert rename_findicon_overlay_primary(tmp_path, "nope", "x") is False
