from __future__ import annotations

from layout.area_lookup import screen_region_by_name
from layout.area_manifest import load_area_doc


def test_main_world_to_main_city_region_uses_bottom_right_city_button() -> None:
    from config.paths import repo_root

    area_doc = load_area_doc(repo_root(), game="wos")

    found = screen_region_by_name(area_doc, "main_world.to.main_city")

    assert found is not None
    entry, region = found
    assert entry["screen_id"] == "main_world"
    bbox = region["bbox"]
    center_x = (float(bbox["x"]) + float(bbox["width"]) / 2) / 100 * 720
    center_y = (float(bbox["y"]) + float(bbox["height"]) / 2) / 100 * 1280
    assert 610 <= center_x <= 700
    assert 1160 <= center_y <= 1265


def test_screen_region_by_name_falls_back_to_global_region_when_scoped_misses() -> None:
    area_doc = {
        "screens": [
            {
                "screen_id": "",
                "regions": [
                    {
                        "name": "hand_pointer_small_reverse",
                        "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
                    }
                ],
            },
            {
                "screen_id": "chief_profile",
                "regions": [
                    {
                        "name": "profile.title",
                        "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                    }
                ],
            },
        ]
    }

    found = screen_region_by_name(
        area_doc,
        "hand_pointer_small_reverse",
        screen_id="chief_profile",
    )

    assert found is not None
    assert found[1]["name"] == "hand_pointer_small_reverse"
