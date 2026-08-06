"""Unit tests for region-by-name resolution in ``layout/area_lookup.py`` /
``layout/area_regions.py``.
"""

from __future__ import annotations

from layout.area_lookup import screen_region_by_name
from layout.area_regions import region_bbox_for_name


def test_screen_region_by_name_resolves_base_region() -> None:
    doc = {
        "version": 2,
        "screens": [
            {
                "id": 1,
                "screen_id": "hero_card",
                "ocr": "references/hero_card.png",
                "regions": [
                    {"name": "promote_btn", "bbox": {"x": 10, "y": 10}},
                    {"name": "level_label", "bbox": {"x": 5, "y": 5}},
                ],
            }
        ],
    }
    res = screen_region_by_name(doc, "promote_btn")
    assert res is not None and res[1]["bbox"]["x"] == 10
    assert screen_region_by_name(doc, "no_such_region") is None


def test_screen_region_by_name_ignores_screen_id_for_global_names() -> None:
    doc = {
        "version": 2,
        "screens": [
            {
                "id": 1,
                "screen_id": "screen_a",
                "regions": [{"name": "icon.close", "bbox": {"x": 10}}],
            },
            {
                "id": 2,
                "screen_id": "screen_b",
                "regions": [{"name": "icon.close.screen_b", "bbox": {"x": 80}}],
            },
        ],
    }

    pair = screen_region_by_name(doc, "icon.close", screen_id="screen_b")

    assert pair is not None
    entry, region = pair
    assert entry["screen_id"] == "screen_a"
    assert region["bbox"]["x"] == 10

    pair_b = screen_region_by_name(doc, "icon.close.screen_b")
    assert pair_b is not None
    assert pair_b[0]["screen_id"] == "screen_b"
    assert pair_b[1]["bbox"]["x"] == 80


def test_screen_region_by_name_resolves_region_alias() -> None:
    doc = {
        "version": 2,
        "screens": [
            {
                "id": 1,
                "screen_id": "screen_a",
                "regions": [
                    {
                        "name": "icon.close",
                        "aliases": ["icon.dismiss"],
                        "bbox": {"x": 10},
                    }
                ],
            }
        ],
    }

    pair = screen_region_by_name(doc, "icon.dismiss", screen_id="screen_a")

    assert pair is not None
    assert pair[1]["name"] == "icon.close"


def test_region_bbox_for_name_base_lookup() -> None:
    doc = {
        "version": 2,
        "screens": [
            {
                "id": 1,
                "screen_id": "hero_card",
                "regions": [{"name": "promote_btn", "bbox": {"x": 10, "y": 10}}],
            }
        ],
    }
    assert region_bbox_for_name(doc, "promote_btn") == {"x": 10, "y": 10}
    assert region_bbox_for_name(doc, "missing") is None
