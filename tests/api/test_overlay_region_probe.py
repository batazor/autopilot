from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from api.services.overlay_test import _area_region_names
from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for
from layout.area_manifest import load_area_doc

if TYPE_CHECKING:
    from pathlib import Path


def test_area_region_names_aggregates_module_manifest_regions(tmp_path: Path) -> None:
    main_dir = _modules_root_for(_default_game(), repo_root=tmp_path) / "main_city"
    main_dir.mkdir(parents=True)
    (main_dir / "module.yaml").write_text(
        "id: main_city\ntitle: Main City\narea: area.yaml\nreferences: references\n",
        encoding="utf-8",
    )
    (main_dir / "area.yaml").write_text(
        yaml.dump(
            {
                "version": 2,
                "screens": [
                    {
                        "id": 1,
                        "screen_id": "main_city",
                        "ocr": "references/main.png",
                        "regions": [{"name": "main_city.button", "action": "exist"}],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    ads_dir = _modules_root_for(_default_game(), repo_root=tmp_path) / "ads"
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
    assert "main_city.button" in names
    assert "button.claim_for_free" in names
