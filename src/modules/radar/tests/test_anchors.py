"""Landmark-anchor constraints: save_anchors writes known-structure game_xy into
the same corners.json the solver already consumes via load_corner_constraints."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.radar.corners import load_corner_constraints, save_anchors
from modules.radar.stitch_georef import MAP_META_NAME

FRAME_W, FRAME_H = 720, 1280


def _make_run(tmp_path: Path) -> Path:
    """A run dir with a 2-frame map_meta (frame 00_00 at origin, 01_00 to its right)."""
    meta = {
        "frames": {
            "00_00": {"canvas_px": [0, 0]},
            "01_00": {"canvas_px": [700, 0]},
        }
    }
    (tmp_path / MAP_META_NAME).write_text(json.dumps(meta), encoding="utf-8")
    return tmp_path


_ENTRIES = [{"ix": 0, "iy": 0}, {"ix": 1, "iy": 0}]


def test_save_anchors_persists_known_game_xy(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    anchors = [
        {"label": "castle", "game_xy": (597, 597), "canvas_px": (300, 300)},
        {"label": "fortress_1", "game_xy": (366, 957), "canvas_px": (650, 400)},
        {"label": "turret", "game_xy": (593, 593), "canvas_px": (100, 100)},
    ]
    sidecar = save_anchors(run, anchors, frame_w=FRAME_W, frame_h=FRAME_H, game_size=1200)
    assert sidecar["game_size"] == 1200
    assert len(sidecar["corners"]) == 3
    castle = next(c for c in sidecar["corners"] if c["label"] == "castle")
    assert castle["game_xy"] == [597, 597]
    assert castle["frame_key"] == "00_00"
    assert castle["frame_px"] == [300.0, 300.0]  # click − frame canvas origin (0,0)


def test_load_corner_constraints_reads_landmarks(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    anchors = [
        {"label": "castle", "game_xy": (597, 597), "canvas_px": (300, 300)},
        {"label": "fort", "game_xy": (366, 957), "canvas_px": (650, 400)},
        {"label": "turret", "game_xy": (593, 593), "canvas_px": (100, 100)},
    ]
    save_anchors(run, anchors, frame_w=FRAME_W, frame_h=FRAME_H)
    constraints = load_corner_constraints(run, _ENTRIES)
    assert len(constraints) == 3
    # each constraint is (frame_index, frame_px, game_xy); all land in frame 0
    assert {c[0] for c in constraints} == {0}
    gxys = {c[2] for c in constraints}
    assert (597.0, 597.0) in gxys
    assert (366.0, 957.0) in gxys


def test_merge_keeps_others_replaces_same_label(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    save_anchors(
        run,
        [
            {"label": "castle", "game_xy": (597, 597), "canvas_px": (300, 300)},
            {"label": "fort", "game_xy": (366, 957), "canvas_px": (650, 400)},
            {"label": "turret", "game_xy": (593, 593), "canvas_px": (100, 100)},
        ],
        frame_w=FRAME_W, frame_h=FRAME_H,
    )
    # Re-mark just the castle at a new pixel — merge keeps fort+turret, replaces castle.
    sidecar = save_anchors(
        run,
        [{"label": "castle", "game_xy": (597, 597), "canvas_px": (320, 280)}],
        frame_w=FRAME_W, frame_h=FRAME_H,
    )
    assert len(sidecar["corners"]) == 3
    castle = next(c for c in sidecar["corners"] if c["label"] == "castle")
    assert castle["canvas_px"] == [320.0, 280.0]
    assert {c["label"] for c in sidecar["corners"]} == {"castle", "fort", "turret"}


def test_off_map_click_rejected(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    with pytest.raises(ValueError, match="off the scanned map"):
        save_anchors(
            run,
            [
                {"label": "castle", "game_xy": (597, 597), "canvas_px": (300, 300)},
                {"label": "fort", "game_xy": (366, 957), "canvas_px": (650, 400)},
                {"label": "ghost", "game_xy": (0, 0), "canvas_px": (5000, 5000)},
            ],
            frame_w=FRAME_W, frame_h=FRAME_H,
        )


def test_too_few_constraints_rejected(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    with pytest.raises(ValueError, match="at least 3"):
        save_anchors(
            run,
            [{"label": "castle", "game_xy": (597, 597), "canvas_px": (300, 300)}],
            frame_w=FRAME_W, frame_h=FRAME_H, merge=False,
        )
