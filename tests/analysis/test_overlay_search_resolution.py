"""Implicit ``{region}_search`` resolution for overlay ``findIcon`` rules."""

from __future__ import annotations

from analysis.overlay_rules import resolved_search_region_for_findicon

_BB = {"x": 1.0, "y": 1.0, "width": 10.0, "height": 10.0}
_BB_SEARCH = {"x": 0.0, "y": 0.0, "width": 50.0, "height": 50.0}


def test_implicit_search_aux_region_is_ignored_without_explicit_search_region() -> None:
    """Documents the post-refactor behaviour: a sibling ``{region}_search`` on
    the same screen no longer auto-resolves. Movable primary regions opt in via
    ``isSearch: true`` on the area entry; everything else takes the explicit
    ``rule['search_region']`` only.
    """
    doc = {
        "screens": [
            {
                "ocr": "references/a.png",
                "regions": [
                    {"name": "btn", "bbox": _BB},
                    {"name": "btn_search", "bbox": _BB_SEARCH},
                ],
            }
        ]
    }
    rule: dict = {"action": "findIcon", "region": "btn"}
    assert resolved_search_region_for_findicon(doc, "btn", "references/a.png", rule) == ""


def test_explicit_search_region_wins() -> None:
    doc = {
        "screens": [
            {
                "ocr": "references/a.png",
                "regions": [
                    {"name": "btn", "bbox": _BB},
                    {"name": "btn_search", "bbox": _BB_SEARCH},
                    {"name": "custom_roi", "bbox": _BB_SEARCH},
                ],
            }
        ]
    }
    rule = {"search_region": "custom_roi"}
    assert resolved_search_region_for_findicon(doc, "btn", "references/a.png", rule) == "custom_roi"


def test_empty_when_no_aux_region() -> None:
    doc = {
        "screens": [
            {
                "ocr": "references/a.png",
                "regions": [{"name": "btn", "bbox": _BB}],
            }
        ]
    }
    assert resolved_search_region_for_findicon(doc, "btn", "references/a.png", {}) == ""


def test_rejects_aux_defined_on_other_reference_frame() -> None:
    doc = {
        "screens": [
            {
                "ocr": "references/a.png",
                "regions": [{"name": "btn", "bbox": _BB}],
            },
            {
                "ocr": "references/b.png",
                "regions": [{"name": "btn_search", "bbox": _BB_SEARCH}],
            },
        ]
    }
    assert resolved_search_region_for_findicon(doc, "btn", "references/a.png", {}) == ""
