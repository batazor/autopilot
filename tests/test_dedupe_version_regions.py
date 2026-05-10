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


def _screen_with_versions(regions: list[dict]) -> dict:
    return {
        "id": 1,
        "screen_id": "test",
        "ocr": "references/x.png",
        "regions": regions,
        "versions": [{"id": "v2", "cond": "True", "ocr": "references/y.png"}],
    }


def test_dedupe_drops_v2_when_same_as_base() -> None:
    override = {**_BASE_REG, "name": "btn_v2"}
    doc = {"screens": [_screen_with_versions([dict(_BASE_REG), dict(override)])]}
    assert dedupe_redundant_version_regions(doc) == 1
    names = [r["name"] for r in doc["screens"][0]["regions"]]
    assert names == ["btn"]


def test_dedupe_keeps_v2_when_bbox_differs() -> None:
    override = {
        **_BASE_REG,
        "name": "btn_v2",
        "bbox": {**_BASE_REG["bbox"], "x": 99.0},
    }
    doc = {"screens": [_screen_with_versions([dict(_BASE_REG), override])]}
    assert dedupe_redundant_version_regions(doc) == 0
    assert len(doc["screens"][0]["regions"]) == 2


def test_dedupe_skips_when_no_versions_block() -> None:
    override = {**_BASE_REG, "name": "btn_v2"}
    doc = {
        "screens": [
            {
                "id": 1,
                "screen_id": "test",
                "ocr": "references/x.png",
                "regions": [dict(_BASE_REG), override],
            }
        ]
    }
    assert dedupe_redundant_version_regions(doc) == 0


def test_dedupe_skips_override_without_base() -> None:
    orphan = {**_BASE_REG, "name": "only_v2"}
    doc = {"screens": [_screen_with_versions([orphan])]}
    assert dedupe_redundant_version_regions(doc) == 0


def test_dedupe_v3_same_as_base() -> None:
    base = dict(_BASE_REG)
    base["name"] = "z"
    v3 = {**base, "name": "z_v3"}
    doc = {
        "screens": [
            {
                "id": 1,
                "screen_id": "t",
                "ocr": "references/x.png",
                "regions": [base, v3],
                "versions": [
                    {"id": "v2", "cond": "False", "ocr": ""},
                    {"id": "v3", "cond": "True", "ocr": ""},
                ],
            }
        ]
    }
    assert dedupe_redundant_version_regions(doc) == 1
    assert [r["name"] for r in doc["screens"][0]["regions"]] == ["z"]
