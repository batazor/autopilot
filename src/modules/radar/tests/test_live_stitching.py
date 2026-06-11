"""Live stitching: the preview rebuilds as frames land during a scan."""

import json
import time

import cv2
import numpy as np

from modules.radar.live import live_stitching
from modules.radar.scanner import MANIFEST_NAME
from modules.radar.stitch import MAP_PREVIEW_NAME
from modules.radar.tests.test_stitch_matching import _make_world


class FakePublisher:
    def __init__(self) -> None:
        self.updates: list[int] = []

    def map_updated(self, frames: int) -> None:
        self.updates.append(frames)


def _write_run(tmp_path, world: np.ndarray, cells: list[tuple[int, int]]) -> None:
    """Manifest + frames for the given cells (axis-aligned 200/150 px steps)."""
    frames = {}
    for ix, iy in cells:
        name = f"frame_{ix:02d}_{iy:02d}.png"
        x, y = ix * 200, iy * 150
        cv2.imwrite(str(tmp_path / name), world[y : y + 300, x : x + 400])
        frames[f"{ix:02d}_{iy:02d}"] = {"ix": ix, "iy": iy, "file": name}
    manifest = {
        "config": {"overlap": 0.5, "stitch_viewport": {"w": 400, "h": 300}},
        "frames": frames,
    }
    tmp = tmp_path / "manifest.tmp"
    tmp.write_text(json.dumps(manifest), encoding="utf-8")
    tmp.replace(tmp_path / MANIFEST_NAME)


def _wait_until(predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_live_stitching_rebuilds_preview_per_frame(tmp_path) -> None:
    world = _make_world(3, 600, 800)
    publisher = FakePublisher()

    with live_stitching(tmp_path, publisher):
        _write_run(tmp_path, world, [(0, 0)])
        assert _wait_until(lambda: publisher.updates == [1])
        assert (tmp_path / MAP_PREVIEW_NAME).is_file()
        first_size = (tmp_path / MAP_PREVIEW_NAME).stat().st_size

        _write_run(tmp_path, world, [(0, 0), (1, 0)])
        assert _wait_until(lambda: publisher.updates == [1, 2])
        # Two frames cover more world → a different (larger) preview.
        assert (tmp_path / MAP_PREVIEW_NAME).stat().st_size != first_size

    # Loop stopped at context exit: no more updates after extra frames appear.
    _write_run(tmp_path, world, [(0, 0), (1, 0), (0, 1)])
    time.sleep(1.2)
    assert publisher.updates == [1, 2]


def test_live_stitching_idles_without_manifest(tmp_path) -> None:
    publisher = FakePublisher()
    with live_stitching(tmp_path, publisher):
        time.sleep(1.2)
    assert publisher.updates == []
    assert not (tmp_path / MAP_PREVIEW_NAME).exists()
