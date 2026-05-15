"""Labeling OmniParser merge behavior."""

from __future__ import annotations

from omniparser.convert import region_hash
from ui.labeling_omniparser import merge_omniparser_regions


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
