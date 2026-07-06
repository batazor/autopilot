"""``static`` / ``isSearch`` exclusivity — save-time normalization."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from dashboard.area_doc import save_json, strip_search_on_static_regions

if TYPE_CHECKING:
    from pathlib import Path


def _doc_with(region: dict) -> dict:
    return {
        "version": 2,
        "screens": [{"id": 1, "screen_id": "s", "ocr": "references/s.png", "regions": [region]}],
    }


def test_strip_search_on_static_regions_drops_is_search() -> None:
    doc = _doc_with(
        {
            "name": "r",
            "action": "exist",
            "static": True,
            "isSearch": True,
            "bbox": {"x": 1, "y": 1, "width": 1, "height": 1},
        }
    )
    assert strip_search_on_static_regions(doc) == 1
    region = doc["screens"][0]["regions"][0]
    assert region.get("static") is True
    assert "isSearch" not in region


def test_strip_search_on_static_regions_keeps_plain_search() -> None:
    doc = _doc_with(
        {
            "name": "r",
            "action": "exist",
            "isSearch": True,
            "bbox": {"x": 1, "y": 1, "width": 1, "height": 1},
        }
    )
    assert strip_search_on_static_regions(doc) == 0
    assert doc["screens"][0]["regions"][0].get("isSearch") is True


def test_save_json_normalizes_static_regions(tmp_path: Path) -> None:
    path = tmp_path / "area.yaml"
    doc = _doc_with(
        {
            "name": "r",
            "action": "exist",
            "static": True,
            "isSearch": True,
            "bbox": {"x": 1, "y": 1, "width": 1, "height": 1},
        }
    )
    save_json(path, doc)  # type: ignore[arg-type]
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    region = saved["screens"][0]["regions"][0]
    assert region.get("static") is True
    assert "isSearch" not in region
