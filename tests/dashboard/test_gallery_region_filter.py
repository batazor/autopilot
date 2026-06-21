"""Gallery region filter semantics (OR): mirror ``ui/views/gallery.py`` loop."""

from __future__ import annotations


def _gallery_keeps_file(*, have: set[str], want_regions: set[str]) -> bool:
    """Return False when file should be skipped."""
    if not want_regions:
        return True
    return bool(want_regions & have)


def test_region_filter_or_any_overlap() -> None:
    assert _gallery_keeps_file(have={"a", "b"}, want_regions={"b", "c"}) is True
    assert _gallery_keeps_file(have={"a"}, want_regions={"b", "c"}) is False


def test_region_filter_empty_selection_keeps_all() -> None:
    assert _gallery_keeps_file(have=set(), want_regions=set()) is True
    assert _gallery_keeps_file(have={"x"}, want_regions=set()) is True


def test_region_filter_single_want() -> None:
    assert _gallery_keeps_file(have={"upgrade_button"}, want_regions={"upgrade_button"}) is True
    assert _gallery_keeps_file(have={"upgrade_building"}, want_regions={"upgrade_button"}) is False
