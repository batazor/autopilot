"""Pure-math tests for the radar grid geometry (no device, no I/O)."""

import pytest

from modules.radar.geometry import (
    Affine,
    Corners,
    diamond_center,
    generate_grid,
    order_grid_center_first,
    point_in_diamond,
)

# Unit-ish diamond: 200×200 bounding box centered at (100, 100).
DIAMOND = Corners(top=(100.0, 0.0), right=(200.0, 100.0), bottom=(100.0, 200.0), left=(0.0, 100.0))


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


class TestSwipeRoute:
    def test_order_grid_center_first_starts_at_center_and_keeps_all_points(self):
        grid = generate_grid(DIAMOND, 40, 30, overlap=0.25, edge_margin_px=4)
        route = order_grid_center_first(grid, DIAMOND)

        assert route[0].x == pytest.approx(100.0)
        assert route[0].y == pytest.approx(100.0)
        assert {(p.ix, p.iy) for p in route} == {(p.ix, p.iy) for p in grid}
        assert len(route) == len(grid)


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
