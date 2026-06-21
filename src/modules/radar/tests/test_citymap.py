"""Tests for assembling a persistent city map from scan chunks."""

import json

import cv2
import numpy as np

from modules.radar import citymap
from modules.radar.citymap import build_city_map, place_runs


def _write_run(d, buildings, *, target="main_city", w=40, h=30):
    d.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(d / "map_full.png"), np.full((h, w, 3), 200, np.uint8))
    (d / "buildings.json").write_text(json.dumps({"count": len(buildings), "buildings": buildings}))
    (d / "manifest.json").write_text(
        json.dumps({"config": {"target": target, "crop": {"x": 0, "y": 0, "w": 10, "h": 10}},
                    "swipe_calibration": {"scale_x": 1.0, "scale_y": 1.0}})
    )


def test_single_run_passthrough(tmp_path):
    src = tmp_path / "run0"
    _write_run(src, [{"name": "Furnace", "canvas_px": [5, 6], "confidence": 95}])
    out = tmp_path / "map"
    res = build_city_map([src], out)
    assert res["chunks"] == 1 and res["buildings"] == 1
    man = json.loads((out / "manifest.json").read_text())
    assert man["config"]["target"] == "main_city"  # loadable as a main_city run
    reg = json.loads((out / "buildings.json").read_text())
    assert reg["buildings"][0]["name"] == "Furnace"
    assert (out / "map_full.png").is_file()


def test_place_runs_offsets_chain(monkeypatch):
    # Mock ORB alignment: chunk B sits +100x,+50y from chunk A's canvas.
    monkeypatch.setattr(citymap, "_canvas_offset", lambda *_a: (100.0, 50.0))
    a = ("a", np.zeros((10, 10, 3), np.uint8), [{"name": "A", "canvas_px": [1, 1]}, {"name": "AA", "canvas_px": [2, 2]}])
    b = ("b", np.zeros((10, 10, 3), np.uint8), [{"name": "B", "canvas_px": [3, 3]}])
    placed = place_runs([a, b])
    # Largest (a, 2 buildings) anchors at (0,0); b is offset by the ORB shift.
    by_name = {n: off for n, _c, off, _b in placed}
    assert by_name["a"] == (0.0, 0.0)
    assert by_name["b"] == (100.0, 50.0)


def test_unanchored_chunk_dropped(monkeypatch):
    monkeypatch.setattr(citymap, "_canvas_offset", lambda *_a: None)  # never overlaps
    a = ("a", np.zeros((10, 10, 3), np.uint8), [{"name": "A", "canvas_px": [1, 1]}])
    b = ("b", np.zeros((10, 10, 3), np.uint8), [{"name": "B", "canvas_px": [3, 3]}])
    placed = place_runs([a, b])
    assert {n for n, *_ in placed} == {"a"}


def test_assemble_gathers_chunks_into_citymap(tmp_path):
    from modules.radar.citymap import assemble_city_map
    from modules.radar.navigator import CITYMAP_DIRNAME, latest_city_run

    _write_run(tmp_path / "scan_a", [{"name": "Furnace", "canvas_px": [5, 6]}])
    _write_run(tmp_path / "world", [{"name": "X", "canvas_px": [1, 1]}], target="global_map")
    res = assemble_city_map(tmp_path)
    assert res["buildings"] == 1  # the global_map run is ignored
    picked = latest_city_run(tmp_path)
    assert picked is not None and picked.name == CITYMAP_DIRNAME


def test_assemble_without_scans_raises(tmp_path):
    import pytest

    from modules.radar.citymap import assemble_city_map

    with pytest.raises(ValueError, match="no main_city"):
        assemble_city_map(tmp_path)
