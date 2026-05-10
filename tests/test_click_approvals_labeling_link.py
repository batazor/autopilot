from __future__ import annotations

from ui.views.click_approvals.common import labeling_query_params_for_area_region


def test_labeling_query_params_for_default_area_region() -> None:
    doc = {
        "screens": [
            {
                "ocr": "references/main_city.png",
                "regions": [{"name": "mail", "bbox": {"x": 1, "y": 2}}],
            }
        ]
    }

    assert labeling_query_params_for_area_region(doc, "mail") == {
        "ref": "main_city.png",
        "region": "mail",
    }


def test_labeling_query_params_for_active_version_region() -> None:
    doc = {
        "screens": [
            {
                "ocr": "references/main_city.png",
                "versions": [
                    {
                        "id": "v2",
                        "cond": "heroes.norah.level >= 6",
                        "ocr": "references/main_city_v2.png",
                    }
                ],
                "regions": [
                    {"name": "mail", "bbox": {"x": 1, "y": 2}},
                    {"name": "mail_v2", "bbox": {"x": 3, "y": 4}},
                ],
            }
        ]
    }

    assert labeling_query_params_for_area_region(
        doc,
        "mail",
        state_flat={"heroes.norah.level": 6},
    ) == {
        "ref": "main_city_v2.png",
        "region": "mail_v2",
        "version": "v2",
    }
