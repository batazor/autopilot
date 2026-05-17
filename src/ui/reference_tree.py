"""Reference PNG paths as a directory tree for Streamlit UIs."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ui.labeling_helpers import (
    ReferenceLeafMeta,
    build_reference_leaf_meta_index,
    format_reference_leaf_title,
    format_screen_id_group_title,
)


@dataclass
class DirNode:
    files: list[str] = field(default_factory=list)
    dirs: dict[str, DirNode] = field(default_factory=dict)


def build_reference_dir_tree(paths: list[Path], root: Path) -> DirNode:
    """Group PNG paths (absolute or under ``root``) into a directory tree.

    Leaf values are posix paths relative to ``root``.
    """
    tree = DirNode()

    for p in paths:
        try:
            rel = p.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        if rel.suffix.lower() != ".png":
            continue
        parts = rel.parts
        node = tree
        if len(parts) == 1:
            node.files.append(rel.as_posix())
            continue
        for part in parts[:-1]:
            node.dirs.setdefault(part, DirNode())
            node = node.dirs[part]
        node.files.append(rel.as_posix())

    _sort_dir_node(tree)
    return tree


def _sort_dir_node(node: DirNode) -> None:
    node.files.sort()
    for child in sorted(node.dirs.keys()):
        _sort_dir_node(node.dirs[child])


def _leaf_title(rel: str, meta_by_rel: dict[str, ReferenceLeafMeta] | None) -> str:
    if meta_by_rel is None:
        return Path(rel).name
    return format_reference_leaf_title(rel, meta_by_rel.get(rel))


def dir_node_to_ant_tree_data(
    node: DirNode,
    meta_by_rel: dict[str, ReferenceLeafMeta] | None = None,
) -> list[dict]:
    """
    Build ``treeData`` for `st_ant_tree` (<https://github.com/flucas96/st_ant_tree>).

    Leaf nodes use repo-relative paths under ``references/`` as ``value`` (posix).
    Folder nodes are structural only (``value`` is a stable placeholder; use ``only_children_select``).
    """
    items: list[dict] = [
        {"value": rel, "title": _leaf_title(rel, meta_by_rel)}
        for rel in sorted(node.files)
    ]
    for dirname, child in sorted(node.dirs.items()):
        children = dir_node_to_ant_tree_data(child, meta_by_rel)
        if not children:
            continue
        items.append(
            {
                "value": f"__dir__/{dirname}",
                "title": f"{dirname}/",
                "children": children,
            }
        )
    return items


def build_reference_screen_id_tree_data(
    paths: list[Path],
    root: Path,
    area_doc: dict[str, Any] | None,
    *,
    unassigned_title: str = "(unassigned)",
) -> list[dict]:
    """Build `st_ant_tree` `treeData` grouped by `area.json` screen_id.

    Leaf node `value` is the posix path relative to `root` (same as `build_reference_dir_tree`).
    Group nodes use `__sid__/...` structural values.
    """
    meta_by_rel = build_reference_leaf_meta_index(area_doc, root, unassigned_title=unassigned_title)

    # Map relative path -> screen_id for grouping.
    by_rel_sid: dict[str, str] = {}
    for rel, meta in meta_by_rel.items():
        by_rel_sid[rel] = meta.screen_id.strip() or unassigned_title

    groups: dict[str, list[str]] = {}
    for p in paths:
        try:
            rel = p.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        if rel.suffix.lower() != ".png":
            continue
        rel_posix = rel.as_posix()
        sid = by_rel_sid.get(rel_posix, "").strip() or unassigned_title
        if rel_posix not in meta_by_rel:
            meta_by_rel[rel_posix] = ReferenceLeafMeta(
                rel=rel_posix,
                screen_id="",
                region_count=0,
                active_version=None,
                unassigned=True,
            )
        groups.setdefault(sid, []).append(rel_posix)

    out: list[dict] = []
    for sid in sorted(groups.keys()):
        files = sorted(groups[sid])
        children = [
            {"value": rel, "title": _leaf_title(rel, meta_by_rel)} for rel in files
        ]
        out.append(
            {
                "value": f"__sid__/{sid}",
                "title": format_screen_id_group_title(sid, len(files), unassigned_title=unassigned_title),
                "children": children,
            }
        )
    return out


def temporal_capture_tree_node(rel_posix: str) -> dict:
    """Single leaf for a pending capture under ``references/temporal/``."""
    name = Path(rel_posix).name
    return {
        "value": rel_posix,
        "title": f"⏳ {name} · pending publish",
    }
