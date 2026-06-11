"""Feature-based (ORB) registration used by radar stitch."""

import json

import cv2
import numpy as np
import pytest

from modules.radar.scanner import MANIFEST_NAME
from modules.radar.stitch import (
    _orb_features,
    _orb_pair_offset,
    _useful_area_mask,
    _valid_content_mask,
    move_prior,
    run_stitch,
)


def _make_world(seed: int, h: int, w: int) -> np.ndarray:
    """Shape-rich texture: icons/buildings analog — plenty of ORB corners.

    Snow-bright background (200): a dark fill would trip the kingdom-edge
    detector in _valid_content_mask and eat the canvas border in tests.
    """
    rng = np.random.default_rng(seed)
    world = np.full((h, w, 3), 200, np.uint8)
    for _ in range(h * w // 900):
        x, y = int(rng.integers(0, w - 45)), int(rng.integers(0, h - 45))
        s = int(rng.integers(8, 40))
        color = tuple(int(c) for c in rng.integers(60, 255, 3))
        if rng.random() < 0.5:
            cv2.rectangle(world, (x, y), (x + s, y + s), color, -1)
        else:
            cv2.circle(world, (x + s // 2, y + s // 2), s // 2 + 2, color, -1)
    return world


def _features(img: np.ndarray):
    return _orb_features(img, _useful_area_mask(img, None, None))


def _locate(canvas: np.ndarray, frame: np.ndarray) -> tuple[int, int]:
    """Exact-search a frame inside the stitched canvas; returns (x, y)."""
    result = cv2.matchTemplate(canvas, frame, cv2.TM_SQDIFF)
    _, _, loc, _ = cv2.minMaxLoc(result)
    return loc


def test_orb_pair_offset_recovers_diagonal_shift() -> None:
    world = _make_world(7, 700, 900)
    a = world[100:400, 100:500]
    b = world[160:460, 230:630]  # pos_b - pos_a = (130, 60) — diagonal move

    estimate = _orb_pair_offset(_features(a), _features(b))

    assert estimate is not None
    dx, dy, score = estimate
    assert dx == pytest.approx(130, abs=1.0)
    assert dy == pytest.approx(60, abs=1.0)
    assert score > 0.3


def test_orb_pair_offset_rejects_featureless_frames() -> None:
    flat = np.full((300, 400, 3), 128, np.uint8)
    textured = _make_world(5, 300, 400)
    assert _orb_pair_offset(_features(flat), _features(textured)) is None


def test_orb_pair_offset_prior_gates_the_consensus() -> None:
    """The prior window keeps aliases out and rejects offsets navigation
    cannot explain (e.g. static-UI zero-shift consensus on a 200px swipe)."""
    world = _make_world(9, 700, 900)
    a = world[100:400, 100:500]
    b = world[190:490, 300:700]  # true offset (200, 90)

    near = _orb_pair_offset(_features(a), _features(b), expected=(210.0, 80.0))
    assert near is not None
    assert near[0] == pytest.approx(200, abs=1.0)
    assert near[1] == pytest.approx(90, abs=1.0)

    # A prior of "no movement" cannot explain a (200, 90) shift → no edge,
    # better nominal fallback than a confidently wrong placement.
    assert _orb_pair_offset(_features(a), _features(b), expected=(0.0, 0.0)) is None


def test_move_prior_inverts_summed_finger_travel() -> None:
    entry = {
        "move": {
            "mode": "swipe",
            "swipes": [
                {"x1": 572, "y1": 204, "x2": 392, "y2": 204, "ms": 600},
                {"x1": 572, "y1": 204, "x2": 392, "y2": 204, "ms": 600},
            ],
        }
    }
    # Finger went 2×180px left → content went left → next frame sits +360 right.
    assert move_prior(entry) == (360.0, 0.0)
    assert move_prior({"move": {"mode": "swipe", "origin": True}}) is None
    assert move_prior({"move": {"mode": "tap", "target_px": [600, 60]}}) is None
    assert move_prior({}) is None


def test_useful_area_mask_excludes_hud_outside_crop() -> None:
    img = np.zeros((1280, 720, 3), np.uint8)
    mask = _useful_area_mask(img, None, {"x": 0, "y": 156, "w": 620, "h": 940})
    assert mask[100, 100] == 0      # top HUD bar
    assert mask[1200, 100] == 0     # bottom nav/chat
    assert mask[600, 680] == 0      # right-side buttons
    assert mask[600, 300] == 255    # world content area


def test_valid_content_mask_uses_yellow_boundary_to_drop_dark_outside() -> None:
    img = np.full((180, 180, 3), (210, 220, 240), dtype=np.uint8)
    dark_poly = np.array([[(0, 0), (180, 0), (0, 180)]], dtype=np.int32)
    cv2.fillPoly(img, dark_poly, (34, 36, 44))
    for start in range(-20, 180, 18):
        cv2.line(
            img,
            (max(start, 0), max(0, 170 - start)),
            (min(start + 10, 179), max(0, 170 - start - 10)),
            (120, 230, 235),
            4,
        )

    mask = _valid_content_mask(img)

    assert mask[8, 8] == 0
    assert mask[150, 150] == 255
    # Yellow boundary itself is kept so the stitched map still shows the edge.
    assert np.count_nonzero(mask[(img[:, :, 1] > 220) & (img[:, :, 2] > 220)]) > 0


def test_valid_content_mask_keeps_fog_far_from_the_border() -> None:
    """Unexplored fog of war is dark too, but it is map content: only dark
    regions touching the yellow border line may be cut. A golden event marker
    elsewhere must not turn fog into 'outside the kingdom'."""
    img = np.full((200, 200, 3), (210, 220, 240), dtype=np.uint8)
    # Yellow marker pixels in the top-left corner (enough to trip the trigger).
    for x in range(0, 60, 12):
        cv2.line(img, (x, 10), (x + 6, 10), (120, 230, 235), 4)
    # Dark fog blob at the bottom-right, border-touching but far from yellow.
    cv2.rectangle(img, (140, 140), (200, 200), (60, 62, 70), -1)

    mask = _valid_content_mask(img)

    assert mask[180, 180] == 255  # fog stays on the map


def test_run_stitch_places_uncropped_frames(tmp_path) -> None:
    """Frames are placed as-is: tile size comes from the image, not config."""
    world = _make_world(3, 600, 800)
    frame_w, frame_h = 400, 300
    overlap = 0.5
    step_x = int(frame_w * (1 - overlap))  # 200
    step_y = int(frame_h * (1 - overlap))  # 150

    frames = {}
    for ix, iy in [(0, 0), (1, 0), (0, 1), (1, 1)]:
        name = f"frame_{ix:02d}_{iy:02d}.png"
        x, y = ix * step_x, iy * step_y
        cv2.imwrite(str(tmp_path / name), world[y : y + frame_h, x : x + frame_w])
        frames[f"{ix:02d}_{iy:02d}"] = {"ix": ix, "iy": iy, "file": name}

    manifest = {
        "config": {
            "overlap": overlap,
            "stitch_viewport": {"w": frame_w, "h": frame_h},
            # crop applies to feature masking only and must not shrink tiles
            "crop": {"x": 0, "y": 0, "w": frame_w, "h": frame_h},
        },
        "frames": frames,
    }
    (tmp_path / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    out = run_stitch(tmp_path)

    canvas = cv2.imread(str(out))
    assert canvas is not None
    # ORB registration is subpixel-accurate; allow ±2 px of rounding.
    assert canvas.shape[1] == pytest.approx(step_x + frame_w, abs=2)
    assert canvas.shape[0] == pytest.approx(step_y + frame_h, abs=2)
    origin = _locate(canvas, world[:frame_h, :frame_w])
    for ix, iy in [(1, 0), (0, 1), (1, 1)]:
        x, y = ix * step_x, iy * step_y
        placed = _locate(canvas, world[y : y + frame_h, x : x + frame_w])
        assert placed[0] - origin[0] == pytest.approx(x, abs=2), (ix, iy)
        assert placed[1] - origin[1] == pytest.approx(y, abs=2), (ix, iy)


def test_run_stitch_pastes_only_the_crop_region(tmp_path) -> None:
    """HUD areas (chat at the bottom, buttons on the right, top bar) must not
    reach the canvas: only the crop region of each frame is pasted, and the
    canvas is trimmed to painted content."""
    world = _make_world(13, 600, 800)
    frame_w, frame_h = 400, 300
    crop = {"x": 10, "y": 20, "w": 360, "h": 240}  # ends 370 / 260
    hud_red = (0, 0, 255)

    frames = {}
    for ix in (0, 1):
        frame = world[0:frame_h, ix * 200 : ix * 200 + frame_w].copy()
        frame[: crop["y"], :] = hud_red                        # top bar
        frame[crop["y"] + crop["h"] :, :] = hud_red            # bottom chat
        frame[:, crop["x"] + crop["w"] :] = hud_red            # right buttons
        frame[:, : crop["x"]] = hud_red                        # left edge
        name = f"frame_{ix:02d}_00.png"
        cv2.imwrite(str(tmp_path / name), frame)
        frames[f"{ix:02d}_00"] = {"ix": ix, "iy": 0, "file": name}

    manifest = {
        "config": {
            "overlap": 0.5,
            "stitch_viewport": {"w": frame_w, "h": frame_h},
            "crop": crop,
        },
        "frames": frames,
    }
    (tmp_path / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    canvas = cv2.imread(str(run_stitch(tmp_path)))

    assert canvas is not None
    red = (canvas[:, :, 2] == 255) & (canvas[:, :, 1] == 0) & (canvas[:, :, 0] == 0)
    assert not red.any()  # no HUD pixels in the map
    # Trimmed to painted content: two crop regions 200px apart. The dark-red
    # HUD stripes also trip the content mask, whose dilation nibbles a few
    # border pixels — hence the loose tolerance.
    assert canvas.shape[1] == pytest.approx(200 + crop["w"], abs=8)
    assert canvas.shape[0] == pytest.approx(crop["h"], abs=8)


def test_run_stitch_measures_isometric_grid_basis(tmp_path) -> None:
    """A minimap grid step moves the screen diagonally — the stitcher must
    measure the real right/down screen vectors instead of assuming axis-aligned
    steps."""
    world = _make_world(11, 900, 1100)
    frame_w, frame_h = 480, 360
    right = (100, 50)   # screen shift per ix+1 (diagonal: isometry)
    down = (-50, 90)    # screen shift per iy+1

    base_x, base_y = 200, 150
    offsets = {}
    frames = {}
    for ix, iy in [(0, 0), (1, 0), (0, 1), (1, 1)]:
        px = ix * right[0] + iy * down[0]
        py = ix * right[1] + iy * down[1]
        offsets[(ix, iy)] = (px, py)
        name = f"frame_{ix:02d}_{iy:02d}.png"
        window = world[
            base_y + py : base_y + py + frame_h,
            base_x + px : base_x + px + frame_w,
        ]
        cv2.imwrite(str(tmp_path / name), window)
        frames[f"{ix:02d}_{iy:02d}"] = {"ix": ix, "iy": iy, "file": name}

    manifest = {
        "config": {
            "overlap": 0.5,
            # Deliberately wrong axis-aligned geometry: the measured basis
            # must override it.
            "stitch_viewport": {"w": frame_w, "h": frame_h},
        },
        "frames": frames,
    }
    (tmp_path / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    out = run_stitch(tmp_path)

    canvas = cv2.imread(str(out))
    assert canvas is not None
    min_x = min(px for px, _ in offsets.values())
    min_y = min(py for _, py in offsets.values())
    expected_w = max(px for px, _ in offsets.values()) - min_x + frame_w
    expected_h = max(py for _, py in offsets.values()) - min_y + frame_h
    # ORB registration is subpixel-accurate; allow ±2 px of rounding.
    assert canvas.shape[1] == pytest.approx(expected_w, abs=2)
    assert canvas.shape[0] == pytest.approx(expected_h, abs=2)
    located = {
        cell: _locate(
            canvas,
            world[
                base_y + py : base_y + py + frame_h,
                base_x + px : base_x + px + frame_w,
            ],
        )
        for cell, (px, py) in offsets.items()
    }
    origin = located[(0, 0)]
    for cell, (px, py) in offsets.items():
        assert located[cell][0] - origin[0] == pytest.approx(px, abs=2), cell
        assert located[cell][1] - origin[1] == pytest.approx(py, abs=2), cell
