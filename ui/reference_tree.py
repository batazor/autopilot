"""Reference PNG paths as a directory tree for Streamlit UIs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DirNode:
    files: list[str] = field(default_factory=list)
    dirs: dict[str, DirNode] = field(default_factory=dict)


def build_reference_dir_tree(paths: list[Path], root: Path) -> DirNode:
    """Group PNG paths (absolute or under ``root``) into a directory tree; leaf values are posix paths relative to ``root``."""
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


def dir_node_to_ant_tree_data(node: DirNode) -> list[dict]:
    """
    Build ``treeData`` for `st_ant_tree` (<https://github.com/flucas96/st_ant_tree>).

    Leaf nodes use repo-relative paths under ``references/`` as ``value`` (posix).
    Folder nodes are structural only (``value`` is a stable placeholder; use ``only_children_select``).
    """
    items: list[dict] = []
    for rel in sorted(node.files):
        items.append({"value": rel, "title": Path(rel).name})
    for dirname, child in sorted(node.dirs.items()):
        children = dir_node_to_ant_tree_data(child)
        if not children:
            continue
        items.append(
            {
                # Structural folder; leaf PNG paths are the real ``value``s (see ``only_children_select``).
                "value": f"__dir__/{dirname}",
                "title": f"{dirname}/",
                "children": children,
            }
        )
    return items
