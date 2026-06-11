"""Pure geometry for the minimap scan: diamond tests, tap grid, affine transform.

Everything here is deterministic math over plain tuples — no I/O, no OpenCV —
so it stays fully unit-testable (see ``tests/test_geometry.py``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np

Vec = tuple[float, float]


class Corners(NamedTuple):
    """Minimap diamond corners in absolute screen pixels."""

    top: Vec
    right: Vec
    bottom: Vec
    left: Vec


@dataclass(frozen=True, slots=True)
class GridPoint:
    """One tap target: raster indices (the frame key) + absolute screen pixels."""

    ix: int
    iy: int
    x: float
    y: float


def diamond_center(corners: Corners) -> Vec:
    """Centroid of the four diamond corners."""
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (sum(xs) / 4.0, sum(ys) / 4.0)


def inset_corners(corners: Corners, inset_x: float, inset_y: float) -> Corners:
    """Shrink the diamond by moving each corner inward along its axis."""
    (tx, ty), (rx, ry), (bx, by), (lx, ly) = corners
    return Corners(
        top=(tx, ty + inset_y),
        right=(rx - inset_x, ry),
        bottom=(bx, by - inset_y),
        left=(lx + inset_x, ly),
    )


def _cross(o: Vec, a: Vec, b: Vec) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def point_in_diamond(
    p: Vec,
    corners: Corners,
    inset_x: float = 0.0,
    inset_y: float = 0.0,
) -> bool:
    """True when ``p`` lies inside the diamond shrunk by the per-axis insets.

    Four half-plane inequalities over the inset quad; points exactly on an
    edge count as inside. An over-large inset collapses the quad — then
    nothing is inside.
    """
    quad = inset_corners(corners, inset_x, inset_y)
    if quad.top[1] >= quad.bottom[1] or quad.left[0] >= quad.right[0]:
        return False
    pts = (quad.top, quad.right, quad.bottom, quad.left)
    signs = [_cross(pts[i], pts[(i + 1) % 4], p) for i in range(4)]
    return all(s >= 0 for s in signs) or all(s <= 0 for s in signs)


def generate_grid(
    corners: Corners,
    rect_w: float,
    rect_h: float,
    overlap: float = 0.25,
    edge_margin_px: float | None = None,
) -> list[GridPoint]:
    """Serpentine raster of minimap tap points covering the diamond.

    Step is ``rect * (1 - overlap)`` along each axis. Candidate tap positions
    are centered on the diamond center and expand outward, which keeps the
    route balanced around the current viewport instead of anchoring it to the
    top-left bounding box corner. Only points at least ``rect / 2`` away from
    the diamond edges are kept, so the camera is never clamped against the
    kingdom border. ``edge_margin_px`` can reduce that inset for intentional
    edge scans. Even rows run left→right, odd rows right→left; ``(ix, iy)`` are
    stable raster indices across the reversal.
    """
    if rect_w <= 0 or rect_h <= 0:
        msg = f"viewport rect must be positive, got {rect_w}×{rect_h}"
        raise ValueError(msg)
    if not 0.0 <= overlap < 1.0:
        msg = f"overlap must be in [0, 1), got {overlap}"
        raise ValueError(msg)
    step_x = rect_w * (1.0 - overlap)
    step_y = rect_h * (1.0 - overlap)
    min_x, max_x = corners.left[0], corners.right[0]
    min_y, max_y = corners.top[1], corners.bottom[1]
    if edge_margin_px is not None and edge_margin_px < 0:
        msg = f"edge margin must be non-negative, got {edge_margin_px}"
        raise ValueError(msg)
    inset_x = rect_w / 2.0 if edge_margin_px is None else edge_margin_px
    inset_y = rect_h / 2.0 if edge_margin_px is None else edge_margin_px
    cx, cy = diamond_center(corners)
    xs = _centered_axis_positions(min_x + inset_x, max_x - inset_x, cx, step_x)
    ys = _centered_axis_positions(min_y + inset_y, max_y - inset_y, cy, step_y)
    grid: list[GridPoint] = []
    for iy, y in ys:
        row = [
            GridPoint(ix=ix, iy=iy, x=x, y=y)
            for ix, x in xs
            if point_in_diamond((x, y), corners, inset_x, inset_y)
        ]
        if iy % 2 == 1:
            row.reverse()
        grid.extend(row)
    return grid


def limit_grid_centered(
    grid: list[GridPoint],
    corners: Corners,
    cols: int,
    rows: int,
) -> list[GridPoint]:
    """Keep only a ``cols×rows`` block of cells nearest the diamond center.

    Debug helper: a full kingdom scan is ~hundreds of frames; a small centered
    window iterates much faster. The window is anchored on the cell closest to
    the minimap center, so it scans where the camera already is. Cells the
    diamond filter already dropped stay dropped — the result may hold fewer
    than ``cols·rows`` points near the kingdom edge.
    """
    if not grid:
        return grid
    cx, cy = diamond_center(corners)
    anchor = min(grid, key=lambda p: ((p.x - cx) ** 2 + (p.y - cy) ** 2, p.iy, p.ix))
    x0 = anchor.ix - (cols - 1) // 2
    y0 = anchor.iy - (rows - 1) // 2
    return [
        p for p in grid
        if x0 <= p.ix < x0 + cols and y0 <= p.iy < y0 + rows
    ]


def order_grid_center_first(grid: list[GridPoint], corners: Corners) -> list[GridPoint]:
    """Greedy nearest-neighbor route starting at the minimap center.

    Swipe navigation is relative: the first captured frame is wherever the
    user has positioned the camera. Starting at the grid cell closest to the
    minimap center keeps that assumption aligned with the manifest indices,
    then the nearest-neighbor walk keeps each camera move short.
    """
    if not grid:
        return []
    cx, cy = diamond_center(corners)
    remaining = list(grid)
    current = min(remaining, key=lambda p: ((p.x - cx) ** 2 + (p.y - cy) ** 2, p.iy, p.ix))
    remaining.remove(current)
    route = [current]
    while remaining:
        current = min(
            remaining,
            key=lambda p: (
                (p.x - current.x) ** 2 + (p.y - current.y) ** 2,
                abs(p.iy - current.iy),
                abs(p.ix - current.ix),
                p.iy,
                p.ix,
            ),
        )
        remaining.remove(current)
        route.append(current)
    return route


def _centered_axis_positions(
    min_pos: float,
    max_pos: float,
    center: float,
    step: float,
) -> list[tuple[int, float]]:
    if min_pos > max_pos:
        return []
    k_min = math.ceil((min_pos - center) / step)
    k_max = math.floor((max_pos - center) / step)
    return [(k - k_min, center + k * step) for k in range(k_min, k_max + 1)]


@dataclass(frozen=True, slots=True)
class Affine:
    """Transform between minimap pixels and game coordinates.

    Built from the correspondence of the diamond corners to the game grid
    square: top → (0, 0), right → (G-1, 0), bottom → (G-1, G-1),
    left → (0, G-1) where G is the kingdom size (1200 for WoS).
    """

    # gx = a·px + b·py + c ; gy = d·px + e·py + f  (and the inverse likewise)
    fwd: tuple[float, float, float, float, float, float]
    inv: tuple[float, float, float, float, float, float]

    @classmethod
    def from_corners(cls, corners: Corners, game_size: int = 1200) -> Affine:
        g = float(game_size - 1)
        src = np.array(
            [
                [*corners.top, 1.0],
                [*corners.right, 1.0],
                [*corners.bottom, 1.0],
                [*corners.left, 1.0],
            ]
        )
        dst = np.array([[0.0, 0.0], [g, 0.0], [g, g], [0.0, g]])
        sol, *_ = np.linalg.lstsq(src, dst, rcond=None)
        a, d = float(sol[0][0]), float(sol[0][1])
        b, e = float(sol[1][0]), float(sol[1][1])
        c, f = float(sol[2][0]), float(sol[2][1])
        det = a * e - b * d
        if abs(det) < 1e-12:
            msg = "degenerate diamond corners (zero-area minimap)"
            raise ValueError(msg)
        ia, ib = e / det, -b / det
        i_d, ie = -d / det, a / det
        ic = -(ia * c + ib * f)
        i_f = -(i_d * c + ie * f)
        return cls(fwd=(a, b, c, d, e, f), inv=(ia, ib, ic, i_d, ie, i_f))

    def to_game(self, px: Vec) -> Vec:
        """Minimap pixel → game (x, y)."""
        a, b, c, d, e, f = self.fwd
        return (a * px[0] + b * px[1] + c, d * px[0] + e * px[1] + f)

    def to_minimap(self, x: float, y: float) -> Vec:
        """Game (x, y) → minimap pixel."""
        a, b, c, d, e, f = self.inv
        return (a * x + b * y + c, d * x + e * y + f)
