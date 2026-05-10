"""Unit tests for the multi-version screen support in ``layout/area_versions.py``
and the version-aware lookup paths in ``layout/area_lookup.py`` /
``layout/area_regions.py``.
"""

from __future__ import annotations

import pytest

from layout.area_lookup import screen_region_by_name
from layout.area_regions import region_bbox_for_name, validate_versions
from layout.area_versions import (
    effective_ocr_for_region,
    eval_cond,
    pick_active_version,
    region_version_of,
    resolve_region_with_version,
)


# ---- eval_cond ------------------------------------------------------------


def test_eval_cond_dotted_key_truthy() -> None:
    state = {"heroes.norah.level": 7}
    assert eval_cond("heroes.norah.level >= 6", state) is True


def test_eval_cond_dotted_key_falsy() -> None:
    state = {"heroes.norah.level": 3}
    assert eval_cond("heroes.norah.level >= 6", state) is False


def test_eval_cond_missing_key_returns_false() -> None:
    assert eval_cond("heroes.gisela.level >= 6", {}) is False


def test_eval_cond_empty_returns_false() -> None:
    assert eval_cond("", {"x": 1}) is False
    assert eval_cond("   ", {"x": 1}) is False


def test_eval_cond_syntax_error_returns_false() -> None:
    assert eval_cond("heroes.norah.level >>>= 6", {"heroes.norah.level": 9}) is False


def test_eval_cond_logical_ops() -> None:
    state = {"a.b": 5, "c.d": True}
    assert eval_cond("a.b > 3 and c.d", state) is True
    assert eval_cond("a.b > 100 or not c.d", state) is False


def test_eval_cond_keywords_not_rewritten() -> None:
    assert eval_cond("True", {}) is True
    assert eval_cond("not False", {}) is True


def test_eval_cond_bare_identifier_uses_state() -> None:
    assert eval_cond("level >= 6", {"level": 7}) is True


# ---- pick_active_version --------------------------------------------------


def test_pick_active_version_none_when_no_versions() -> None:
    entry = {"id": 1, "regions": []}
    assert pick_active_version(entry, {"x": 1}) is None


def test_pick_active_version_returns_first_truthy() -> None:
    entry = {
        "versions": [
            {"id": "v2", "cond": "heroes.norah.level >= 100"},
            {"id": "v3", "cond": "heroes.norah.level >= 6"},
        ]
    }
    assert pick_active_version(entry, {"heroes.norah.level": 7}) == "v3"


def test_pick_active_version_default_when_state_none() -> None:
    entry = {"versions": [{"id": "v2", "cond": "True"}]}
    assert pick_active_version(entry, None) is None


def test_pick_active_version_skips_invalid_entries() -> None:
    entry = {
        "versions": [
            {"id": "", "cond": "True"},
            {"id": "v2", "cond": ""},
            {"id": "v3", "cond": "True"},
        ]
    }
    assert pick_active_version(entry, {}) == "v3"


# ---- resolve_region_with_version ------------------------------------------


def _entry_with_versions(
    base: list[dict],
    *,
    v2_regions: list[dict] | None = None,
    v2_removed: list[str] | None = None,
) -> dict:
    ver_block: dict = {"id": "v2", "cond": "True"}
    if v2_regions is not None:
        ver_block["regions"] = v2_regions
    if v2_removed is not None:
        ver_block["removed"] = v2_removed
    return {
        "ocr": "references/screen.png",
        "regions": base,
        "versions": [ver_block],
    }


def test_resolve_default_returns_base() -> None:
    entry = _entry_with_versions(
        [{"name": "promote_btn", "bbox": {"x": 10}}],
        v2_regions=[{"name": "promote_btn", "bbox": {"x": 50}}],
    )
    reg = resolve_region_with_version(entry, "promote_btn", None)
    assert reg is not None and reg["bbox"]["x"] == 10


def test_resolve_v2_returns_override_when_present() -> None:
    entry = _entry_with_versions(
        [{"name": "promote_btn", "bbox": {"x": 10}}],
        v2_regions=[{"name": "promote_btn", "bbox": {"x": 50}}],
    )
    reg = resolve_region_with_version(entry, "promote_btn", "v2")
    assert reg is not None and reg["bbox"]["x"] == 50


def test_resolve_v2_falls_back_to_base_when_no_override() -> None:
    entry = _entry_with_versions(
        [{"name": "promote_btn", "bbox": {"x": 10}}, {"name": "level_label", "bbox": {"y": 5}}],
        v2_regions=[],
    )
    reg = resolve_region_with_version(entry, "level_label", "v2")
    assert reg is not None and reg["bbox"] == {"y": 5}


def test_resolve_v2_returns_none_for_removed_in_version() -> None:
    """Region exists in base but is listed in versions[v2].removed → resolver returns None."""
    entry = _entry_with_versions(
        [{"name": "old_chest", "bbox": {"x": 1}}],
        v2_regions=[],
        v2_removed=["old_chest"],
    )
    assert resolve_region_with_version(entry, "old_chest", "v2") is None
    # But under default it still resolves.
    assert resolve_region_with_version(entry, "old_chest", None) is not None


def test_resolve_v2_returns_version_only_region() -> None:
    """Region only in versions[v2].regions, no base → resolver returns it under v2 only."""
    entry = _entry_with_versions(
        [],
        v2_regions=[{"name": "new_minimap_btn", "bbox": {"x": 99}}],
    )
    assert resolve_region_with_version(entry, "new_minimap_btn", None) is None
    reg = resolve_region_with_version(entry, "new_minimap_btn", "v2")
    assert reg is not None and reg["bbox"]["x"] == 99


def test_resolve_unknown_name_returns_none() -> None:
    entry = _entry_with_versions([{"name": "a"}], v2_regions=[{"name": "b"}])
    assert resolve_region_with_version(entry, "missing", "v2") is None


# ---- effective_ocr_for_region --------------------------------------------


def test_effective_ocr_for_version_region_uses_version_ocr() -> None:
    entry = {
        "ocr": "references/main_city.png",
        "regions": [],
        "versions": [
            {
                "id": "v2",
                "cond": "True",
                "ocr": "references/main_city_v2.png",
                "regions": [{"name": "is_new_chapter", "bbox": {}}],
            }
        ],
    }
    reg = entry["versions"][0]["regions"][0]
    assert effective_ocr_for_region(entry, reg) == "references/main_city_v2.png"


def test_effective_ocr_for_base_region_uses_default_ocr() -> None:
    entry = {
        "ocr": "references/main_city.png",
        "regions": [{"name": "is_new_chapter", "bbox": {}}],
        "versions": [
            {
                "id": "v2",
                "cond": "True",
                "ocr": "references/main_city_v2.png",
                "regions": [],
            }
        ],
    }
    reg = entry["regions"][0]
    assert effective_ocr_for_region(entry, reg) == "references/main_city.png"


def test_effective_ocr_falls_back_to_default_when_version_has_no_ocr() -> None:
    entry = {
        "ocr": "references/main_city.png",
        "regions": [],
        "versions": [
            {
                "id": "v2",
                "cond": "True",
                "regions": [{"name": "v2_only", "bbox": {}}],
            }
        ],
    }
    reg = entry["versions"][0]["regions"][0]
    assert effective_ocr_for_region(entry, reg) == "references/main_city.png"


# ---- region_version_of ----------------------------------------------------


def test_region_version_of_base_returns_none() -> None:
    entry = _entry_with_versions(
        [{"name": "x"}],
        v2_regions=[{"name": "y"}],
    )
    assert region_version_of(entry, entry["regions"][0]) is None


def test_region_version_of_inside_version_block() -> None:
    entry = _entry_with_versions(
        [{"name": "x"}],
        v2_regions=[{"name": "y"}],
    )
    y_reg = entry["versions"][0]["regions"][0]
    assert region_version_of(entry, y_reg) == "v2"


# ---- validate_versions ----------------------------------------------------


def _doc(entry: dict) -> dict:
    return {"version": 2, "screens": [entry]}


def test_validate_versions_accepts_well_formed_with_overrides() -> None:
    doc = _doc(
        {
            "id": 1,
            "screen_id": "hero_card",
            "regions": [{"name": "promote_btn", "bbox": {}}],
            "versions": [
                {
                    "id": "v2",
                    "cond": "heroes.norah.level >= 6",
                    "regions": [{"name": "promote_btn", "bbox": {}}],
                }
            ],
        }
    )
    validate_versions(doc)  # should not raise


def test_validate_versions_accepts_removed() -> None:
    doc = _doc(
        {
            "id": 1,
            "regions": [{"name": "old_btn", "bbox": {}}],
            "versions": [
                {"id": "v2", "cond": "True", "removed": ["old_btn"]},
            ],
        }
    )
    validate_versions(doc)  # should not raise


def test_validate_versions_rejects_bad_id_format() -> None:
    doc = _doc(
        {"id": 1, "regions": [], "versions": [{"id": "version2", "cond": "True"}]}
    )
    with pytest.raises(ValueError, match="version id"):
        validate_versions(doc)


def test_validate_versions_rejects_duplicate_ids() -> None:
    doc = _doc(
        {
            "id": 1,
            "regions": [],
            "versions": [
                {"id": "v2", "cond": "True"},
                {"id": "v2", "cond": "False"},
            ],
        }
    )
    with pytest.raises(ValueError, match="duplicate version"):
        validate_versions(doc)


def test_validate_versions_rejects_empty_cond() -> None:
    doc = _doc({"id": 1, "regions": [], "versions": [{"id": "v2", "cond": ""}]})
    with pytest.raises(ValueError, match="empty 'cond'"):
        validate_versions(doc)


def test_validate_versions_rejects_cond_syntax_error() -> None:
    doc = _doc(
        {"id": 1, "regions": [], "versions": [{"id": "v2", "cond": "a >>= b"}]}
    )
    with pytest.raises(ValueError, match="syntax error"):
        validate_versions(doc)


def test_validate_versions_rejects_removed_naming_unknown_region() -> None:
    doc = _doc(
        {
            "id": 1,
            "regions": [{"name": "exists", "bbox": {}}],
            "versions": [{"id": "v2", "cond": "True", "removed": ["does_not_exist"]}],
        }
    )
    with pytest.raises(ValueError, match="non-existent base region"):
        validate_versions(doc)


def test_validate_versions_rejects_remove_and_override_conflict() -> None:
    doc = _doc(
        {
            "id": 1,
            "regions": [{"name": "btn", "bbox": {}}],
            "versions": [
                {
                    "id": "v2",
                    "cond": "True",
                    "regions": [{"name": "btn", "bbox": {}}],
                    "removed": ["btn"],
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="cannot both override and remove"):
        validate_versions(doc)


def test_validate_versions_rejects_duplicate_names_within_version_block() -> None:
    doc = _doc(
        {
            "id": 1,
            "regions": [],
            "versions": [
                {
                    "id": "v2",
                    "cond": "True",
                    "regions": [
                        {"name": "btn", "bbox": {}},
                        {"name": "btn", "bbox": {}},
                    ],
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="duplicate region name"):
        validate_versions(doc)


# ---- screen_region_by_name + region_bbox_for_name (state-aware) -----------


def _doc_with_versions() -> dict:
    return {
        "version": 2,
        "screens": [
            {
                "id": 1,
                "screen_id": "hero_card",
                "ocr": "references/hero_card.png",
                "regions": [
                    {"name": "promote_btn", "bbox": {"x": 10, "y": 10}},
                    {"name": "level_label", "bbox": {"x": 5, "y": 5}},
                    {"name": "old_chest", "bbox": {"x": 1, "y": 1}},
                ],
                "versions": [
                    {
                        "id": "v2",
                        "cond": "heroes.norah.level >= 6",
                        "ocr": "references/hero_card_v2.png",
                        "regions": [
                            {"name": "promote_btn", "bbox": {"x": 50, "y": 80}},
                            {"name": "new_minimap", "bbox": {"x": 90, "y": 90}},
                        ],
                        "removed": ["old_chest"],
                    }
                ],
            }
        ],
    }


def test_screen_region_by_name_default_when_state_none() -> None:
    doc = _doc_with_versions()
    res = screen_region_by_name(doc, "promote_btn")
    assert res is not None and res[1]["bbox"]["x"] == 10


def test_screen_region_by_name_picks_v2_override_under_matching_state() -> None:
    doc = _doc_with_versions()
    res = screen_region_by_name(doc, "promote_btn", state_flat={"heroes.norah.level": 9})
    assert res is not None and res[1]["bbox"]["x"] == 50


def test_screen_region_by_name_picks_base_under_non_matching_state() -> None:
    doc = _doc_with_versions()
    res = screen_region_by_name(doc, "promote_btn", state_flat={"heroes.norah.level": 2})
    assert res is not None and res[1]["bbox"]["x"] == 10


def test_screen_region_by_name_v2_active_falls_back_for_unmoved_region() -> None:
    doc = _doc_with_versions()
    res = screen_region_by_name(doc, "level_label", state_flat={"heroes.norah.level": 9})
    assert res is not None and res[1]["bbox"]["x"] == 5


def test_screen_region_by_name_v2_active_returns_none_for_removed() -> None:
    doc = _doc_with_versions()
    res = screen_region_by_name(doc, "old_chest", state_flat={"heroes.norah.level": 9})
    assert res is None
    res2 = screen_region_by_name(doc, "old_chest", state_flat={"heroes.norah.level": 2})
    assert res2 is not None


def test_screen_region_by_name_v2_only_invisible_under_default() -> None:
    doc = _doc_with_versions()
    assert screen_region_by_name(doc, "new_minimap") is None
    res = screen_region_by_name(doc, "new_minimap", state_flat={"heroes.norah.level": 9})
    assert res is not None and res[1]["bbox"]["x"] == 90


def test_region_bbox_for_name_state_aware() -> None:
    doc = _doc_with_versions()
    assert region_bbox_for_name(doc, "promote_btn") == {"x": 10, "y": 10}
    assert region_bbox_for_name(
        doc, "promote_btn", state_flat={"heroes.norah.level": 9}
    ) == {"x": 50, "y": 80}
    assert region_bbox_for_name(
        doc, "old_chest", state_flat={"heroes.norah.level": 9}
    ) is None
