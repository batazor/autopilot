"""Merged analyze YAML and area manifests cache on mtime fingerprints."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import yaml

from analysis.overlay_manifest import (
    analyze_manifests_fingerprint,
    clear_merged_analyze_yaml_cache,
    load_merged_analyze_yaml,
)
from layout.area_manifest import clear_area_doc_cache, load_area_doc

if TYPE_CHECKING:
    from pathlib import Path


def test_load_merged_analyze_yaml_cache_hits_until_mtime_changes(tmp_path: Path) -> None:
    clear_merged_analyze_yaml_cache()
    module_dir = tmp_path / "modules" / "cache_test"
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
    module_dir = tmp_path / "modules" / "include_cache_test"
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
    area_json = tmp_path / "area.json"
    area_json.write_text(
        '{"screens": [{"screen_id": "s1", "regions": [{"name": "r1"}]}]}',
        encoding="utf-8",
    )

    from layout.area_manifest import _load_area_doc_cached

    first = load_area_doc(tmp_path)
    assert len(first["screens"]) == 1
    assert _load_area_doc_cached.cache_info().hits == 0

    second = load_area_doc(tmp_path)
    assert second is first
    assert _load_area_doc_cached.cache_info().hits == 1

    time.sleep(0.02)
    area_json.write_text(
        '{"screens": [{"screen_id": "s1"}, {"screen_id": "s2"}]}',
        encoding="utf-8",
    )
    third = load_area_doc(tmp_path)
    assert third is not first
    assert len(third["screens"]) == 2
