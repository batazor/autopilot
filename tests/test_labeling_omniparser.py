from __future__ import annotations

import hashlib

from PIL import Image

from omniparser.convert import region_hash
from ui import labeling_omniparser as omni
from ui.labeling_omniparser import merge_omniparser_regions


def test_filter_blacklisted_omniparser_regions_by_crop_hash(monkeypatch) -> None:
    image = Image.new("RGBA", (4, 4), (10, 20, 30, 255))
    digest = hashlib.sha256(image.tobytes()).hexdigest()
    monkeypatch.setattr(omni, "OMNIPARSER_CROP_HASH_BLACKLIST", frozenset({digest}))
    monkeypatch.setattr(omni, "OMNIPARSER_NAME_BLACKLIST_PREFIXES", ())
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
    ]

    kept, skipped = omni._filter_blacklisted_omniparser_regions(image, regions)

    assert [r["name"] for r in kept] == ["icon.real_button"]
    assert skipped == 1


def _region(name: str, *, x: float = 10.0) -> dict[str, object]:
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
