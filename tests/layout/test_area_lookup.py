from __future__ import annotations

from layout.area_lookup import screen_region_by_name


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


def test_screen_region_by_name_prefers_scoped_region_over_global_region() -> None:
    area_doc = {
        "screens": [
            {
                "screen_id": "",
                "regions": [
                    {
                        "name": "shared.button",
                        "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
                    }
                ],
            },
            {
                "screen_id": "chief_profile",
                "regions": [
                    {
                        "name": "shared.button",
                        "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                    }
                ],
            },
        ]
    }

    found = screen_region_by_name(area_doc, "shared.button", screen_id="chief_profile")

    assert found is not None
    assert found[0]["screen_id"] == "chief_profile"
    assert found[1]["bbox"]["x"] == 10
