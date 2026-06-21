"""Tests for layout.reference_basename (Labeling basename rename + MCP)."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from layout.reference_basename import (
    normalize_reference_basename,
    rename_reference_basename,
    resolve_references_context,
    suggest_reference_basename,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_shop_fixture(repo: Path) -> None:
    mod = repo / "games" / "wos" / "core" / "shop"
    refs = mod / "references"
    crop = refs / "crop"
    crop.mkdir(parents=True)
    (refs / "page.shop.v1.png").write_bytes(b"png-v1")
    (crop / "page.shop.v1_title.png").write_bytes(b"crop")
    area = {
        "version": 2,
        "screens": [
            {
                "id": 1,
                "screen_id": "shop.dawn_market",
                "ocr": "references/page.shop.v1.png",
                "regions": [],
            }
        ],
    }
    (mod / "area.yaml").write_text(
        yaml.safe_dump(area, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def test_normalize_reference_basename() -> None:
    assert normalize_reference_basename("page.shop.v2") == "page.shop.v2"
    assert normalize_reference_basename("Page Shop v2!") == "Page_Shop_v2"


def test_resolve_references_context_module(tmp_path: Path) -> None:
    _write_shop_fixture(tmp_path)
    rel = "games/wos/core/shop/references/page.shop.v1.png"
    ctx = resolve_references_context(tmp_path, rel)
    assert ctx.references_prefix == "games/wos/core/shop/references"
    assert ctx.area_path == tmp_path / "games/wos/core/shop/area.yaml"


def test_suggest_reference_basename(tmp_path: Path) -> None:
    _write_shop_fixture(tmp_path)
    out = suggest_reference_basename(
        tmp_path,
        source_repo_rel="games/wos/core/shop/references/page.shop.v1.png",
    )
    assert out["current_basename"] == "page.shop.v1"
    assert out["screen_id"] == "shop.dawn_market"
    assert out["suggested_basename"] == "shop_dawn_market"


def test_rename_reference_basename_syncs_area_and_crops(tmp_path: Path) -> None:
    _write_shop_fixture(tmp_path)
    rel = "games/wos/core/shop/references/page.shop.v1.png"
    out = rename_reference_basename(
        tmp_path,
        source_repo_rel=rel,
        basename="page.shop.v2",
    )
    assert out["ok"] is True
    assert out["new_rel"] == "page.shop.v2.png"
    assert (tmp_path / "games/wos/core/shop/references/page.shop.v2.png").is_file()
    assert not (tmp_path / "games/wos/core/shop/references/page.shop.v1.png").exists()
    assert (tmp_path / "games/wos/core/shop/references/crop/page.shop.v2_title.png").is_file()

    area = yaml.safe_load((tmp_path / "games/wos/core/shop/area.yaml").read_text(encoding="utf-8"))
    assert area["screens"][0]["ocr"] == "references/page.shop.v2.png"


def test_rename_rolls_back_when_area_invalid(tmp_path: Path) -> None:
    _write_shop_fixture(tmp_path)
    area_path = tmp_path / "games/wos/core/shop/area.yaml"
    area_path.write_text(": [invalid yaml\n", encoding="utf-8")
    rel = "games/wos/core/shop/references/page.shop.v1.png"
    out = rename_reference_basename(tmp_path, source_repo_rel=rel, basename="page.shop.v2")
    assert out["ok"] is False
    assert (tmp_path / "games/wos/core/shop/references/page.shop.v1.png").is_file()


def test_rename_rejects_missing_source(tmp_path: Path) -> None:
    _write_shop_fixture(tmp_path)
    with pytest.raises(FileNotFoundError):
        rename_reference_basename(
            tmp_path,
            source_repo_rel="games/wos/core/shop/references/missing.png",
            basename="x",
        )
