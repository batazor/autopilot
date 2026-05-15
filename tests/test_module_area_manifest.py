from __future__ import annotations

import json
from pathlib import Path

import yaml

from layout.area_manifest import load_area_doc
from layout.crop_paths import exported_crop_png, resolve_reference_path


def test_default_area_doc_includes_module_area_manifests(tmp_path: Path) -> None:
    (tmp_path / "area.json").write_text(
        json.dumps(
            {
                "screens": [
                    {
                        "id": "core",
                        "ocr": "references/core.png",
                        "regions": [{"name": "core.button", "bbox": {}}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    module_root = tmp_path / "modules" / "vip"
    module_root.mkdir(parents=True)
    (module_root / "module.yaml").write_text(
        yaml.safe_dump({"id": "vip", "name": "VIP"}),
        encoding="utf-8",
    )
    (module_root / "area.yaml").write_text(
        yaml.safe_dump(
            {
                "screens": [
                    {
                        "id": "vip",
                        "ocr": "references/page.vip.png",
                        "versions": [{"id": "v2", "ocr": "references/page.vip.v2.png"}],
                        "regions": [{"name": "vip.claim", "bbox": {}}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    doc = load_area_doc(tmp_path)

    screens = doc["screens"]
    assert [screen["id"] for screen in screens] == ["core", "vip"]
    assert screens[1]["ocr"] == "modules/vip/references/page.vip.png"
    assert screens[1]["versions"][0]["ocr"] == "modules/vip/references/page.vip.v2.png"


def test_module_reference_uses_module_crop_directory(tmp_path: Path) -> None:
    ref_rel = "modules/vip/references/page.vip.png"

    crop = exported_crop_png(tmp_path, ref_rel, "vip.claim")
    ref_path = resolve_reference_path(tmp_path, ref_rel)

    assert crop == tmp_path / "modules/vip/references/crop/page.vip_vip.claim.png"
    assert ref_path == tmp_path / ref_rel
