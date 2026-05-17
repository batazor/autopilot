"""Unit tests for scenarios page nested-table helpers."""

from __future__ import annotations

from pathlib import Path

from ui.views import scenarios as mod


def test_folder_node_to_nested_rows_groups_by_path() -> None:
    meta = [
        (Path("a.yaml"), "overlay/a.yaml", "a", "A", {"enabled": True, "steps": []}),
        (Path("b.yaml"), "overlay/b.yaml", "b", "B", {"enabled": False, "steps": [1]}),
        (Path("c.yaml"), "root.yaml", "c", "C", {"enabled": True, "steps": []}),
    ]
    tree = mod._build_folder_tree_from_meta(meta)
    rows = mod._folder_node_to_nested_rows(tree, ())
    assert len(rows) == 2  # overlay folder + root file
    folder = next(r for r in rows if str(r["id"]).startswith("folder:"))
    assert folder["name"] == "overlay/"
    assert folder["selectable"] is False
    assert len(folder["subRows"]) == 2
    child_ids = {str(r["id"]) for r in folder["subRows"]}
    assert child_ids == {"a", "b"}
    root_file = next(r for r in rows if r["id"] == "c")
    assert root_file["selectable"] is True
