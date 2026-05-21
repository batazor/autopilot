from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from api.services.overlay_test import _area_region_names
from layout.area_manifest import load_area_doc

if TYPE_CHECKING:
    from pathlib import Path


def test_area_region_names_includes_module_manifest_regions(tmp_path: Path) -> None:
    core = tmp_path / "area.json"
    core.write_text(
        yaml.dump(
            {
                "version": 2,
                "screens": [
                    {
                        "id": 1,
                        "screen_id": "main_city",
                        "ocr": "references/main.png",
                        "regions": [{"name": "core.only", "action": "exist"}],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    ads_dir = tmp_path / "modules" / "ads"
    ads_dir.mkdir(parents=True)
    (ads_dir / "module.yaml").write_text(
        "id: ads\ntitle: Ads\narea: area.yaml\nreferences: references\n",
        encoding="utf-8",
    )
    (ads_dir / "area.yaml").write_text(
        yaml.dump(
            {
                "version": 2,
                "screens": [
                    {
                        "id": 1,
                        "screen_id": "myriad_bazaar",
                        "ocr": "references/myriad.png",
                        "regions": [
                            {"name": "button.claim_for_free", "action": "exist"},
                        ],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    doc = load_area_doc(tmp_path)
    names = _area_region_names(doc)
    assert "core.only" in names
    assert "button.claim_for_free" in names
