from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

import config.module_discovery as module_discovery
import config.paths as paths
import layout.area_manifest as area_manifest
import navigation.screen_graph as screen_graph
from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for

if TYPE_CHECKING:
    from pathlib import Path


def _seed_fake_module(tmp_path: Path, area_doc: dict[str, object]) -> Path:
    """Write a minimal modules/fake/{module,area}.yaml tree under ``tmp_path``."""
    module_dir = _modules_root_for(_default_game(), repo_root=tmp_path) / "fake"
    module_dir.mkdir(parents=True)
    (module_dir / "module.yaml").write_text("id: fake\n", encoding="utf-8")
    (module_dir / "area.yaml").write_text(
        yaml.safe_dump(area_doc, sort_keys=False), encoding="utf-8"
    )
    return module_dir


def test_area_screen_region_adds_screen_landmark(mocker, tmp_path: Path) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text("screens: []\n", encoding="utf-8")
    _seed_fake_module(
        tmp_path,
        {
            "screens": [
                {
                    "screen_id": "reconnect",
                    "screen_region": "icon.reconnect",
                    "regions": [
                        {
                            "name": "icon.reconnect",
                            "threshold": 0.91,
                            "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
                        }
                    ],
                }
            ]
        },
    )
    mocker.patch.object(paths, "repo_root", new=lambda: tmp_path)
    mocker.patch.object(screen_graph, "_screen_verify_yaml_paths", new=lambda: [cfg])
    # Per-hero wiki screens are synthesized from the real heroes index; the
    # test wants to assert the area-region path in isolation, so suppress them.
    mocker.patch.object(screen_graph, "_hero_ids", new=list)
    module_discovery._clear_module_discovery_caches()
    area_manifest.clear_area_doc_cache()
    screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]

    try:
        assert screen_graph.screen_verify_screen_names() == ["reconnect"]
        assert screen_graph.screen_landmark_rules("reconnect") == [
            {"match": "icon.reconnect", "threshold": 0.91}
        ]
    finally:
        screen_graph.load_screen_verify_config.cache_clear()  # ty: ignore[unresolved-attribute]
        module_discovery._clear_module_discovery_caches()
        area_manifest.clear_area_doc_cache()
