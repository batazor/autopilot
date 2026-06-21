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


def generate_raster_grid(
    start: Vec,
    cols: int,
    rows: int,
    step_x: float,
    step_y: float,
) -> list[GridPoint]:
    """Plain serpentine raster in absolute screen px — no minimap diamond.

    For views without a minimap world grid (the city interior, event islands):
    cell ``(0, 0)`` is the START view (captured in place), and the route walks
    right then down over ``cols × rows`` in fixed screen-px steps — so the
    operator positions the camera at the TOP-LEFT of the area before scanning.
    The camera moves by swipes; the stitcher derives true frame positions from
    *measured* ORB offsets, so ``x``/``y`` here only feed the inter-cell swipe
    deltas (and a debug layout) — their absolute origin is irrelevant. Even rows
    run left→right, odd rows right→left; ``(ix, iy)`` stay stable across the
    reversal, matching :func:`generate_grid`.
    """
    if cols <= 0 or rows <= 0:
        msg = f"raster must be positive, got {cols}×{rows}"
        raise ValueError(msg)
    if step_x <= 0 or step_y <= 0:
        msg = f"raster step must be positive, got {step_x}×{step_y}"
        raise ValueError(msg)
    sx, sy = start
    grid: list[GridPoint] = []
    for iy in range(rows):
        col_range = range(cols) if iy % 2 == 0 else range(cols - 1, -1, -1)
        grid.extend(
            GridPoint(ix=ix, iy=iy, x=sx + ix * step_x, y=sy + iy * step_y)
            for ix in col_range
        )
    return grid


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


def scale_corners(corners: Corners, factor: float) -> Corners:
    """Expand the diamond about its center by ``factor`` (1.0 = unchanged).

    The scan route's extent is the diamond's bounding box, so scaling the
    corners scales how far the route reaches from the bottom anchor. Used to
    size the route to the TRUE kingdom when the minimap diamond was calibrated
    against a central sub-region only (~1/9 of the real area). Origin
    positioning and the live minimap-rect reading keep the unscaled diamond —
    only the grid coverage (and the move-prior scale) grow.
    """
    if factor == 1.0:
        return corners
    cx, cy = diamond_center(corners)

    def s(p: Vec) -> Vec:
        return (cx + (p[0] - cx) * factor, cy + (p[1] - cy) * factor)

    return Corners(
        top=s(corners.top), right=s(corners.right), bottom=s(corners.bottom), left=s(corners.left),
    )


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


def limit_grid_from_bottom(
    grid: list[GridPoint], max_frames: int | None, skip_rows: int = 0,
) -> list[GridPoint]:
    """Select ``max_frames`` cells as whole rows from the bottom up.

    ``skip_rows`` drops that many of the lowest rows first, so scanning starts
    higher than the bare vertex (which sits on the kingdom edge — dark
    out-of-bounds in frame). Then take full rows bottom→top until the frame
    budget runs out; the last row is partial, centered over the row below it so
    it stays connected. ``max_frames=None`` keeps every row — the scan is then
    expected to end on its own at the top border. Plain row coverage, no fan
    into the middle — the capture route (a row-by-row serpentine) is built by
    :func:`scan_walk_from_bottom`.
    """
    if not grid:
        return grid
    if skip_rows > 0:
        cutoff = max(p.iy for p in grid) - skip_rows
        grid = [p for p in grid if p.iy <= cutoff]
        if not grid:
            return grid
    budget = max_frames if max_frames is not None else len(grid)
    by_cell = {(p.ix, p.iy): p for p in grid}
    rows: dict[int, list[int]] = {}
    for p in grid:
        rows.setdefault(p.iy, []).append(p.ix)
    selected: list[GridPoint] = []
    prev_ixs: list[int] | None = None
    for iy in sorted(rows, reverse=True):  # bottom row first, climbing up
        if len(selected) >= budget:
            break
        ixs = sorted(rows[iy])
        remaining = budget - len(selected)
        if len(ixs) > remaining:
            # Partial top row: keep the cells nearest the row below's center so
            # they sit on already-scanned ground (a contiguous, supported block).
            span = prev_ixs or ixs
            center = (span[0] + span[-1]) / 2
            ixs = sorted(sorted(ixs, key=lambda x: abs(x - center))[:remaining])
        selected.extend(by_cell[(ix, iy)] for ix in ixs)
        prev_ixs = ixs
    return selected


def extend_grid_below(
    grid: list[GridPoint],
    corners: Corners,
    step_y: float,
    rows: int,
    inset_px: float = 0.0,
) -> list[GridPoint]:
    """Add capture rows *below* the diamond-fitted raster, near the vertex.

    The fitted raster is centered on the diamond, so its lowest row can sit a
    sizable fraction of a step above the bottom corner — the scan then never
    quite reaches the kingdom's bottom tip. Each extra row steps down by
    ``step_y``, clamped to ``inset_px`` above the bottom vertex: a tap on the
    bare tip teleports into the *neighbouring* state, so the start must stay a
    little inside our own. Rows taper with the diamond: only the lowest row's
    x positions still inside it are kept, and when none fit the single cell
    nearest the vertex x survives — cells past the taper would point the camera
    outside the kingdom, where the game clamps the pan and the view guard kills
    the scan.
    """
    if not grid or rows <= 0:
        return grid
    vertex_x, bottom_y = corners.bottom
    floor_y = bottom_y - inset_px
    max_iy = max(p.iy for p in grid)
    base = sorted((p for p in grid if p.iy == max_iy), key=lambda p: p.ix)
    out = list(grid)
    prev_y = base[0].y
    for k in range(1, rows + 1):
        y = min(base[0].y + k * step_y, floor_y)
        if y <= prev_y + 1e-6:
            break  # already clamped at the floor — no lower row exists
        keep = [p for p in base if point_in_diamond((p.x, y), corners)]
        if not keep:
            keep = [min(base, key=lambda p: abs(p.x - vertex_x))]
        out.extend(GridPoint(ix=p.ix, iy=max_iy + k, x=p.x, y=y) for p in keep)
        prev_y = y
    return out


def scan_walk_from_bottom(cells: list[GridPoint]) -> list[tuple[GridPoint, bool]]:
    """Row-by-row serpentine over ``cells``, bottom row up — line by line.

    Each row is swept fully before climbing to the next; no detour through the
    middle. A row is entered directly above where the previous one ended (a
    one-step overlapping climb), swept to the near end, then back across to the
    far end. Returns ``(point, capture)`` steps where every consecutive pair is
    grid adjacent (one swipe); the back-across pass re-walks captured cells with
    ``capture=False``, so the camera never makes a no-overlap jump and each new
    frame registers against the adjacent cell it was reached from.
    """
    if not cells:
        return []
    by_cell = {(p.ix, p.iy): p for p in cells}

    # Capture order: full row sweeps, bottom→top, each row entered over the
    # previous row's end so the climb always overlaps.
    rows: dict[int, list[int]] = {}
    for p in cells:
        rows.setdefault(p.iy, []).append(p.ix)
    order: list[GridPoint] = []
    prev_end_ix: int | None = None
    for iy in sorted(rows, reverse=True):
        ixs = sorted(rows[iy])
        if prev_end_ix is None:
            seq = ixs  # first row: straight sweep, left to right
        else:
            entry = min(max(prev_end_ix, ixs[0]), ixs[-1])
            right = [ix for ix in ixs if ix >= entry]
            left = [ix for ix in ixs if ix < entry][::-1]
            seq = right + left
        order.extend(by_cell[(ix, iy)] for ix in seq)
        prev_end_ix = seq[-1]

    # Expand to single steps: between non-adjacent captures, re-walk the cells
    # in between (all already captured) so every move is one overlapping step.
    walk: list[tuple[GridPoint, bool]] = [(order[0], True)]
    visited = {(order[0].ix, order[0].iy)}
    for nxt in order[1:]:
        cur = walk[-1][0]
        ix, iy = cur.ix, cur.iy
        path: list[tuple[int, int]] = []
        while ix != nxt.ix:
            ix += 1 if nxt.ix > ix else -1
            path.append((ix, iy))
        while iy != nxt.iy:
            iy += 1 if nxt.iy > iy else -1
            path.append((ix, iy))
        for key in path:
            capture = key not in visited
            walk.append((by_cell[key], capture))
            visited.add(key)
    return walk


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
