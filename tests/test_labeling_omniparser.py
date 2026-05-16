from __future__ import annotations

import hashlib

from PIL import Image

import omniparser.supervision_bridge as sb
from omniparser.types import ParsedUiElement
from ui import labeling_omniparser as omni
from ui.labeling_omniparser import merge_omniparser_regions


def test_filter_blacklisted_omniparser_regions_by_crop_hash(monkeypatch) -> None:
    image = Image.new("RGBA", (4, 4), (10, 20, 30, 255))
    digest = hashlib.sha256(image.tobytes()).hexdigest()
    monkeypatch.setattr(sb, "OMNIPARSER_CROP_HASH_BLACKLIST", frozenset({digest}))
    monkeypatch.setattr(sb, "OMNIPARSER_NAME_BLACKLIST_PREFIXES", ())
    regions = [
        {
            "name": "icon.decor",
            "bbox": {
                "x": 0.0,
                "y": 0.0,
                "width": 100.0,
                "height": 100.0,
            },
        },
    ]

    kept, skipped = omni._filter_blacklisted_omniparser_regions(image, regions)

    assert kept == []
    assert skipped == 1


def test_filter_blacklisted_omniparser_regions_by_name() -> None:
    image = Image.new("RGBA", (4, 4), (10, 20, 30, 255))
    regions = [
        {
            "name": "icon.unanswerable.2",
            "bbox": {
                "x": 0.0,
                "y": 0.0,
                "width": 50.0,
                "height": 50.0,
            },
        },
        {
            "name": "icon.real_button",
            "bbox": {
                "x": 50.0,
                "y": 50.0,
                "width": 50.0,
                "height": 50.0,
            },
        },
        {
            "name": "text.6_6",
            "bbox": {
                "x": 25.0,
                "y": 25.0,
                "width": 25.0,
                "height": 25.0,
            },
        },
    ]

    kept, skipped = omni._filter_blacklisted_omniparser_regions(image, regions)

    assert [r["name"] for r in kept] == ["icon.real_button"]
    assert skipped == 2


def _region(name: str, *, x: float = 10.0) -> dict[str, object]:
    from omniparser.convert import region_hash

    region: dict[str, object] = {
        "name": name,
        "action": "exist",
        "type": "string",
        "bbox": {"x": x, "y": 20.0, "width": 10.0, "height": 10.0},
    }
    region["hash"] = region_hash(region)
    return region


def test_merge_adds_alias_when_hash_matches_current_region() -> None:
    existing = [_region("icon.close")]
    proposed = [_region("icon.dismiss")]

    merged, added, aliased, skipped = merge_omniparser_regions(existing, proposed)

    assert merged == existing
    assert added == 0
    assert aliased == 1
    assert skipped == 0
    assert existing[0]["aliases"] == ["icon.dismiss"]


def test_merge_only_considers_current_regions_for_name_duplicates() -> None:
    existing = [_region("icon.keep", x=70.0)]
    proposed = [_region("icon.close", x=10.0)]

    merged, added, aliased, skipped = merge_omniparser_regions(existing, proposed)

    assert [r["name"] for r in merged] == ["icon.keep", "icon.close"]
    assert added == 1
    assert aliased == 0
    assert skipped == 0


def test_min_area_prefilter_skips_small_elements() -> None:
    big = ParsedUiElement(
        type="icon",
        bbox=(0.0, 0.0, 0.5, 0.5),
        interactivity=True,
        content="icon 0.9",
    )
    tiny = ParsedUiElement(
        type="icon",
        bbox=(0.9, 0.9, 0.901, 0.901),
        interactivity=True,
        content="icon 0.9",
    )
    image = Image.new("RGB", (200, 200), (255, 255, 255))
    regions, stats = sb.build_omniparser_proposal_regions(
        (big, tiny),
        image,
        width=200,
        height=200,
        min_area_pct=0.04,
        nms_iou_threshold=0.5,
    )
    names = [str(r["name"]) for r in regions]
    assert len(names) >= 1
    assert stats.skipped_min_area >= 1


def test_nms_suppresses_overlapping_duplicate_boxes() -> None:
    a = ParsedUiElement(
        type="icon",
        bbox=(0.0, 0.0, 0.5, 0.5),
        interactivity=True,
        content="icon 0.95",
    )
    b = ParsedUiElement(
        type="icon",
        bbox=(0.0, 0.0, 0.46, 0.46),
        interactivity=True,
        content="icon 0.85",
    )
    image = Image.new("RGB", (100, 100), (0, 0, 0))
    regs, stats = sb.build_omniparser_proposal_regions(
        (a, b),
        image,
        width=100,
        height=100,
        min_area_pct=0.01,
        nms_iou_threshold=0.1,
    )
    assert stats.after_min_area_count == 2
    assert len(regs) == 1
    assert stats.nms_removed == 1


def test_merge_detected_regions_replace() -> None:
    existing = [_region("keep")]
    proposed = [_region("new", x=50.0)]
    merged, added, aliased, skipped = sb.merge_detected_regions(
        merge_mode="replace",
        existing=existing,
        proposed_regions=proposed,
    )
    assert merged == proposed
    assert added == len(proposed)
    assert aliased == 0
    assert skipped == 0


def test_roundtrip_parse_element_json() -> None:
    original = ParsedUiElement(
        type="text",
        bbox=(0.1, 0.2, 0.55, 0.6),
        interactivity=False,
        content="OK",
    )
    d = sb.parsed_element_to_dict(original)
    back = sb.parsed_element_from_dict(dict(d))
    assert back == original

