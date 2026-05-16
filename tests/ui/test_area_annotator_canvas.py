from __future__ import annotations

from ui.area_annotator import (
    OMNIPARSER_PROPOSAL_CANVAS_FLAG,
    OMNIPARSER_PROPOSAL_STROKE,
    regions_to_initial_drawing,
    sync_regions_from_canvas,
)


def test_regions_to_initial_drawing_adds_purple_omni_proposal_overlay() -> None:
    regions = [
        {
            "name": "main.button",
            "bbox": {"x": 10.0, "y": 20.0, "width": 30.0, "height": 40.0},
        }
    ]
    proposals = [
        {
            "name": "icon.pending",
            "bbox": {"x": 50.0, "y": 60.0, "width": 10.0, "height": 5.0},
        }
    ]

    drawing = regions_to_initial_drawing(
        regions,
        canvas_w=200,
        canvas_h=100,
        selected_idx=0,
        proposal_regions=proposals,
    )

    objects = drawing["objects"]
    assert len(objects) == 2
    proposal_obj = objects[1]
    assert proposal_obj[OMNIPARSER_PROPOSAL_CANVAS_FLAG] is True
    assert proposal_obj["stroke"] == OMNIPARSER_PROPOSAL_STROKE
    assert proposal_obj["selectable"] is False


def test_sync_regions_from_canvas_ignores_omni_proposal_overlay() -> None:
    regions = [
        {
            "name": "main.button",
            "bbox": {"x": 10.0, "y": 20.0, "width": 30.0, "height": 40.0},
        }
    ]
    drawing = regions_to_initial_drawing(
        regions,
        canvas_w=200,
        canvas_h=100,
        selected_idx=0,
        proposal_regions=[
            {
                "name": "icon.pending",
                "bbox": {"x": 50.0, "y": 60.0, "width": 10.0, "height": 5.0},
            }
        ],
    )

    synced = sync_regions_from_canvas(
        regions,
        drawing,
        canvas_w=200,
        canvas_h=100,
        orig_w=200,
        orig_h=100,
    )

    assert [r["name"] for r in synced] == ["main.button"]
