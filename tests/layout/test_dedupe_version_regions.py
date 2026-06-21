"""Tests for dropping version overrides identical to the base region on save."""

from __future__ import annotations

from layout.area_regions import dedupe_redundant_version_regions

_BASE_REG = {
    "name": "btn",
    "action": "exist",
    "type": "string",
    "threshold": 0.9,
    "bbox": {
        "x": 1.0,
        "y": 2.0,
        "width": 3.0,
        "height": 4.0,
        "rotation": 0.0,
        "original_width": 720,
        "original_height": 1280,
    },
}


def _screen_with_v2(*, base_regions: list[dict], v2_regions: list[dict]) -> dict:
    return {
        "id": 1,
        "screen_id": "test",
        "ocr": "references/x.png",
        "regions": base_regions,
        "versions": [
            {
                "id": "v2",
                "cond": "True",
                "ocr": "references/y.png",
                "regions": v2_regions,
            }
        ],
    }


def test_dedupe_drops_v2_when_same_as_base() -> None:
    """Override identical to base is redundant — resolver would return base anyway."""
    override = dict(_BASE_REG)
    doc = {"screens": [_screen_with_v2(base_regions=[dict(_BASE_REG)], v2_regions=[override])]}
    assert dedupe_redundant_version_regions(doc) == 1
    assert [r["name"] for r in doc["screens"][0]["regions"]] == ["btn"]
    assert doc["screens"][0]["versions"][0]["regions"] == []


def test_dedupe_keeps_v2_when_bbox_differs() -> None:
    override = {**_BASE_REG, "bbox": {**_BASE_REG["bbox"], "x": 99.0}}  # ty: ignore[invalid-argument-type]
    doc = {"screens": [_screen_with_v2(base_regions=[dict(_BASE_REG)], v2_regions=[override])]}
    assert dedupe_redundant_version_regions(doc) == 0
    assert len(doc["screens"][0]["versions"][0]["regions"]) == 1


def test_dedupe_skips_when_no_versions_block() -> None:
    """Without versions[], the dedupe walk has nothing to consider."""
    doc = {
        "screens": [
            {
                "id": 1,
                "screen_id": "test",
                "ocr": "references/x.png",
                "regions": [dict(_BASE_REG)],
            }
        ]
    }
    assert dedupe_redundant_version_regions(doc) == 0


def test_dedupe_keeps_v2_only_region_with_no_base_counterpart() -> None:
    """A v2-only region is not redundant — there's nothing in base to fall back to."""
    only_v2 = {**_BASE_REG, "name": "only_v2"}
    doc = {"screens": [_screen_with_v2(base_regions=[], v2_regions=[only_v2])]}
    assert dedupe_redundant_version_regions(doc) == 0
    assert len(doc["screens"][0]["versions"][0]["regions"]) == 1


def test_dedupe_drops_redundant_overrides_across_multiple_versions() -> None:
    base = {**_BASE_REG, "name": "z"}
    v3 = dict(base)
    doc = {
        "screens": [
            {
                "id": 1,
                "screen_id": "t",
                "ocr": "references/x.png",
                "regions": [base],
                "versions": [
                    {"id": "v2", "cond": "False", "ocr": "", "regions": []},
                    {"id": "v3", "cond": "True", "ocr": "", "regions": [v3]},
                ],
            }
        ]
    }
    assert dedupe_redundant_version_regions(doc) == 1
    assert doc["screens"][0]["versions"][1]["regions"] == []
