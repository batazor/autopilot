"""OCR name-plate stitch anchors — bridge featureless-snow gaps that drift ORB.

`_plate_match_edges` turns a building read in two frames into a `MatchEdge`
(`pos_j - pos_i = frame_px_i - frame_px_j`); the joint solve then follows it
where ORB had no edge, so a snow gap no longer folds the map. A grid-prior gate
keeps a candidate only when its offset matches the local camera step — rejecting
cross-matches of DIFFERENT buildings that share a normalized name (Shelter N).
"""

import numpy as np

import modules.radar.labels as labels_mod
from modules.radar.stitch import _plate_match_edges
from modules.radar.stitch_matching import MatchEdge, _solve_matched_positions

CROP = {"x": 0, "y": 0, "w": 720, "h": 1280}
RIGHT = (300.0, 0.0)  # grid step right
DOWN = (0.0, 300.0)   # grid step down


def _patch_dets(monkeypatch, per_frame):
    """Make `detect_labels` return `per_frame[k]` for the k-th non-None frame."""
    calls = iter(per_frame)
    monkeypatch.setattr(labels_mod, "detect_labels", lambda *_a: next(calls))
    monkeypatch.setattr(labels_mod, "_tesseract_cmd", lambda: "tesseract")


def _img():
    return np.zeros((4, 4, 3), np.uint8)


def _entries(cells):
    return [{"ix": ix, "iy": iy} for ix, iy in cells]


def test_plate_edge_from_shared_unique_building(monkeypatch):
    # Furnace in two right-neighbour frames; implied offset == the grid step.
    _patch_dets(monkeypatch, [
        [{"name": "Furnace", "confidence": 90, "frame_px": [400, 300]}],
        [{"name": "Furnace", "confidence": 90, "frame_px": [100, 300]}],
    ])
    edges = _plate_match_edges(_entries([(0, 0), (1, 0)]), [_img(), _img()], CROP, RIGHT, DOWN)
    assert len(edges) == 1
    e = edges[0]
    assert (e.i, e.j) == (0, 1)
    assert e.dx == 300 and e.dy == 0  # pos_j - pos_i = frame_px_i - frame_px_j == right step
    assert e.score > 0.5


def test_plate_edges_skip_duplicate_within_frame(monkeypatch):
    # "Shelter" twice in frame 0 → ambiguous which-to-which → no edge at all.
    _patch_dets(monkeypatch, [
        [{"name": "Shelter", "confidence": 90, "frame_px": [200, 300]},
         {"name": "Shelter", "confidence": 90, "frame_px": [400, 300]}],
        [{"name": "Shelter", "confidence": 90, "frame_px": [100, 300]}],
    ])
    assert _plate_match_edges(_entries([(0, 0), (1, 0)]), [_img(), _img()], CROP, RIGHT, DOWN) == []


def test_plate_edge_gate_rejects_cross_match(monkeypatch):
    # Two DIFFERENT shelters (same normalized name, one per frame) at the same
    # screen spot → implied offset (0,0) ≠ grid step (300,0) → gate rejects.
    _patch_dets(monkeypatch, [
        [{"name": "Shelter", "confidence": 90, "frame_px": [400, 300]}],
        [{"name": "Shelter", "confidence": 90, "frame_px": [400, 300]}],
    ])
    assert _plate_match_edges(_entries([(0, 0), (1, 0)]), [_img(), _img()], CROP, RIGHT, DOWN) == []


def test_plate_edge_skips_far_grid_cells(monkeypatch):
    # Same name but the frames are >2 grid cells apart → not co-visible → skip.
    _patch_dets(monkeypatch, [
        [{"name": "Furnace", "confidence": 90, "frame_px": [400, 300]}],
        [{"name": "Furnace", "confidence": 90, "frame_px": [400, 300]}],
    ])
    assert _plate_match_edges(_entries([(0, 0), (5, 0)]), [_img(), _img()], CROP, RIGHT, DOWN) == []


def test_no_crop_no_edges():
    assert _plate_match_edges(_entries([(0, 0)]), [_img()], None, RIGHT, DOWN) == []


def test_plate_edge_corrects_drift_no_fold():
    # Grid prior (nominal) drifted: says the two frames are 500px apart. A strong
    # plate edge says only 100px → the joint solve must follow the plate, not the
    # fold. This is the anti-fold guarantee, minus the on-device OCR.
    nominal = [(0.0, 0.0), (500.0, 0.0)]
    images = [_img(), _img()]
    edges = [MatchEdge(0, 1, 100.0, 0.0, 0.9)]
    positions, _ = _solve_matched_positions(nominal, images, edges)
    dx = positions[1][0] - positions[0][0]
    assert abs(dx - 100.0) < 50.0  # pulled to the plate edge, fold corrected
