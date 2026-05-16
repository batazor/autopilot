from __future__ import annotations

import copy
import hashlib

from PIL import Image

import omniparser.supervision_bridge as sb
from omniparser.convert import elements_to_regions
from omniparser.types import ParsedUiElement
from ui import labeling_omniparser as omni
from ui.labeling_omniparser import merge_detected_regions, merge_omniparser_regions


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


def _region_by_name(regions: list[dict[str, object]], name: str) -> dict[str, object]:
    return next(r for r in regions if r.get("name") == name)


def _fixture_region(
    name: str,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
) -> dict[str, object]:
    return {
        "name": name,
        "action": "exist",
        "type": "string",
        "threshold": 0.9,
        "bbox": {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "rotation": 0.0,
            "original_width": 720,
            "original_height": 1280,
        },
    }


def _main_city_v2_fixture_regions() -> list[dict[str, object]]:
    """Small frozen fixture copied from ``area.json`` main_city.png version v2."""

    return [
        _fixture_region(
            "region",
            x=0.5386100386100386,
            y=0.21739130434782608,
            width=11.200772200772201,
            height=8.484347826086957,
        ),
        _fixture_region(
            "is_main_city",
            x=13.281853281853282,
            y=4.021739130434782,
            width=5.397683397683398,
            height=3.0358695652173915,
        ),
        _fixture_region(
            "mail.new",
            x=86.67953667953668,
            y=78.3695652173913,
            width=11.200772200772201,
            height=6.7,
        ),
        _fixture_region(
            "icon.world",
            x=80.87451737451738,
            y=90.95869565217392,
            width=17.214285714285715,
            height=9.041304347826086,
        ),
        _fixture_region(
            "main_city.resources",
            x=54.35521235521235,
            y=0.08260869565217391,
            width=15.127413127413128,
            height=2.9239130434782608,
        ),
        _fixture_region(
            "main_city.diamond",
            x=73.57722007722008,
            y=0.05217391304347826,
            width=18.853281853281853,
            height=3.5184782608695646,
        ),
    ]


def _main_city_v3_fixture_regions() -> list[dict[str, object]]:
    """Frozen Omni-style proposal fixture copied from a saved main_city_v3 run."""

    return [
        _fixture_region(
            "is_main_city",
            x=13.281853281853282,
            y=4.021739130434782,
            width=5.397683397683398,
            height=3.0358695652173915,
        ),
        _fixture_region(
            "text.60",
            x=94.16666666666667,
            y=78.75,
            width=4.1666666666666625,
            height=1.6406250000000067,
        ),
        _fixture_region(
            "icon.a_notification_indicating_a_number_60",
            x=86.81853281853282,
            y=78.79130434782608,
            width=10.922780922780923,
            height=6.091304347826089,
        ),
        _fixture_region(
            "icon.world",
            x=80.87451737451738,
            y=90.98434782608696,
            width=17.214285714285715,
            height=9.015652173913043,
        ),
        _fixture_region(
            "icon.54_1k",
            x=53.47876447876448,
            y=0.0,
            width=26.924710424710426,
            height=3.5630434782608695,
        ),
        _fixture_region(
            "icon.a_low_profile_or_profile_icon",
            x=82.23359073359073,
            y=0.43695652173913035,
            width=10.204633204633204,
            height=2.6934782608695653,
        ),
    ]


def test_merge_adds_alias_when_hash_matches_current_region() -> None:
    existing = [_region("icon.close")]
    proposed = [_region("icon.dismiss")]

    merged, added, aliased, skipped = merge_omniparser_regions(existing, proposed)

    assert merged == existing
    assert added == 0
    assert aliased == 1
    assert skipped == 0
    assert existing[0]["aliases"] == ["icon.dismiss"]


def test_merge_adds_alias_when_bbox_overlap_matches_current_region() -> None:
    existing = [_region("icon.close", x=10.0)]
    proposed = [_region("icon.dismiss", x=10.5)]

    merged, added, aliased, skipped = merge_omniparser_regions(existing, proposed)

    assert merged == existing
    assert added == 0
    assert aliased == 1
    assert skipped == 0
    assert existing[0]["aliases"] == ["icon.dismiss"]


def test_merge_skips_intersection_when_bbox_overlap_is_not_same_icon() -> None:
    existing = [_region("icon.keep", x=10.0)]
    proposed = [_region("icon.overlap", x=19.0)]

    merged, added, aliased, skipped = merge_omniparser_regions(existing, proposed)

    assert merged == existing
    assert added == 0
    assert aliased == 0
    assert skipped == 1


def test_merge_only_considers_current_regions_for_name_duplicates() -> None:
    existing = [_region("icon.keep", x=70.0)]
    proposed = [_region("icon.close", x=10.0)]

    merged, added, aliased, skipped = merge_omniparser_regions(existing, proposed)

    assert [r["name"] for r in merged] == ["icon.keep", "icon.close"]
    assert added == 1
    assert aliased == 0
    assert skipped == 0


def test_main_city_v3_omni_regions_match_main_city_v2_by_coordinates() -> None:
    existing_v2 = copy.deepcopy(_main_city_v2_fixture_regions())
    proposed_v3 = copy.deepcopy(_main_city_v3_fixture_regions())

    # If main_city_v3 was already saved after overlap reuse, it contains the
    # canonical names directly. Otherwise this catches the old Omni names and
    # verifies that the merge aliases them to v2 canonical regions.
    has_canonical_mail = any(r.get("name") == "mail.new" for r in proposed_v3)

    # The saved main_city_v3 proposal does not include a world-button candidate
    # yet, so model the common Omni behavior directly: same icon, bbox shifted by
    # a few pixels / tenths of a percent.
    shifted_world = copy.deepcopy(_region_by_name(existing_v2, "icon.world"))
    shifted_world["name"] = "icon.world.omni"
    shifted_bbox = shifted_world["bbox"]
    assert isinstance(shifted_bbox, dict)
    shifted_bbox["x"] = float(shifted_bbox["x"]) + 0.5
    shifted_bbox["y"] = float(shifted_bbox["y"]) - 0.25
    proposed_v3.append(shifted_world)

    merged, added, aliased, skipped = merge_omniparser_regions(existing_v2, proposed_v3)
    merged_by_name = {str(r.get("name")): r for r in merged}

    mail_aliases = merged_by_name["mail.new"].get("aliases")
    world_aliases = merged_by_name["icon.world"].get("aliases")
    assert isinstance(world_aliases, list)
    if not has_canonical_mail:
        assert isinstance(mail_aliases, list)
        assert any(alias in mail_aliases for alias in ("text.60", "icon.a_notification_indicating_a_number_60"))
    assert "icon.world.omni" in world_aliases
    assert aliased >= 1
    assert added >= 0
    assert skipped >= 0


def test_main_city_v3_proposals_reuse_main_city_v2_names_before_save() -> None:
    existing = copy.deepcopy(_main_city_v2_fixture_regions())
    renamed, reused, dropped = sb.reuse_proposal_names_from_overlapping_regions(
        copy.deepcopy(_main_city_v3_fixture_regions()),
        existing,
    )
    names = [str(r.get("name")) for r in renamed]

    assert "is_main_city" in names
    assert "region" not in names
    assert "mail.new" in names
    assert "icon.world" in names
    assert "main_city.resources" in names
    assert "main_city.diamond" in names
    assert "icon.a_notification_indicating_a_number_60" not in names
    assert "text.60" not in names
    assert "icon.54_1k" not in names
    assert "icon.a_low_profile_or_profile_icon" not in names
    assert names.count("mail.new") == 1
    assert reused >= 0
    assert dropped >= 0


def test_overlap_reuse_prefers_smaller_existing_region_when_scores_tie() -> None:
    proposals = [
        {
            "name": "icon.omni",
            "action": "exist",
            "type": "string",
            "bbox": {"x": 10.0, "y": 10.0, "width": 10.0, "height": 10.0},
        }
    ]
    existing = [
        {
            "name": "region",
            "action": "exist",
            "type": "string",
            "bbox": {"x": 0.0, "y": 0.0, "width": 40.0, "height": 40.0},
        },
        {
            "name": "main_city.title",
            "action": "exist",
            "type": "string",
            "bbox": {"x": 10.0, "y": 10.0, "width": 10.0, "height": 10.0},
        },
    ]

    renamed, reused, dropped = sb.reuse_proposal_names_from_overlapping_regions(proposals, existing)

    assert renamed[0]["name"] == "main_city.title"
    assert renamed[0]["bbox"] == existing[1]["bbox"]
    assert renamed[0]["_omni_matched_existing"] is True
    assert reused == 1
    assert dropped == 0


def test_overlap_reuse_copies_canonical_bbox_from_existing_region() -> None:
    proposals = [
        {
            "name": "icon.omni_title",
            "action": "exist",
            "type": "string",
            "bbox": {"x": 1.0, "y": 1.0, "width": 21.0, "height": 8.0},
        }
    ]
    existing = [
        {
            "name": "mail.title",
            "action": "exist",
            "type": "string",
            "bbox": {"x": 2.0, "y": 2.0, "width": 20.0, "height": 6.0},
        }
    ]

    renamed, reused, dropped = sb.reuse_proposal_names_from_overlapping_regions(proposals, existing)

    assert renamed[0]["name"] == "mail.title"
    assert renamed[0]["bbox"] == existing[0]["bbox"]
    assert renamed[0]["_omni_matched_existing"] is True
    assert renamed[0]["hash"] == sb.region_hash(renamed[0])
    assert reused == 1
    assert dropped == 0


def test_overlap_reuse_collapses_icon_and_text_inside_one_current_region() -> None:
    proposals = [
        {
            "name": "icon.omni_part",
            "action": "exist",
            "type": "string",
            "bbox": {"x": 12.0, "y": 12.0, "width": 5.0, "height": 6.0},
        },
        {
            "name": "text.omni_part",
            "action": "exist",
            "type": "string",
            "bbox": {"x": 20.0, "y": 12.0, "width": 18.0, "height": 6.0},
        },
    ]
    existing = [
        {
            "name": "mail.delete",
            "action": "exist",
            "type": "string",
            "bbox": {"x": 10.0, "y": 10.0, "width": 30.0, "height": 10.0},
        }
    ]

    renamed, reused, dropped = sb.reuse_proposal_names_from_overlapping_regions(proposals, existing)

    assert len(renamed) == 1
    assert renamed[0]["name"] == "mail.delete"
    assert renamed[0]["bbox"] == existing[0]["bbox"]
    assert renamed[0]["_omni_matched_existing"] is True
    assert reused == 1
    assert dropped == 1


def test_apply_strips_internal_omni_matched_marker() -> None:
    proposed = [
        {
            "name": "mail.title",
            "action": "exist",
            "type": "string",
            "_omni_matched_existing": True,
            "bbox": {"x": 10.0, "y": 10.0, "width": 10.0, "height": 10.0},
        }
    ]

    merged, added, aliased, skipped = merge_detected_regions(
        merge_mode="replace",
        existing=[],
        proposed_regions=proposed,
    )

    assert "_omni_matched_existing" not in merged[0]
    assert added == 1
    assert aliased == 0
    assert skipped == 0


def test_overlap_reuse_ignores_search_and_tap_auxiliary_regions() -> None:
    proposals = [
        {
            "name": "icon.omni",
            "action": "exist",
            "type": "string",
            "bbox": {"x": 10.0, "y": 10.0, "width": 10.0, "height": 10.0},
        }
    ]
    existing = [
        {
            "name": "go_big_button_search",
            "action": "exist",
            "type": "string",
            "bbox": {"x": 10.0, "y": 10.0, "width": 10.0, "height": 10.0},
        },
        {
            "name": "button.tap",
            "aliases": ["button_tap"],
            "action": "click",
            "type": "string",
            "bbox": {"x": 10.0, "y": 10.0, "width": 10.0, "height": 10.0},
        },
    ]

    renamed, reused, dropped = sb.reuse_proposal_names_from_overlapping_regions(proposals, existing)

    assert renamed[0]["name"] == "icon.omni"
    assert reused == 0
    assert dropped == 0


def test_merge_ignores_search_region_as_overlap_match_target() -> None:
    existing = [
        {
            "name": "go_big_button_search",
            "action": "exist",
            "type": "string",
            "bbox": {"x": 10.0, "y": 10.0, "width": 10.0, "height": 10.0},
        }
    ]
    proposed = [
        {
            "name": "icon.omni",
            "action": "exist",
            "type": "string",
            "bbox": {"x": 10.0, "y": 10.0, "width": 10.0, "height": 10.0},
        }
    ]

    merged, added, aliased, skipped = merge_omniparser_regions(existing, proposed)

    assert [r["name"] for r in merged] == ["go_big_button_search", "icon.omni"]
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


def test_omniparser_text_elements_are_persisted_as_exist_regions() -> None:
    el = ParsedUiElement(
        type="text",
        bbox=(0.1, 0.2, 0.4, 0.25),
        interactivity=False,
        content="Survival",
    )

    legacy_regions = elements_to_regions([el], image_width=100, image_height=100)
    bridge_regions, _stats = sb.build_omniparser_proposal_regions(
        (el,),
        Image.new("RGB", (100, 100), (255, 255, 255)),
        width=100,
        height=100,
        min_area_pct=0.01,
        nms_iou_threshold=0.5,
    )

    assert legacy_regions[0]["name"] == "text.survival"
    assert legacy_regions[0]["action"] == "exist"
    assert bridge_regions[0]["name"] == "text.survival"
    assert bridge_regions[0]["action"] == "exist"


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

