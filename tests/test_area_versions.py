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
    resolve_region_with_version,
    split_versioned_name,
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
    # `True`, `False`, `not in` etc. shouldn't get mangled by the dotted-ident regex.
    assert eval_cond("True", {}) is True
    assert eval_cond("not False", {}) is True


def test_eval_cond_bare_identifier_uses_state() -> None:
    # Single-segment names aren't dotted, so they're resolved via the eval namespace.
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


def _entry_with_regions(*names: str) -> dict:
    return {
        "regions": [{"name": n, "bbox": {"x": i, "y": i}} for i, n in enumerate(names)]
    }


def test_resolve_default_returns_unsuffixed() -> None:
    entry = _entry_with_regions("promote_btn", "promote_btn_v2")
    reg = resolve_region_with_version(entry, "promote_btn", None)
    assert reg is not None
    assert reg["name"] == "promote_btn"


def test_resolve_v2_returns_suffixed_when_present() -> None:
    entry = _entry_with_regions("promote_btn", "promote_btn_v2")
    reg = resolve_region_with_version(entry, "promote_btn", "v2")
    assert reg is not None
    assert reg["name"] == "promote_btn_v2"


def test_resolve_v2_falls_back_to_default_when_override_missing() -> None:
    entry = _entry_with_regions("promote_btn", "level_label")
    reg = resolve_region_with_version(entry, "promote_btn", "v2")
    assert reg is not None
    assert reg["name"] == "promote_btn"


def test_resolve_unknown_name_returns_none() -> None:
    entry = _entry_with_regions("a", "b")
    assert resolve_region_with_version(entry, "missing", "v2") is None


def test_effective_ocr_for_versioned_region() -> None:
    entry = {
        "ocr": "references/main_city.png",
        "versions": [{"id": "v2", "cond": "True", "ocr": "references/main_city_v2.png"}],
    }
    assert (
        effective_ocr_for_region(entry, {"name": "is_new_chapter_v2"})
        == "references/main_city_v2.png"
    )


def test_effective_ocr_for_default_region() -> None:
    entry = {
        "ocr": "references/main_city.png",
        "versions": [{"id": "v2", "cond": "True", "ocr": "references/main_city_v2.png"}],
    }
    assert (
        effective_ocr_for_region(entry, {"name": "is_new_chapter"})
        == "references/main_city.png"
    )


# ---- split_versioned_name -------------------------------------------------


def test_split_versioned_name_known_version() -> None:
    assert split_versioned_name("promote_btn_v2", {"v2", "v3"}) == ("promote_btn", "v2")


def test_split_versioned_name_unknown_version_left_alone() -> None:
    assert split_versioned_name("promote_btn_v9", {"v2"}) == ("promote_btn_v9", None)


def test_split_versioned_name_no_suffix() -> None:
    assert split_versioned_name("promote_btn", {"v2"}) == ("promote_btn", None)


# ---- validate_versions ----------------------------------------------------


def _doc(entry: dict) -> dict:
    return {"version": 2, "screens": [entry]}


def test_validate_versions_accepts_well_formed() -> None:
    doc = _doc(
        {
            "id": 1,
            "screen_id": "hero_card",
            "versions": [{"id": "v2", "cond": "heroes.norah.level >= 6"}],
            "regions": [
                {"name": "promote_btn", "bbox": {}},
                {"name": "promote_btn_v2", "bbox": {}},
            ],
        }
    )
    validate_versions(doc)  # should not raise


def test_validate_versions_rejects_bad_id_format() -> None:
    doc = _doc(
        {"id": 1, "versions": [{"id": "version2", "cond": "True"}], "regions": []}
    )
    with pytest.raises(ValueError, match=r"^v\\\\d\+\$|version id"):
        validate_versions(doc)


def test_validate_versions_rejects_duplicate_ids() -> None:
    doc = _doc(
        {
            "id": 1,
            "versions": [
                {"id": "v2", "cond": "True"},
                {"id": "v2", "cond": "False"},
            ],
            "regions": [],
        }
    )
    with pytest.raises(ValueError, match="duplicate version"):
        validate_versions(doc)


def test_validate_versions_rejects_orphan_suffix() -> None:
    doc = _doc(
        {
            "id": 1,
            "versions": [{"id": "v2", "cond": "True"}],
            "regions": [
                {"name": "promote_btn_v9", "bbox": {}},  # v9 not declared
            ],
        }
    )
    with pytest.raises(ValueError, match="version suffix"):
        validate_versions(doc)


def test_validate_versions_rejects_empty_cond() -> None:
    doc = _doc({"id": 1, "versions": [{"id": "v2", "cond": ""}], "regions": []})
    with pytest.raises(ValueError, match="empty 'cond'"):
        validate_versions(doc)


def test_validate_versions_rejects_cond_syntax_error() -> None:
    doc = _doc(
        {"id": 1, "versions": [{"id": "v2", "cond": "a >>= b"}], "regions": []}
    )
    with pytest.raises(ValueError, match="syntax error"):
        validate_versions(doc)


# ---- screen_region_by_name + region_bbox_for_name (state-aware) -----------


def _doc_with_versions() -> dict:
    return {
        "version": 2,
        "screens": [
            {
                "id": 1,
                "screen_id": "hero_card",
                "versions": [{"id": "v2", "cond": "heroes.norah.level >= 6"}],
                "regions": [
                    {"name": "promote_btn", "bbox": {"x": 10, "y": 10}},
                    {"name": "promote_btn_v2", "bbox": {"x": 50, "y": 80}},
                    {"name": "level_label", "bbox": {"x": 5, "y": 5}},
                ],
            }
        ],
    }


def test_screen_region_by_name_default_when_state_none() -> None:
    doc = _doc_with_versions()
    res = screen_region_by_name(doc, "promote_btn")
    assert res is not None and res[1]["name"] == "promote_btn"


def test_screen_region_by_name_picks_v2_under_matching_state() -> None:
    doc = _doc_with_versions()
    res = screen_region_by_name(doc, "promote_btn", state_flat={"heroes.norah.level": 9})
    assert res is not None and res[1]["name"] == "promote_btn_v2"


def test_screen_region_by_name_picks_default_under_non_matching_state() -> None:
    doc = _doc_with_versions()
    res = screen_region_by_name(doc, "promote_btn", state_flat={"heroes.norah.level": 2})
    assert res is not None and res[1]["name"] == "promote_btn"


def test_screen_region_by_name_v2_active_falls_back_for_unmoved_region() -> None:
    doc = _doc_with_versions()
    res = screen_region_by_name(doc, "level_label", state_flat={"heroes.norah.level": 9})
    assert res is not None and res[1]["name"] == "level_label"


def test_region_bbox_for_name_state_aware() -> None:
    doc = _doc_with_versions()
    bbox_default = region_bbox_for_name(doc, "promote_btn")
    bbox_v2 = region_bbox_for_name(doc, "promote_btn", state_flat={"heroes.norah.level": 9})
    assert bbox_default == {"x": 10, "y": 10}
    assert bbox_v2 == {"x": 50, "y": 80}
