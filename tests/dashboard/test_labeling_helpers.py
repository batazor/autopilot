"""Tests for Labeling page helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dashboard.labeling_helpers import (
    build_reference_leaf_meta_index,
    format_reference_leaf_title,
    labeling_workflow_steps,
    preview_delete_reference_impact,
    suggest_basename_from_entry,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_build_reference_leaf_meta_index(tmp_path: Path) -> None:
    ref_root = tmp_path / "references"
    ref_root.mkdir()
    doc = {
        "screens": [
            {
                "screen_id": "page.shop",
                "ocr": "references/inst_page.shop.png",
                "regions": [{"name": "claim"}, {"name": "close"}],
                "active_version": "v2",
                "versions": [{"id": "v2", "regions": [{"name": "badge"}]}],
            },
        ],
    }
    meta = build_reference_leaf_meta_index(doc, ref_root)
    assert "inst_page.shop.png" in meta
    m = meta["inst_page.shop.png"]
    assert m.screen_id == "page.shop"
    assert m.region_count == 3
    assert m.active_version == "v2"
    assert format_reference_leaf_title("inst_page.shop.png", m) == (
        "inst_page.shop.png · 3 reg · v:v2"
    )


def test_format_reference_leaf_title_unassigned() -> None:
    title = format_reference_leaf_title(
        "orphan.png",
        None,
    )
    assert title == "⚠ orphan.png · no area.json"


def test_suggest_basename_from_entry() -> None:
    entry = {"screen_id": "main_city", "active_version": "v2"}
    assert suggest_basename_from_entry(entry, "emu1") == "emu1_main_city_v2"
    assert suggest_basename_from_entry({"screen_id": ""}, "emu1") is None


def test_labeling_workflow_steps_temporal() -> None:
    steps = labeling_workflow_steps(
        pending_rel="temporal/emu_shot.png",
        sel_rel="temporal/emu_shot.png",
        entry=None,
        region_count=0,
        area_saved=False,
    )
    assert steps[0].done
    assert not steps[1].done
    assert steps[0].detail == "temporal (unsaved)"


def test_preview_delete_reference_impact(tmp_path: Path) -> None:
    repo = tmp_path
    ref_root = repo / "references"
    crop_dir = ref_root / "crop"
    crop_dir.mkdir(parents=True)
    png = ref_root / "screen.png"
    png.write_bytes(b"x")
    (crop_dir / "screen_claim.png").write_bytes(b"y")
    doc = {
        "screens": [
            {
                "ocr": "references/screen.png",
                "regions": [{"name": "claim"}, {"name": "close"}],
            },
        ],
    }
    impact = preview_delete_reference_impact(repo, ref_root, "screen.png", doc)
    assert impact.area_entries == 1
    assert impact.region_names == ("claim", "close")
    assert impact.crop_count == 1
