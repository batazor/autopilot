"""Unit test for the chevron-walk ordering (`_ordered_hids`).

The walk identifies each card by the grid ORDER (not a name OCR), so the
row-major sort of ``hero_grid_positions`` is the contract the routing rests on.
"""

from __future__ import annotations

import numpy as np
from games.wos.heroes.heroes.scan_hero_details import (
    _SEG_PX,
    _STAR_CENTERS,
    _STAR_HALF_W,
    _STAR_Y,
    _ordered_hids,
    detect_star_segments,
)


class _FakeRedis:
    def __init__(self, positions: dict[str, bytes]) -> None:
        self._positions = positions

    async def hgetall(self, _key: str) -> dict[str, bytes]:
        return self._positions


async def test_ordered_hids_is_row_major_and_drops_garbage():
    positions = {
        "gina": b"r0c2",
        "flint": b"r0c0",
        "molly": b"r0c1",
        "bahiti": b"r1c0",
        "natalia": b"r1c1",
        "broken": b"not-a-cell",  # malformed → dropped
    }
    out = await _ordered_hids(_FakeRedis(positions), "bs1")
    # row 0 left→right, then row 1 left→right; the malformed cell is excluded.
    assert out == ["flint", "molly", "gina", "bahiti", "natalia"]


async def test_ordered_hids_empty_when_no_positions():
    assert await _ordered_hids(_FakeRedis({}), "bs1") == []


def _paint_star(frame, idx, n_px):
    """Paint ~n_px bright-cyan pixels into star ``idx``'s box."""
    h, w = frame.shape[:2]
    y0 = int(_STAR_Y[0] / 100 * h)
    x0 = int((_STAR_CENTERS[idx] - _STAR_HALF_W) / 100 * w)
    rows = max(1, n_px // 20)
    frame[y0 : y0 + rows, x0 : x0 + 20] = (220, 220, 0)  # BGR bright cyan


def test_detect_star_segments_full_partial_empty():
    frame = np.zeros((1280, 720, 3), dtype="uint8")  # all dark → 0 segments
    _paint_star(frame, 0, 5000)         # >> a full star → caps at 6
    _paint_star(frame, 1, 5000)         # full
    _paint_star(frame, 2, _SEG_PX)      # ~1 segment
    # stars 3, 4 left dark → 0
    assert detect_star_segments(frame) == [6, 6, 1, 0, 0]


def test_detect_star_segments_all_empty_is_zero():
    frame = np.zeros((1280, 720, 3), dtype="uint8")
    assert detect_star_segments(frame) == [0, 0, 0, 0, 0]
