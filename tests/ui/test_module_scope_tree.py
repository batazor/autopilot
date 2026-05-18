"""Pure-Python coverage for :func:`ui.module_scope.build_module_scope_tree`.

Streamlit / ``st_ant_tree`` rendering is exercised through the UI; here we
only assert the data the tree component is fed, since that's where the
hierarchy logic lives.
"""
from __future__ import annotations

from ui.module_scope import _DIR_PREFIX, _pick_scope, build_module_scope_tree


def test_all_and_core_stay_top_level() -> None:
    """``all`` / ``core`` never get grouped under a namespace bucket."""
    tree = build_module_scope_tree([("all", "All"), ("core", "Core")])
    titles = [n["title"] for n in tree]
    assert titles == ["All", "Core"]
    # No directory placeholders should appear.
    assert all(not str(n.get("value", "")).startswith(_DIR_PREFIX) for n in tree)


def test_slashed_modules_collapse_under_namespace() -> None:
    """``core/X`` + ``core/Y`` collapse to one ``core/`` group with two leaves."""
    tree = build_module_scope_tree(
        [
            ("all", "All"),
            ("core", "Core"),
            ("core/heroes", "Heroes"),
            ("core/chief_profile", "Chief profile"),
            ("events/trials", "Trials"),
        ]
    )
    # Top-level leaves first, then alphabetised groups: core/, events/.
    top_titles = [n["title"] for n in tree if "children" not in n]
    assert top_titles == ["All", "Core"]

    groups = [n for n in tree if "children" in n]
    assert [g["title"] for g in groups] == ["core/", "events/"]
    assert [g["value"] for g in groups] == [
        f"{_DIR_PREFIX}core",
        f"{_DIR_PREFIX}events",
    ]
    # Group node itself is not selectable.
    assert all(g.get("selectable") is False for g in groups)

    # Children carry the full storage_key as their value (callers map back to scope).
    core_children = groups[0]["children"]
    assert {c["value"] for c in core_children} == {
        "core/heroes",
        "core/chief_profile",
    }
    # Children are alphabetised by title for a stable UI ordering.
    assert [c["title"] for c in core_children] == ["Chief profile", "Heroes"]


def test_slashless_top_level_module_stays_flat() -> None:
    """A module with no namespace prefix (``vip``) renders as a top-level leaf."""
    tree = build_module_scope_tree([("all", "All"), ("vip", "VIP")])
    assert [n["title"] for n in tree] == ["All", "VIP"]
    assert all("children" not in n for n in tree)


def test_pick_scope_unwraps_list_form() -> None:
    """``st_ant_tree`` may return a single-element list — coerce to the bare value."""
    got = _pick_scope(
        ["core/heroes"], fallback="all", valid_keys={"all", "core/heroes"}
    )
    assert got == "core/heroes"


def test_pick_scope_ignores_directory_placeholder() -> None:
    """Clicking a group header doesn't change the scope — fall back to current."""
    got = _pick_scope(
        f"{_DIR_PREFIX}core", fallback="core/heroes", valid_keys={"core/heroes"}
    )
    assert got == "core/heroes"


def test_pick_scope_rejects_unknown_key() -> None:
    """Stale defaults / typos fall back instead of breaking downstream consumers."""
    got = _pick_scope("legacy/ghost", fallback="all", valid_keys={"all", "core"})
    assert got == "all"
