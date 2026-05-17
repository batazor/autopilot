"""Tests for Gallery nested-table helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ui.gallery_table import build_gallery_nested_rows, gallery_page_url

if TYPE_CHECKING:
    from pathlib import Path


def test_gallery_page_url_builds_labeling_link(monkeypatch) -> None:
    monkeypatch.setattr(
        "streamlit.context",
        type("C", (), {"url": "http://localhost:8501/gallery?module=core"})(),
        raising=False,
    )
    url = gallery_page_url("labeling", {"ref": "foo.png", "module": "core", "version": "default"})
    assert "labeling" in url
    assert "ref=foo.png" in url
    assert "module=core" in url


def test_build_gallery_nested_rows_groups_by_screen_id(tmp_path: Path) -> None:
    ref_root = tmp_path / "references"
    ref_root.mkdir()
    p1 = ref_root / "a.png"
    p2 = ref_root / "b.png"
    p1.write_bytes(b"x")
    p2.write_bytes(b"y")

    def _slice(_mtime: float, _path: str, rel: str, _mode: str, _prefix: str):
        if rel == "a.png":
            return frozenset({"btn"}), [], "page.a"
        return frozenset(), [], "(unassigned)"

    rows = build_gallery_nested_rows(
        [p1, p2],
        group_by_page=True,
        ref_root=ref_root,
        area_mtime=1.0,
        area_path_str=str(tmp_path / "area.json"),
        area_doc={"screens": []},
        module_key="core",
        references_prefix="references",
        gallery_slice_cached=_slice,
        display_ref_for_card=MagicMock(return_value="a.png"),
        screen_entry_for_ref=MagicMock(return_value=None),
    )
    assert len(rows) == 2
    groups = {str(r["screen_id"]) for r in rows}
    assert "page.a" in groups
    assert "(unassigned)" in groups
    page_a = next(r for r in rows if r["screen_id"] == "page.a")
    assert len(page_a["subRows"]) == 1
    assert page_a["subRows"][0]["id"] == "a.png"
