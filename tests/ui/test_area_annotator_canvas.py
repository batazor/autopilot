from __future__ import annotations

from ui.area_annotator import (
    ensure_entry_for_reference_path,
    load_json,
    regions_to_initial_drawing,
    save_json,
    sync_regions_from_canvas,
)


def test_regions_to_initial_drawing_maps_region_names_to_canvas_objects() -> None:
    regions = [
        {
            "name": "main.button",
            "bbox": {"x": 10.0, "y": 20.0, "width": 30.0, "height": 40.0},
        }
    ]

    drawing = regions_to_initial_drawing(
        regions,  # ty: ignore[invalid-argument-type]
        canvas_w=200,
        canvas_h=100,
        selected_idx=0,
    )

    objects = drawing["objects"]
    assert len(objects) == 1
    assert objects[0]["wos_region_name"] == "main.button"


def test_load_and_save_json_supports_module_area_yaml(tmp_path) -> None:
    area_path = tmp_path / "area.yaml"
    doc = {
        "screens": [
            {
                "id": 54,
                "screen_id": "event.trials",
                "ocr": "references/page.trials.png",
                "regions": [],
            }
        ]
    }

    removed = save_json(area_path, doc)  # ty: ignore[invalid-argument-type]
    loaded = load_json(area_path)

    assert removed == 0
    assert loaded["screens"][0]["screen_id"] == "event.trials"
    assert area_path.read_text(encoding="utf-8").startswith("screens:")


def test_ensure_entry_matches_module_local_ocr_by_reference_prefix() -> None:
    entries = [
        {
            "id": 54,
            "screen_id": "event.trials",
            "ocr": "references/page.trials.png",
            "regions": [{"name": "trial.day.1"}],
        }
    ]

    idx = ensure_entry_for_reference_path(
        entries,  # ty: ignore[invalid-argument-type]
        "modules/events/trials/references/page.trials.png",
        references_prefix="modules/events/trials/references",
    )

    assert idx == 0
    assert len(entries) == 1


def test_ensure_entry_stores_new_module_ocr_as_local_reference() -> None:
    entries = []

    idx = ensure_entry_for_reference_path(
        entries,
        "modules/events/trials/references/new.png",
        references_prefix="modules/events/trials/references",
    )

    assert idx == 0
    assert entries[0]["ocr"] == "references/new.png"


def test_sync_regions_from_canvas_removes_region_when_canvas_object_is_deleted() -> None:
    regions = [
        {
            "name": "main.button",
            "bbox": {"x": 10.0, "y": 20.0, "width": 30.0, "height": 40.0},
        },
        {
            "name": "close.button",
            "bbox": {"x": 50.0, "y": 60.0, "width": 10.0, "height": 5.0},
        },
    ]
    drawing = regions_to_initial_drawing(
        regions,  # ty: ignore[invalid-argument-type]
        canvas_w=200,
        canvas_h=100,
        selected_idx=0,
    )
    drawing["objects"] = [
        obj
        for obj in drawing["objects"]
        if obj.get("wos_region_name") != "close.button"
    ]

    synced = sync_regions_from_canvas(
        regions,  # ty: ignore[invalid-argument-type]
        drawing,
        canvas_w=200,
        canvas_h=100,
        orig_w=200,
        orig_h=100,
    )

    assert [r["name"] for r in synced] == ["main.button"]
