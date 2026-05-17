"""Tests for crop-hash + bbox overlap name reuse (no supervision import)."""

from __future__ import annotations

from PIL import Image

from omniparser.supervision_bridge import reuse_proposal_names_from_existing_crops


def test_reuse_proposal_names_when_overlap_and_crop_hash_match() -> None:
    image = Image.new("RGBA", (100, 100), (200, 50, 50, 255))
    bbox = {"x": 10.0, "y": 10.0, "width": 20.0, "height": 20.0}
    existing = [{"name": "btn.merge_gift", "bbox": dict(bbox)}]
    proposals = [{"name": "icon.parser_new", "bbox": {"x": 11.0, "y": 10.0, "width": 20.0, "height": 20.0}}]
    out, n = reuse_proposal_names_from_existing_crops(image, proposals, existing)
    assert n == 1
    assert out[0]["name"] == "btn.merge_gift"
    assert out[0]["bbox"] == bbox
    assert out[0]["_omni_matched_existing"] is True


def test_reuse_proposal_names_skips_when_no_overlap() -> None:
    image = Image.new("RGBA", (100, 100), (99, 99, 99, 255))
    existing = [
        {"name": "left.btn", "bbox": {"x": 5.0, "y": 5.0, "width": 10.0, "height": 10.0}},
    ]
    proposals = [
        {"name": "icon.z", "bbox": {"x": 80.0, "y": 80.0, "width": 10.0, "height": 10.0}},
    ]
    out, n = reuse_proposal_names_from_existing_crops(image, proposals, existing)
    assert n == 0
    assert out[0]["name"] == "icon.z"


def test_reuse_proposal_names_ignores_search_and_tap_regions() -> None:
    image = Image.new("RGBA", (100, 100), (200, 50, 50, 255))
    bbox = {"x": 10.0, "y": 10.0, "width": 20.0, "height": 20.0}
    existing = [
        {"name": "gift_button_search", "bbox": dict(bbox)},
        {"name": "gift_button_tap", "bbox": dict(bbox)},
    ]
    proposals = [{"name": "icon.parser_new", "bbox": dict(bbox)}]

    out, n = reuse_proposal_names_from_existing_crops(image, proposals, existing)

    assert n == 0
    assert out[0]["name"] == "icon.parser_new"
