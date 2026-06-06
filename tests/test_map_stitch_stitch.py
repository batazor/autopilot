from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np

from tools.map_stitch import stitch as stitch_mod

if TYPE_CHECKING:
    from pathlib import Path


def test_stitch_grid_mosaic_keeps_all_synthetic_frames(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    output = tmp_path / "map_full.png"

    frame_h, frame_w = 480, 360
    crop = stitch_mod._viewport_crop(np.zeros((frame_h, frame_w, 3), np.uint8))
    right = (170, 12)
    down = (-8, 220)
    rows, cols = 2, 3

    base_w = crop.width + right[0] * (cols - 1) + abs(down[0]) * (rows - 1) + 20
    base_h = crop.height + down[1] * (rows - 1) + right[1] * (cols - 1) + 20
    rng = np.random.default_rng(123)
    base = rng.integers(0, 255, (base_h, base_w, 3), dtype=np.uint8)
    base = cv2.GaussianBlur(base, (5, 5), 0)

    for r in range(rows):
        for c in range(cols):
            x = c * right[0] + r * down[0] + abs(down[0])
            y = c * right[1] + r * down[1]
            frame = np.full((frame_h, frame_w, 3), 40, np.uint8)
            frame[crop.y0:crop.y1, crop.x0:crop.x1] = base[
                y:y + crop.height,
                x:x + crop.width,
            ]
            cv2.imwrite(str(frames_dir / f"frame_{r}_{c}.png"), frame)

    stitch_mod.stitch(frames_dir=frames_dir, output=output)

    result = cv2.imread(str(output), cv2.IMREAD_COLOR)
    assert result is not None
    assert result.shape[1] > crop.width * 1.5
    assert result.shape[0] > crop.height * 1.2
