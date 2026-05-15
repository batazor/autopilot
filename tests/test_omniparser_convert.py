"""OmniParser → area.json region conversion (no GPU)."""

from __future__ import annotations

from omniparser.convert import elements_to_regions, region_hash, region_name_for_element, slugify_region_name
from omniparser.types import ParsedUiElement


def test_slugify_region_name() -> None:
    assert slugify_region_name("Claim All!", fallback="icon_1") == "claim_all"
    assert slugify_region_name("   ", fallback="icon_1") == "icon_1"


def test_region_name_for_element_uses_namespaces() -> None:
    assert (
        region_name_for_element(
            ParsedUiElement(type="icon", bbox=(0, 0, 1, 1), interactivity=True, content="Close"),
            index=1,
        )
        == "icon.close"
    )
    assert (
        region_name_for_element(
            ParsedUiElement(type="icon", bbox=(0, 0, 1, 1), interactivity=False, content="Close"),
            index=1,
        )
        == "icon.close.disabled"
    )
    assert (
        region_name_for_element(
            ParsedUiElement(type="text", bbox=(0, 0, 1, 1), interactivity=False, content="Claim All!"),
            index=2,
        )
        == "text.claim_all"
    )


def test_elements_to_regions_percent_bbox() -> None:
    elements = [
        ParsedUiElement(
            type="icon",
            bbox=(0.1, 0.2, 0.3, 0.4),
            interactivity=True,
            content="Shop",
        ),
    ]
    regions = elements_to_regions(elements, image_width=720, image_height=1280)
    assert len(regions) == 1
    bbox = regions[0]["bbox"]
    assert isinstance(bbox, dict)
    assert bbox["x"] == 10.0
    assert bbox["y"] == 20.0
    assert bbox["width"] == 20.0
    assert bbox["height"] == 20.0
    assert bbox["original_width"] == 720
    assert bbox["original_height"] == 1280
    assert regions[0]["name"] == "icon.shop"
    assert regions[0]["action"] == "exist"
    assert isinstance(regions[0]["hash"], str)
    assert regions[0]["hash"] == region_hash(regions[0])


def test_elements_to_regions_dedupes_generated_names() -> None:
    elements = [
        ParsedUiElement(type="text", bbox=(0.0, 0.0, 0.2, 0.1), interactivity=False, content="Go"),
        ParsedUiElement(type="text", bbox=(0.0, 0.1, 0.2, 0.2), interactivity=False, content="Go"),
    ]
    regions = elements_to_regions(elements, image_width=100, image_height=100)
    assert len(regions) == 2
    assert {r["name"] for r in regions} == {"text.go", "text.go.2"}


def test_region_hash_is_stable_for_float_noise() -> None:
    base = {
        "name": "icon.close",
        "action": "exist",
        "type": "string",
        "bbox": {"x": 10.0000001, "y": 20.0, "width": 30.0, "height": 40.0},
    }
    same = {
        "name": "icon.close",
        "action": "exist",
        "type": "string",
        "bbox": {"height": 40.0, "width": 30.0, "y": 20.0, "x": 10.0000002},
    }
    assert region_hash(base) == region_hash(same)
