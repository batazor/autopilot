from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np

from analysis.overlay import evaluate_overlay_rules
from layout.crop_paths import exported_crop_png

if TYPE_CHECKING:
    from pathlib import Path


def test_search_region_match_uses_named_tap_region_offset(tmp_path: Path) -> None:
    repo = tmp_path
    ref_rel = "references/tutorial.png"
    (repo / "references" / "crop").mkdir(parents=True)

    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    template = np.zeros((10, 10, 3), dtype=np.uint8)
    template[:, :5] = (0, 220, 255)
    template[:, 5:] = (0, 0, 255)
    frame[40:50, 30:40] = template

    crop = exported_crop_png(repo, ref_rel, "hand_pointer")
    cv2.imwrite(str(crop), template)

    area_doc = {
        "screens": [
            {
                "id": 1,
                "ocr": ref_rel,
                "regions": [
                    {
                        "name": "hand_pointer",
                        "bbox": {"x": 10, "y": 10, "width": 10, "height": 10},
                    },
                    {
                        "name": "hand_pointer_search",
                        "bbox": {"x": 0, "y": 0, "width": 100, "height": 100},
                    },
                    {
                        "name": "hand_pointer_tap",
                        "bbox": {"x": 25, "y": 35, "width": 10, "height": 10},
                    },
                ],
            }
        ]
    }
    rules = [
        {
            "name": "hand_pointer.visible",
            "region": "hand_pointer",
            "search_region": "hand_pointer_search",
            "action": "findIcon",
            "threshold": 0.98,
        }
    ]

    out = evaluate_overlay_rules(frame, area_doc, repo, rules)
    hit = out["hand_pointer.visible"]

    assert hit["matched"] is True
    assert hit["tap_match_x_pct"] == 35
    assert hit["tap_match_y_pct"] == 45
    assert hit["tap_region"] == "hand_pointer_tap"
    assert hit["tap_x_pct"] == 50
    assert hit["tap_y_pct"] == 70


def test_search_region_can_come_from_same_screen_different_reference(tmp_path: Path) -> None:
    repo = tmp_path
    ref_rel = "games/wos/events/trials/references/main_city.trials.png"
    (repo / "games/wos/events/trials/references/crop").mkdir(parents=True)

    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    template = np.zeros((10, 10, 3), dtype=np.uint8)
    template[:, :5] = (0, 220, 255)
    template[:, 5:] = (0, 0, 255)
    frame[40:50, 30:40] = template

    crop = exported_crop_png(repo, ref_rel, "module.event.icon")
    cv2.imwrite(str(crop), template)

    area_doc = {
        "screens": [
            {
                "id": 1,
                "screen_id": "main_city",
                "ocr": ref_rel,
                "regions": [
                    {
                        "name": "module.event.icon",
                        "bbox": {"x": 10, "y": 10, "width": 10, "height": 10},
                    }
                ],
            },
            {
                "id": 2,
                "screen_id": "main_city",
                "ocr": "references/main_city.png",
                "regions": [
                    {
                        "name": "main_city.icon_search",
                        "bbox": {"x": 0, "y": 0, "width": 100, "height": 100},
                    }
                ],
            },
        ]
    }
    rules = [
        {
            "name": "module.event.icon.visible",
            "region": "module.event.icon",
            "search_region": "main_city.icon_search",
            "action": "findIcon",
            "threshold": 0.98,
        }
    ]

    out = evaluate_overlay_rules(frame, area_doc, repo, rules)
    hit = out["module.event.icon.visible"]

    assert hit["matched"] is True
    assert hit["top_left"] == [30, 40]
    assert hit["search_region"] == "main_city.icon_search"


def test_findicon_can_use_direct_template_file(tmp_path: Path) -> None:
    repo = tmp_path
    template_path = repo / "games/wos/events/trials/references/event.trials.png"
    template_path.parent.mkdir(parents=True)

    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    template = np.zeros((10, 10, 4), dtype=np.uint8)
    template[:, :, 3] = 255
    template[:, :5, :3] = (0, 220, 255)
    template[:, 5:, :3] = (0, 0, 255)
    frame[40:50, 30:40] = template[:, :, :3]
    cv2.imwrite(str(template_path), template)

    area_doc = {
        "screens": [
            {
                "id": 1,
                "screen_id": "main_city",
                "ocr": "references/main_city.png",
                "regions": [
                    {
                        "name": "main_city.icon_search",
                        "bbox": {"x": 0, "y": 0, "width": 100, "height": 100},
                    }
                ],
            }
        ]
    }
    rules = [
        {
            "name": "module.event.icon.visible",
            "region": "main_city.icon_search",
            "template": "games/wos/events/trials/references/event.trials.png",
            "action": "findIcon",
            "threshold": 0.98,
        }
    ]

    out = evaluate_overlay_rules(frame, area_doc, repo, rules)
    hit = out["module.event.icon.visible"]

    assert hit["matched"] is True
    assert hit["top_left"] == [30, 40]
    assert hit["template"] == "games/wos/events/trials/references/event.trials.png"
    assert hit["match_source"] == "direct_template"


def test_direct_template_findicon_can_require_red_dot(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path
    template_path = repo / "games/wos/events/trials/references/event.trials.png"
    template_path.parent.mkdir(parents=True)

    monkeypatch.setattr(
        "analysis.overlay_engine.has_red_dot_in_bbox_percent",
        lambda *_args, **_kwargs: True,
    )

    frame = np.zeros((1280, 720, 3), dtype=np.uint8)
    template = np.full((57, 64, 4), 255, dtype=np.uint8)
    template[:, :, :3] = (0, 220, 255)
    frame[124:181, 523:587] = template[:, :, :3]
    cv2.imwrite(str(template_path), template)

    area_doc = {
        "screens": [
            {
                "id": 1,
                "screen_id": "main_city",
                "ocr": "references/main_city.png",
                "regions": [
                    {
                        "name": "main_city.icon_search",
                            "bbox": {"x": 65, "y": 5, "width": 34, "height": 36},
                    }
                ],
            }
        ]
    }
    rules = [
        {
            "name": "trials.main_city.event_icon.visible",
            "region": "main_city.icon_search",
            "template": "games/wos/events/trials/references/event.trials.png",
            "action": "findIcon",
            "threshold": 0.9,
            "isRedDot": True,
        }
    ]

    out = evaluate_overlay_rules(frame, area_doc, repo, rules)
    hit = out["trials.main_city.event_icon.visible"]

    assert hit["matched"] is True
    assert hit["top_left"] == [523, 124]
    assert hit["red_dot_required"] is True
    assert hit["red_dot_present"] is True


def test_one_to_one_match_uses_named_tap_region_offset(tmp_path: Path) -> None:
    """findIcon without ``search_region`` must still apply the ``_tap`` offset."""
    repo = tmp_path
    ref_rel = "references/event.png"
    (repo / "references" / "crop").mkdir(parents=True)

    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    # Match the primary region 1:1 inside its declared bbox: x=10..20, y=10..20.
    template = np.zeros((10, 10, 3), dtype=np.uint8)
    template[:, :5] = (0, 220, 255)
    template[:, 5:] = (0, 0, 255)
    frame[10:20, 10:20] = template

    crop = exported_crop_png(repo, ref_rel, "event_popup")
    cv2.imwrite(str(crop), template)

    area_doc = {
        "screens": [
            {
                "id": 1,
                "ocr": ref_rel,
                "regions": [
                    {
                        "name": "event_popup",
                        "bbox": {"x": 10, "y": 10, "width": 10, "height": 10},
                    },
                    {
                        "name": "event_popup_tap",
                        "bbox": {"x": 70, "y": 80, "width": 10, "height": 10},
                    },
                ],
            }
        ]
    }
    rules = [
        {
            "name": "event_popup.visible",
            "region": "event_popup",
            "action": "findIcon",
            "threshold": 0.9,
        }
    ]

    out = evaluate_overlay_rules(frame, area_doc, repo, rules)
    hit = out["event_popup.visible"]

    assert hit["matched"] is True
    # Primary match center = (15, 15) pct; tap center = (75, 85) pct.
    assert hit["tap_match_x_pct"] == 15
    assert hit["tap_match_y_pct"] == 15
    assert hit["tap_region"] == "event_popup_tap"
    assert hit["tap_delta_x_pct"] == 60
    assert hit["tap_delta_y_pct"] == 70
    assert hit["tap_x_pct"] == 75
    assert hit["tap_y_pct"] == 85
