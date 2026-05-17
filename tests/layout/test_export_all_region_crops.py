"""Bulk crop export for every ``area.json`` screen with a reference PNG."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from ui.area_annotator import (
    crop_path_for_entry_region,
    export_all_region_crops_for_area_doc,
)


def test_export_all_writes_one_crop_per_screen(tmp_path: Path) -> None:
    ref_a = tmp_path / "references"
    ref_a.mkdir(parents=True)
    crop_dir = ref_a / "crop"
    img_a = Image.fromarray(np.zeros((128, 64, 3), dtype=np.uint8))
    img_a.save(ref_a / "one.png")
    img_b = Image.fromarray(np.ones((128, 64, 3), dtype=np.uint8) * 50)
    img_b.save(ref_a / "two.png")

    doc = {
        "version": 2,
        "fsm": {"initial_screen": "", "transitions": []},
        "screens": [
            {
                "id": 1,
                "ocr": "references/one.png",
                "regions": [
                    {
                        "name": "r1",
                        "action": "exist",
                        "type": "string",
                        "threshold": 0.9,
                        "bbox": {
                            "x": 0.0,
                            "y": 0.0,
                            "width": 50.0,
                            "height": 50.0,
                            "rotation": 0.0,
                            "original_width": 64,
                            "original_height": 128,
                        },
                    },
                    {
                        "name": "r1_search",
                        "action": "exist",
                        "overlay_auxiliary": True,
                        "bbox": {
                            "x": 0.0,
                            "y": 0.0,
                            "width": 100.0,
                            "height": 100.0,
                            "rotation": 0.0,
                            "original_width": 64,
                            "original_height": 128,
                        },
                    },
                ],
            },
            {
                "id": 2,
                "ocr": "references/two.png",
                "regions": [
                    {
                        "name": "r2",
                        "action": "exist",
                        "type": "string",
                        "threshold": 0.9,
                        "bbox": {
                            "x": 10.0,
                            "y": 10.0,
                            "width": 30.0,
                            "height": 30.0,
                            "rotation": 0.0,
                            "original_width": 64,
                            "original_height": 128,
                        },
                    }
                ],
            },
            {
                "id": 3,
                "ocr": "references/missing.png",
                "regions": [
                    {
                        "name": "gone",
                        "action": "exist",
                        "bbox": {
                            "x": 0.0,
                            "y": 0.0,
                            "width": 10.0,
                            "height": 10.0,
                            "rotation": 0.0,
                            "original_width": 64,
                            "original_height": 128,
                        },
                    }
                ],
            },
        ],
    }

    written, warns = export_all_region_crops_for_area_doc(doc, repo_root=tmp_path)  # ty: ignore[invalid-argument-type]
    assert len(written) == 2
    assert (crop_dir / "one_r1.png").is_file()
    assert (crop_dir / "two_r2.png").is_file()
    assert any("missing.png" in w for w in warns)


def test_crop_path_uses_default_ocr_stem_for_base_region(tmp_path: Path) -> None:
    entry = {
        "ocr": "references/main_city_v1.png",
        "regions": [{"name": "mailBox", "bbox": {}}],
    }

    p = crop_path_for_entry_region(tmp_path, entry, "mailBox")  # ty: ignore[invalid-argument-type]

    assert p == tmp_path / "references" / "crop" / "main_city_v1_mailBox.png"


def test_crop_path_picks_version_ocr_for_version_region(tmp_path: Path) -> None:
    entry = {
        "ocr": "references/main_city_v1.png",
        "regions": [],
        "versions": [
            {
                "id": "v2",
                "cond": "True",
                "ocr": "references/main_city_v2.png",
                "regions": [{"name": "mailBox", "bbox": {}}],
            }
        ],
    }

    p = crop_path_for_entry_region(tmp_path, entry, "mailBox", active_version="v2")  # ty: ignore[invalid-argument-type]

    assert p == tmp_path / "references" / "crop" / "main_city_v2_mailBox.png"


def test_crop_path_falls_back_to_default_ocr_when_version_missing_image(tmp_path: Path) -> None:
    entry = {
        "ocr": "references/main_city_v1.png",
        "regions": [],
        "versions": [
            {
                "id": "v2",
                "cond": "True",
                "regions": [{"name": "mailBox", "bbox": {}}],
            }
        ],
    }

    p = crop_path_for_entry_region(tmp_path, entry, "mailBox", active_version="v2")  # ty: ignore[invalid-argument-type]

    assert p == tmp_path / "references" / "crop" / "main_city_v1_mailBox.png"


def test_crop_path_returns_none_for_unknown_region(tmp_path: Path) -> None:
    assert crop_path_for_entry_region(tmp_path, {}, "mailBox") is None
    assert crop_path_for_entry_region(tmp_path, None, "mailBox") is None
    assert (
        crop_path_for_entry_region(
            tmp_path, {"ocr": "ref.png", "regions": [{"name": "x"}]}, ""
        )
        is None
    )
    # Region not found in any block.
    assert (
        crop_path_for_entry_region(
            tmp_path, {"ocr": "ref.png", "regions": [{"name": "x"}]}, "missing"
        )
        is None
    )
