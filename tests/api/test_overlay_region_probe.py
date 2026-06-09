from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np
import yaml

from api.services import overlay_test
from api.services.overlay_test import _area_region_names, probe
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


def test_area_region_probe_uses_red_dot_detector_for_red_dot_region(
    tmp_path: Path,
    monkeypatch,
) -> None:
    frame = np.full((1280, 720, 3), (90, 60, 30), dtype=np.uint8)
    cv2.circle(frame, (660, 260), 8, (40, 40, 230), thickness=-1)
    ok, encoded = cv2.imencode(".png", frame)
    assert ok

    area_doc = {
        "version": 2,
        "screens": [
            {
                "screen_id": "shop.artisans_trove",
                "ocr": "",
                "regions": [
                    {
                        "name": "artisans_trove.box",
                        "action": "exist",
                        "has_red_dot": True,
                        "bbox": {
                            "x": 79.44362017804154,
                            "y": 18.20882789317508,
                            "width": 15,
                            "height": 8,
                        },
                    }
                ],
            }
        ],
    }

    monkeypatch.setattr(probe,"repo_root", lambda: tmp_path)
    monkeypatch.setattr(probe,"load_area_doc", lambda _repo: area_doc)
    monkeypatch.setattr(
        probe,
        "load_preview_bytes",
        lambda **_kwargs: (encoded.tobytes(), "temporal/bs1.png", 1.0),
    )
    monkeypatch.setattr(
        probe,
        "load_rolling_instance_preview",
        lambda _instance_id: (None, "", None),
    )
    monkeypatch.setattr(probe,"active_player_state_flat", lambda **_kwargs: {})
    monkeypatch.setattr(
        "dashboard.redis_client.get_instance_state",
        lambda *_args, **_kwargs: {
            "current_screen": "shop.artisans_trove",
            "active_player": "p1",
        },
    )

    result = overlay_test.run_area_region_probe(
        client=object(),
        instance_id="bs1",
        region="artisans_trove.box",
    )

    row = result["result"]
    assert row is not None
    assert row["action"] == "red_dot"
    assert row["matched"] is True
    assert row["red_dot_present"] is True


def test_area_region_probe_defaults_to_area_threshold(
    tmp_path: Path,
    monkeypatch,
) -> None:
    frame = np.zeros((1280, 720, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", frame)
    assert ok

    area_doc = {
        "version": 2,
        "screens": [
            {
                "screen_id": "chief_profile",
                "ocr": "",
                "regions": [
                    {
                        "name": "chief_profile.title",
                        "action": "exist",
                        "threshold": 0.85,
                        "bbox": {
                            "x": 12,
                            "y": 1,
                            "width": 27,
                            "height": 5,
                        },
                    }
                ],
            }
        ],
    }
    captured: dict[str, float] = {}

    async def fake_evaluate(_image, _area_doc, _repo, rules, **_kwargs):
        captured["threshold"] = rules[0]["threshold"]
        return {
            rules[0]["name"]: {
                "matched": True,
                "action": "findIcon",
                "region": rules[0]["region"],
                "threshold": rules[0]["threshold"],
                "score": rules[0]["threshold"],
            }
        }

    monkeypatch.setattr(probe,"repo_root", lambda: tmp_path)
    monkeypatch.setattr(probe,"load_area_doc", lambda _repo: area_doc)
    monkeypatch.setattr(
        probe,
        "load_preview_bytes",
        lambda **_kwargs: (encoded.tobytes(), "temporal/bs1.png", 1.0),
    )
    monkeypatch.setattr(
        probe,
        "load_rolling_instance_preview",
        lambda _instance_id: (None, "", None),
    )
    monkeypatch.setattr(probe,"active_player_state_flat", lambda **_kwargs: {})
    monkeypatch.setattr(probe,"evaluate_overlay_rules_async", fake_evaluate)
    monkeypatch.setattr(
        "dashboard.redis_client.get_instance_state",
        lambda *_args, **_kwargs: {
            "current_screen": "chief_profile",
            "active_player": "p1",
        },
    )

    result = overlay_test.run_area_region_probe(
        client=object(),
        instance_id="bs1",
        region="chief_profile.title",
    )

    assert captured["threshold"] == 0.85
    assert result["result"]["threshold"] == 0.85
