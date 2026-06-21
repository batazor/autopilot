"""Merged analyze YAML and area manifests cache on mtime fingerprints."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import yaml

from analysis.overlay_manifest import (
    analyze_manifests_fingerprint,
    clear_merged_analyze_yaml_cache,
    load_merged_analyze_yaml,
)
from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for
from layout.area_manifest import clear_area_doc_cache, load_area_doc

if TYPE_CHECKING:
    from pathlib import Path


def test_load_merged_analyze_yaml_cache_hits_until_mtime_changes(tmp_path: Path) -> None:
    clear_merged_analyze_yaml_cache()
    module_dir = _modules_root_for(_default_game(), repo_root=tmp_path) / "cache_test"
    (module_dir / "analyze").mkdir(parents=True)
    (module_dir / "module.yaml").write_text("id: cache_test\n", encoding="utf-8")
    manifest = module_dir / "analyze" / "analyze.yaml"
    manifest.write_text(
        yaml.safe_dump({"overlay": [{"name": "first.rule", "region": "x", "screens": ["x"]}]}),
        encoding="utf-8",
    )

    from analysis.overlay_manifest import _load_merged_analyze_yaml_cached

    first = load_merged_analyze_yaml(tmp_path)
    assert [r["name"] for r in first["overlay"]] == ["first.rule"]
    assert _load_merged_analyze_yaml_cached.cache_info().hits == 0

    second = load_merged_analyze_yaml(tmp_path)
    assert second is first
    assert _load_merged_analyze_yaml_cached.cache_info().hits == 1

    time.sleep(0.02)
    manifest.write_text(
        yaml.safe_dump({"overlay": [{"name": "second.rule", "region": "y", "screens": ["y"]}]}),
        encoding="utf-8",
    )
    third = load_merged_analyze_yaml(tmp_path)
    assert third is not first
    assert [r["name"] for r in third["overlay"]] == ["second.rule"]


def test_load_merged_analyze_yaml_cache_invalidates_when_include_changes(
    tmp_path: Path,
) -> None:
    """Editing ``include:`` targets must bust cache without touching parent analyze.yaml."""
    clear_merged_analyze_yaml_cache()
    module_dir = _modules_root_for(_default_game(), repo_root=tmp_path) / "include_cache_test"
    analyze_dir = module_dir / "analyze"
    pages_dir = analyze_dir / "pages"
    pages_dir.mkdir(parents=True)
    (module_dir / "module.yaml").write_text("id: include_cache_test\n", encoding="utf-8")
    manifest = analyze_dir / "analyze.yaml"
    page = pages_dir / "extra.yaml"
    manifest.write_text(
        yaml.safe_dump({"include": ["pages/extra.yaml"]}),
        encoding="utf-8",
    )
    page.write_text(
        yaml.safe_dump({"overlay": [{"name": "from.page", "region": "x", "screens": ["x"]}]}),
        encoding="utf-8",
    )

    first = load_merged_analyze_yaml(tmp_path)
    assert [r["name"] for r in first["overlay"]] == ["from.page"]
    fp_before = analyze_manifests_fingerprint(tmp_path)

    second = load_merged_analyze_yaml(tmp_path)
    assert second is first
    assert analyze_manifests_fingerprint(tmp_path) == fp_before

    page.write_text(
        yaml.safe_dump({"overlay": [{"name": "from.page.v2", "region": "y", "screens": ["y"]}]}),
        encoding="utf-8",
    )
    assert analyze_manifests_fingerprint(tmp_path) != fp_before

    third = load_merged_analyze_yaml(tmp_path)
    assert third is not first
    assert [r["name"] for r in third["overlay"]] == ["from.page.v2"]


def test_load_area_doc_cache_hits_until_mtime_changes(tmp_path: Path) -> None:
    clear_area_doc_cache()
    module_dir = _modules_root_for(_default_game(), repo_root=tmp_path) / "area_cache_test"
    module_dir.mkdir(parents=True)
    (module_dir / "module.yaml").write_text("id: area_cache_test\n", encoding="utf-8")
    area_yaml = module_dir / "area.yaml"
    area_yaml.write_text(
        yaml.safe_dump(
            {"screens": [{"screen_id": "s1", "regions": [{"name": "r1"}]}]}
        ),
        encoding="utf-8",
    )

    from config.module_discovery import _clear_module_discovery_caches
    from layout.area_manifest import _load_area_doc_cached

    _clear_module_discovery_caches()
    first = load_area_doc(tmp_path)
    assert len(first["screens"]) == 1
    assert _load_area_doc_cached.cache_info().hits == 0

    second = load_area_doc(tmp_path)
    assert second is first
    assert _load_area_doc_cached.cache_info().hits == 1

    time.sleep(0.02)
    area_yaml.write_text(
        yaml.safe_dump(
            {"screens": [{"screen_id": "s1"}, {"screen_id": "s2"}]}
        ),
        encoding="utf-8",
    )
    third = load_area_doc(tmp_path)
    assert third is not first
    assert len(third["screens"]) == 2


def test_load_area_doc_cache_invalidates_when_non_max_manifest_changes(
    tmp_path: Path,
) -> None:
    clear_area_doc_cache()
    modules_root = _modules_root_for(_default_game(), repo_root=tmp_path)
    older_dir = modules_root / "older_area"
    newer_dir = modules_root / "newer_area"
    older_dir.mkdir(parents=True)
    newer_dir.mkdir(parents=True)
    (older_dir / "module.yaml").write_text("id: older_area\n", encoding="utf-8")
    (newer_dir / "module.yaml").write_text("id: newer_area\n", encoding="utf-8")
    older_area = older_dir / "area.yaml"
    newer_area = newer_dir / "area.yaml"
    older_area.write_text(
        yaml.safe_dump({"screens": [{"screen_id": "older.v1"}]}),
        encoding="utf-8",
    )
    newer_area.write_text(
        yaml.safe_dump({"screens": [{"screen_id": "newer"}]}),
        encoding="utf-8",
    )

    from config.module_discovery import _clear_module_discovery_caches

    _clear_module_discovery_caches()
    first = load_area_doc(tmp_path)
    assert {s["screen_id"] for s in first["screens"]} == {"older.v1", "newer"}

    newer_mtime = newer_area.stat().st_mtime
    older_area.write_text(
        yaml.safe_dump({"screens": [{"screen_id": "older.v2"}]}),
        encoding="utf-8",
    )
    os.utime(older_area, (newer_mtime - 1, newer_mtime - 1))

    second = load_area_doc(tmp_path)
    assert second is not first
    assert {s["screen_id"] for s in second["screens"]} == {"older.v2", "newer"}
