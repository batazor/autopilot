"""Pure-math tests for the radar grid geometry (no device, no I/O)."""

import itertools

import pytest

from modules.radar.geometry import (
    Affine,
    Corners,
    diamond_center,
    extend_grid_below,
    generate_grid,
    generate_raster_grid,
    limit_grid_centered,
    limit_grid_from_bottom,
    point_in_diamond,
    scan_walk_from_bottom,
)

# Unit-ish diamond: 200×200 bounding box centered at (100, 100).
DIAMOND = Corners(top=(100.0, 0.0), right=(200.0, 100.0), bottom=(100.0, 200.0), left=(0.0, 100.0))


class TestRasterGrid:
    def test_shape_and_indices(self):
        grid = generate_raster_grid((100.0, 200.0), cols=3, rows=2, step_x=50.0, step_y=40.0)
        assert len(grid) == 6
        assert {(p.ix, p.iy) for p in grid} == {(ix, iy) for iy in range(2) for ix in range(3)}

    def test_origin_is_start_and_steps(self):
        # Cell (0,0) sits exactly at the start view; steps are the given screen px.
        grid = {(p.ix, p.iy): (p.x, p.y) for p in generate_raster_grid(
            (100.0, 200.0), cols=3, rows=2, step_x=50.0, step_y=40.0)}
        assert grid[(0, 0)] == (100.0, 200.0)
        assert grid[(2, 0)] == (200.0, 200.0)
        assert grid[(0, 1)] == (100.0, 240.0)

    def test_serpentine_order(self):
        # Even rows L→R, odd rows R→L; consecutive cells are always one step apart.
        seq = [(p.ix, p.iy) for p in generate_raster_grid(
            (0.0, 0.0), cols=3, rows=2, step_x=10.0, step_y=10.0)]
        assert seq == [(0, 0), (1, 0), (2, 0), (2, 1), (1, 1), (0, 1)]

    def test_consecutive_steps_overlap_one_cell(self):
        # Every move between captures is a single grid step → frames overlap.
        pts = generate_raster_grid((0.0, 0.0), cols=4, rows=3, step_x=10.0, step_y=10.0)
        for a, b in itertools.pairwise(pts):
            assert abs(a.x - b.x) + abs(a.y - b.y) == pytest.approx(10.0)

    @pytest.mark.parametrize("bad", [(0, 2, 5.0, 5.0), (2, 0, 5.0, 5.0), (2, 2, 0.0, 5.0), (2, 2, 5.0, -1.0)])
    def test_rejects_non_positive(self, bad):
        cols, rows, sx, sy = bad
        with pytest.raises(ValueError, match="positive"):
            generate_raster_grid((0.0, 0.0), cols=cols, rows=rows, step_x=sx, step_y=sy)


class TestPointInDiamond:
    def test_center_is_inside(self):
        assert point_in_diamond((100.0, 100.0), DIAMOND)

    @pytest.mark.parametrize("p", [(0.0, 0.0), (200.0, 0.0), (0.0, 200.0), (200.0, 200.0), (49.0, 49.0)])
    def test_bounding_box_corners_are_outside(self, p):
        assert not point_in_diamond(p, DIAMOND)

    def test_diamond_corners_are_on_the_edge(self):
        # Edge points count as inside without inset…
        assert point_in_diamond(DIAMOND.top, DIAMOND)
        assert point_in_diamond(DIAMOND.left, DIAMOND)

    def test_inset_excludes_near_edge_points(self):
        # …and are excluded once any inset applies.
        assert not point_in_diamond(DIAMOND.top, DIAMOND, inset_x=10, inset_y=10)
        # Point just inside the top edge: inside without inset, outside with.
        p = (100.0, 5.0)
        assert point_in_diamond(p, DIAMOND)
        assert not point_in_diamond(p, DIAMOND, inset_x=0, inset_y=10)

    def test_overlarge_inset_collapses_diamond(self):
        # Inset bigger than the half-diagonals inverts the quad — nothing inside.
        assert not point_in_diamond((100.0, 100.0), DIAMOND, inset_x=150, inset_y=150)


class TestGenerateGrid:
    def test_count_on_synthetic_diamond(self):
        # rect 100×100, overlap 0 → the centered safe raster has only the
        # diamond center. That avoids anchoring sparse scans to a corner.
        grid = generate_grid(DIAMOND, 100, 100, overlap=0.0)
        assert len(grid) == 1
        only = grid[0]
        assert (only.x, only.y) == (100.0, 100.0)
        assert (only.ix, only.iy) == (0, 0)

    def test_all_points_inside_inset_diamond(self):
        grid = generate_grid(DIAMOND, 40, 30, overlap=0.25)
        assert grid
        for p in grid:
            assert point_in_diamond((p.x, p.y), DIAMOND, inset_x=20, inset_y=15)

    def test_edge_margin_expands_toward_diamond_edges(self):
        safe = generate_grid(DIAMOND, 40, 30, overlap=0.25)
        edge = generate_grid(DIAMOND, 40, 30, overlap=0.25, edge_margin_px=4)
        assert len(edge) > len(safe)
        assert min(p.y for p in edge) < min(p.y for p in safe)
        assert max(p.y for p in edge) > max(p.y for p in safe)
        for p in edge:
            assert point_in_diamond((p.x, p.y), DIAMOND, inset_x=4, inset_y=4)

    def test_matches_bruteforce_count(self):
        rect_w, rect_h, overlap = 40.0, 30.0, 0.25
        step_x, step_y = rect_w * (1 - overlap), rect_h * (1 - overlap)
        cx, cy = diamond_center(DIAMOND)
        xs = [
            cx + k * step_x
            for k in range(-10, 11)
            if DIAMOND.left[0] + rect_w / 2 <= cx + k * step_x <= DIAMOND.right[0] - rect_w / 2
        ]
        ys = [
            cy + k * step_y
            for k in range(-10, 11)
            if DIAMOND.top[1] + rect_h / 2 <= cy + k * step_y <= DIAMOND.bottom[1] - rect_h / 2
        ]
        expected = sum(
            point_in_diamond((x, y), DIAMOND, rect_w / 2, rect_h / 2)
            for x in xs
            for y in ys
        )
        assert len(generate_grid(DIAMOND, rect_w, rect_h, overlap=overlap)) == expected

    def test_serpentine_order(self):
        grid = generate_grid(DIAMOND, 30, 30, overlap=0.5)
        rows: dict[int, list[int]] = {}
        row_order: list[int] = []
        for p in grid:
            if p.iy not in rows:
                rows[p.iy] = []
                row_order.append(p.iy)
            rows[p.iy].append(p.ix)
        # Rows are visited top to bottom…
        assert row_order == sorted(row_order)
        # …even rows left→right, odd rows right→left.
        for iy, ixs in rows.items():
            expected = sorted(ixs, reverse=bool(iy % 2))
            assert ixs == expected, f"row {iy} not serpentine: {ixs}"

    def test_indices_are_raster_stable(self):
        # The same (ix, iy) must mean the same x/y regardless of row direction.
        grid = generate_grid(DIAMOND, 30, 30, overlap=0.5)
        step = 15.0
        origin_x = {round(p.x - p.ix * step, 6) for p in grid}
        origin_y = {round(p.y - p.iy * step, 6) for p in grid}
        assert len(origin_x) == 1
        assert len(origin_y) == 1
        ox = origin_x.pop()
        oy = origin_y.pop()
        for p in grid:
            assert p.x == pytest.approx(ox + p.ix * step)
            assert p.y == pytest.approx(oy + p.iy * step)

    def test_invalid_args_raise(self):
        with pytest.raises(ValueError, match="rect"):
            generate_grid(DIAMOND, 0, 10)
        with pytest.raises(ValueError, match="overlap"):
            generate_grid(DIAMOND, 10, 10, overlap=1.0)
        with pytest.raises(ValueError, match="edge margin"):
            generate_grid(DIAMOND, 10, 10, edge_margin_px=-1)


class TestGridLimit:
    def test_keeps_centered_window(self):
        grid = generate_grid(DIAMOND, 40, 30, overlap=0.25, edge_margin_px=4)
        limited = limit_grid_centered(grid, DIAMOND, cols=2, rows=3)

        assert len(limited) == 6
        ixs = sorted({p.ix for p in limited})
        iys = sorted({p.iy for p in limited})
        assert len(ixs) == 2
        assert len(iys) == 3
        # The window contains the cell closest to the diamond center.
        cx, cy = diamond_center(DIAMOND)
        anchor = min(grid, key=lambda p: (p.x - cx) ** 2 + (p.y - cy) ** 2)
        assert anchor.ix in ixs
        assert anchor.iy in iys
        # Contiguous index ranges — one rectangular block, no holes.
        assert ixs == list(range(ixs[0], ixs[0] + 2))
        assert iys == list(range(iys[0], iys[0] + 3))

    def test_window_larger_than_grid_keeps_everything(self):
        grid = generate_grid(DIAMOND, 40, 30, overlap=0.25, edge_margin_px=4)
        limited = limit_grid_centered(grid, DIAMOND, cols=99, rows=99)
        assert limited == grid

    def test_from_bottom_takes_whole_rows_upward(self):
        grid = generate_grid(DIAMOND, 40, 30, overlap=0.25, edge_margin_px=4)
        max_iy = max(p.iy for p in grid)
        full_bottom = sorted(p.ix for p in grid if p.iy == max_iy)
        # A budget for the whole bottom row plus part of the next.
        wedge = limit_grid_from_bottom(grid, max_frames=len(full_bottom) + 3)

        full_by_row = {iy: sorted(p.ix for p in grid if p.iy == iy)
                       for iy in {p.iy for p in wedge}}
        kept_by_row: dict[int, list[int]] = {}
        for p in wedge:
            kept_by_row.setdefault(p.iy, []).append(p.ix)
        # Bottom row taken in full; every row but the topmost kept one is full.
        assert sorted(kept_by_row[max_iy]) == full_bottom
        top_iy = min(kept_by_row)
        for iy, ixs in kept_by_row.items():
            if iy != top_iy:
                assert sorted(ixs) == full_by_row[iy]  # complete row
        # Rows are contiguous from the bottom — no skipped row in between.
        assert set(kept_by_row) == set(range(top_iy, max_iy + 1))
        # Connected: every cell touches another kept cell.
        kept = {(p.ix, p.iy) for p in wedge}
        for ix, iy in kept:
            assert any((ix + dx, iy + dy) in kept for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)))

    def test_from_bottom_caps_at_max_frames(self):
        grid = generate_grid(DIAMOND, 40, 30, overlap=0.25, edge_margin_px=4)
        assert len(limit_grid_from_bottom(grid, max_frames=3)) == 3
        assert limit_grid_from_bottom([], max_frames=5) == []

    def test_extend_grid_below_adds_a_clamped_vertex_row(self):
        grid = generate_grid(DIAMOND, 40, 30, overlap=0.25, edge_margin_px=4)
        max_iy = max(p.iy for p in grid)
        base_row = sorted((p for p in grid if p.iy == max_iy), key=lambda p: p.ix)
        step_y = 30 * 0.75

        extended = extend_grid_below(grid, DIAMOND, step_y=step_y, rows=2)

        added = [p for p in extended if p.iy > max_iy]
        # First extra row steps down by step_y (or clamps at the vertex y=200).
        ys = sorted({p.y for p in added})
        assert ys[0] == pytest.approx(min(base_row[0].y + step_y, 200.0))
        assert max(ys) <= 200.0  # never below the minimap's bottom vertex
        # Extra rows reuse the lowest row's x positions and continue iy.
        assert {p.ix for p in added} <= {p.ix for p in base_row}
        # Tapers with the diamond: an added row never out-counts the row above,
        # and the bare tip keeps exactly one cell (nearest the vertex x).
        for iy in sorted({p.iy for p in added}):
            row = [p for p in added if p.iy == iy]
            assert len(row) <= len(base_row)
            if all(p.y == pytest.approx(200.0) for p in row):
                assert len(row) == 1
        # Clamping stops duplicates: rows that would land on the same y are cut.
        assert len({(p.ix, p.iy) for p in extended}) == len(extended)

    def test_extend_grid_below_inset_keeps_off_the_bare_tip(self):
        """A tap on the vertex itself teleports into the neighbouring state —
        the overscan floor must stay inset_px above it."""
        grid = generate_grid(DIAMOND, 40, 30, overlap=0.25, edge_margin_px=4)
        extended = extend_grid_below(grid, DIAMOND, step_y=22.5, rows=3, inset_px=6.0)
        assert max(p.y for p in extended) <= 200.0 - 6.0

    def test_extend_grid_below_noop_for_zero_rows(self):
        grid = generate_grid(DIAMOND, 40, 30, overlap=0.25, edge_margin_px=4)
        assert extend_grid_below(grid, DIAMOND, step_y=22.5, rows=0) == grid

    def test_from_bottom_skip_rows_starts_higher(self):
        grid = generate_grid(DIAMOND, 40, 30, overlap=0.25, edge_margin_px=4)
        bottom_iy = max(p.iy for p in grid)

        base = limit_grid_from_bottom(grid, max_frames=6)
        skipped = limit_grid_from_bottom(grid, max_frames=6, skip_rows=1)

        assert max(p.iy for p in base) == bottom_iy            # tip included
        assert max(p.iy for p in skipped) == bottom_iy - 1     # lowest row dropped
        assert skipped[0].iy == bottom_iy - 1                  # wedge re-anchored

    def test_scan_walk_is_row_by_row_single_steps(self):
        grid = generate_grid(DIAMOND, 40, 30, overlap=0.25, edge_margin_px=4)
        wedge = limit_grid_from_bottom(grid, max_frames=10)
        walk = scan_walk_from_bottom(wedge)

        # Starts on the bottom-most row and captures every wedge cell once.
        assert walk[0][0].iy == max(p.iy for p in wedge)
        captured = [p for p, cap in walk if cap]
        assert len(captured) == len(wedge)
        assert {(p.ix, p.iy) for p in captured} == {(p.ix, p.iy) for p in wedge}
        # Row-major: a row is finished before the next is touched, bottom → top
        # (iy strictly decreasing as new rows are entered — never back down).
        first_capture_iy = [p.iy for p, cap in walk if cap]
        row_starts = [iy for i, iy in enumerate(first_capture_iy)
                      if i == 0 or iy != first_capture_iy[i - 1]]
        assert row_starts == sorted(row_starts, reverse=True)
        assert len(row_starts) == len(set(row_starts))  # each row entered once
        # Every camera move is a single grid step — no no-overlap jump.
        for (a, _), (b, _) in itertools.pairwise(walk):
            assert abs(a.ix - b.ix) + abs(a.iy - b.iy) == 1
        # Backtrack steps only revisit already-captured cells.
        seen: set[tuple[int, int]] = set()
        for p, cap in walk:
            if cap:
                seen.add((p.ix, p.iy))
            else:
                assert (p.ix, p.iy) in seen
        # No wasted trailing backtracks.
        assert walk[-1][1] is True

    def test_scan_walk_empty(self):
        assert scan_walk_from_bottom([]) == []


class TestAffine:
    def test_corner_mapping(self):
        affine = Affine.from_corners(DIAMOND, game_size=1200)
        assert affine.to_game(DIAMOND.top) == pytest.approx((0.0, 0.0), abs=1e-9)
        assert affine.to_game(DIAMOND.right) == pytest.approx((1199.0, 0.0), abs=1e-9)
        assert affine.to_game(DIAMOND.bottom) == pytest.approx((1199.0, 1199.0), abs=1e-9)
        assert affine.to_game(DIAMOND.left) == pytest.approx((0.0, 1199.0), abs=1e-9)

    def test_center_maps_to_game_center(self):
        affine = Affine.from_corners(DIAMOND, game_size=1200)
        cx, cy = diamond_center(DIAMOND)
        assert affine.to_game((cx, cy)) == pytest.approx((599.5, 599.5))

    def test_round_trip(self):
        affine = Affine.from_corners(DIAMOND, game_size=1200)
        for px in [(100.0, 100.0), (80.0, 60.0), (133.3, 140.7), DIAMOND.top]:
            gx, gy = affine.to_game(px)
            assert affine.to_minimap(gx, gy) == pytest.approx(px, abs=1e-6)

    def test_round_trip_from_game_coords(self):
        affine = Affine.from_corners(DIAMOND, game_size=1200)
        for gx, gy in [(0.0, 0.0), (599.5, 599.5), (1199.0, 0.0), (250.25, 871.5)]:
            px = affine.to_minimap(gx, gy)
            assert affine.to_game(px) == pytest.approx((gx, gy), abs=1e-6)

    def test_degenerate_corners_raise(self):
        flat = Corners(top=(0.0, 0.0), right=(1.0, 0.0), bottom=(2.0, 0.0), left=(3.0, 0.0))
        with pytest.raises(ValueError, match="degenerate"):
            Affine.from_corners(flat)
