"""Tests for reference rename → area.yaml sync (module-local ``ocr`` paths)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from ui.reference_area_sync import sync_area_json_ocr_after_reference_rename
from ui.reference_ocr_paths import reference_basename_stem, resolve_ocr_path_in_reference_context

if TYPE_CHECKING:
    from pathlib import Path


def test_reference_basename_stem_multi_dot() -> None:
    assert reference_basename_stem("page.shop.v1.png") == "page.shop.v1"


def test_resolve_module_local_ocr_path(tmp_path: Path) -> None:
    repo = tmp_path
    prefix = "modules/core/shop/references"
    refs = repo / prefix
    refs.mkdir(parents=True)
    png = refs / "page.shop.v1.png"
    png.write_bytes(b"x")

    resolved = resolve_ocr_path_in_reference_context(
        "references/page.shop.v1.png", prefix, repo_root_path=repo
    )
    assert resolved == png.resolve()


def test_sync_area_yaml_after_rename_preserves_format(tmp_path: Path) -> None:
    repo = tmp_path
    mod = repo / "modules/core/shop"
    refs = mod / "references"
    refs.mkdir(parents=True)
    (refs / "page.shop.v1.png").write_bytes(b"x")
    area = mod / "area.yaml"
    area.write_text(
        "version: 2\nscreens:\n"
        "- screen_id: shop\n"
        "  ocr: references/page.shop.v1.png\n"
        "  regions: []\n",
        encoding="utf-8",
    )

    ok, err, n = sync_area_json_ocr_after_reference_rename(
        repo,
        old_rel_under_refs="page.shop.v1.png",
        new_rel_under_refs="page.shop.v2.png",
        area_path=area,
        references_prefix="modules/core/shop/references",
    )
    assert ok and not err and n == 1
    doc = yaml.safe_load(area.read_text(encoding="utf-8"))
    assert doc["screens"][0]["ocr"] == "references/page.shop.v2.png"
    assert area.read_text(encoding="utf-8").lstrip().startswith("version:")
