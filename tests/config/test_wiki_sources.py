"""Merge semantics for :mod:`config.wiki_sources`.

Each test stages an isolated fake repo (``db/<entity>/`` ± ``modules/<id>/wiki/``)
under ``tmp_path`` and calls ``load_merged_entries`` with ``repo_root=tmp_path``
to keep the real project tree out of the assertions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for
from config.wiki_sources import CORE_SOURCE, find_entry, load_merged_entries

if TYPE_CHECKING:
    from pathlib import Path


def _write_yaml(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _seed_core_items(root: Path) -> None:
    _write_yaml(
        root / "db" / "items" / "index.yaml",
        {
            "items": [
                {"id": "vip_points", "name": "VIP Points", "file": "vip_points.yaml"},
                {"id": "speed_up_1h", "name": "Speedup 1h", "file": "speed_up_1h.yaml"},
            ]
        },
    )
    _write_yaml(root / "db" / "items" / "vip_points.yaml", {"id": "vip_points", "name": "VIP Points (core)"})
    _write_yaml(root / "db" / "items" / "speed_up_1h.yaml", {"id": "speed_up_1h", "name": "Speedup 1h"})


def _seed_module(root: Path, module_id: str, items_index: list[dict[str, Any]]) -> Path:
    module_dir = root / "games" / "wos" / module_id
    (module_dir / "module.yaml").parent.mkdir(parents=True, exist_ok=True)
    (module_dir / "module.yaml").write_text(f"id: {module_id}\ntitle: {module_id.title()}\n", encoding="utf-8")
    _write_yaml(module_dir / "wiki" / "items" / "index.yaml", {"items": items_index})
    return module_dir


def test_load_merged_entries_returns_core_only_when_no_modules(tmp_path: Path) -> None:
    _seed_core_items(tmp_path)

    rows = load_merged_entries("items", repo_root=tmp_path)

    assert [r.id for r in rows] == ["vip_points", "speed_up_1h"]
    assert all(r.source == CORE_SOURCE for r in rows)
    assert rows[0].yaml_path == (tmp_path / "db" / "items" / "vip_points.yaml").resolve()


def test_module_only_entry_is_appended_with_module_provenance(tmp_path: Path) -> None:
    _seed_core_items(tmp_path)
    module_dir = _seed_module(
        tmp_path,
        "vip",
        [{"id": "vip_chest", "name": "VIP Weekly Chest", "file": "vip_chest.yaml"}],
    )
    _write_yaml(module_dir / "wiki" / "items" / "vip_chest.yaml", {"id": "vip_chest", "name": "VIP Weekly Chest"})

    rows = load_merged_entries("items", repo_root=tmp_path)

    assert [r.id for r in rows] == ["vip_points", "speed_up_1h", "vip_chest"]
    chest = rows[-1]
    assert chest.source == "vip"
    assert chest.yaml_path == (module_dir / "wiki" / "items" / "vip_chest.yaml").resolve()


def test_module_entry_overrides_core_and_keeps_original_slot(tmp_path: Path) -> None:
    _seed_core_items(tmp_path)
    module_dir = _seed_module(
        tmp_path,
        "vip",
        [{"id": "vip_points", "name": "VIP Points (vip module override)", "file": "vip_points.yaml"}],
    )
    _write_yaml(
        module_dir / "wiki" / "items" / "vip_points.yaml",
        {"id": "vip_points", "name": "VIP Points (vip module override)"},
    )

    rows = load_merged_entries("items", repo_root=tmp_path)

    # The override keeps the index position of the core row — overrides should
    # not shuffle existing layout — while flipping source/yaml_path.
    assert [r.id for r in rows] == ["vip_points", "speed_up_1h"]
    overridden = rows[0]
    assert overridden.source == "vip"
    assert overridden.name == "VIP Points (vip module override)"
    assert overridden.yaml_path == (module_dir / "wiki" / "items" / "vip_points.yaml").resolve()


def test_find_entry_locates_module_owned_row(tmp_path: Path) -> None:
    _seed_core_items(tmp_path)
    module_dir = _seed_module(
        tmp_path,
        "exploration",
        [{"id": "exploration_chest", "name": "Exploration Chest"}],
    )
    _write_yaml(
        module_dir / "wiki" / "items" / "exploration_chest.yaml",
        {"id": "exploration_chest", "name": "Exploration Chest"},
    )

    hit = find_entry("items", "exploration_chest", repo_root=tmp_path)
    assert hit is not None
    assert hit.source == "exploration"
    assert hit.yaml_path == (module_dir / "wiki" / "items" / "exploration_chest.yaml").resolve()


def test_module_without_module_yaml_is_ignored(tmp_path: Path) -> None:
    _seed_core_items(tmp_path)
    # Bare modules/<id>/wiki/items dir with no module.yaml shouldn't be merged —
    # module.yaml is the gate that marks a directory as a real module.
    _write_yaml(
        _modules_root_for(_default_game(), repo_root=tmp_path) / "stray" / "wiki" / "items" / "index.yaml",
        {"items": [{"id": "stray_item", "name": "Stray"}]},
    )

    rows = load_merged_entries("items", repo_root=tmp_path)
    assert [r.id for r in rows] == ["vip_points", "speed_up_1h"]


def test_module_assets_icon_resolves_under_module(tmp_path: Path) -> None:
    _seed_core_items(tmp_path)
    module_dir = _seed_module(
        tmp_path,
        "vip",
        [{"id": "vip_chest", "name": "VIP Chest", "file": "vip_chest.yaml"}],
    )
    _write_yaml(module_dir / "wiki" / "items" / "vip_chest.yaml", {"id": "vip_chest", "name": "VIP Chest"})
    icon = module_dir / "wiki" / "items" / "assets" / "vip_chest" / "icon.png"
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal valid PNG header is enough for path resolution

    chest = find_entry("items", "vip_chest", repo_root=tmp_path)
    assert chest is not None
    assert chest.icon_path == icon
