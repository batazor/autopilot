from __future__ import annotations

import json
from pathlib import Path

import navigation.screen_graph as screen_graph


def test_area_screen_region_adds_screen_landmark(mocker, tmp_path: Path) -> None:
    cfg = tmp_path / "screen_verify.yaml"
    cfg.write_text("screens: []\n", encoding="utf-8")
    area = tmp_path / "area.json"
    area.write_text(
        json.dumps(
            {
                "screens": [
                    {
                        "screen_id": "reconnect",
                        "screen_region": "icon.reconnect",
                        "regions": [
                            {
                                "name": "icon.reconnect",
                                "threshold": 0.91,
                                "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    mocker.patch.object(screen_graph, "_screen_verify_yaml_path", new=lambda: cfg)
    mocker.patch.object(screen_graph, "_area_json_path", new=lambda: area)
    # Per-hero wiki screens are synthesized from the real heroes index; the
    # test wants to assert the area-region path in isolation, so suppress them.
    mocker.patch.object(screen_graph, "_hero_ids", new=lambda: [])
    screen_graph.load_screen_verify_config.cache_clear()

    try:
        assert screen_graph.screen_verify_screen_names() == ["reconnect"]
        assert screen_graph.screen_landmark_rules("reconnect") == [
            {"match": "icon.reconnect", "threshold": 0.91}
        ]
    finally:
        screen_graph.load_screen_verify_config.cache_clear()
