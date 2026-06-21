"""Tile pyramid: zoom math, coverage, padding, idempotency."""

import json
import os

import pytest
from PIL import Image

from modules.radar.tiles import TILES_META_NAME, generate_tiles, max_zoom_for


class TestMaxZoom:
    @pytest.mark.parametrize(
        ("w", "h", "expected"),
        [
            (256, 256, 0),
            (257, 100, 1),
            (512, 512, 1),
            (600, 500, 2),
            (7000, 8800, 6),
        ],
    )
    def test_levels(self, w, h, expected):
        assert max_zoom_for(w, h) == expected

    def test_invalid_dims_raise(self):
        with pytest.raises(ValueError, match="positive"):
            max_zoom_for(0, 100)


class TestGenerateTiles:
    def _make_run(self, tmp_path, w=600, h=500):
        Image.new("RGB", (w, h), (30, 60, 90)).save(tmp_path / "map_full.png")
        return tmp_path

    def test_pyramid_structure(self, tmp_path):
        run = self._make_run(tmp_path)
        meta = generate_tiles(run)
        assert meta == {"width": 600, "height": 500, "min_zoom": 0, "max_zoom": 2, "tile_size": 256}
        # z2 native: 3×2 tiles; z1 300×250: 2×1; z0 150×125: 1×1.
        assert sorted(p.relative_to(run).as_posix() for p in run.glob("tiles/2/*/*.png")) == [
            "tiles/2/0/0.png", "tiles/2/0/1.png",
            "tiles/2/1/0.png", "tiles/2/1/1.png",
            "tiles/2/2/0.png", "tiles/2/2/1.png",
        ]
        assert len(list(run.glob("tiles/1/*/*.png"))) == 2
        assert len(list(run.glob("tiles/0/*/*.png"))) == 1
        assert json.loads((run / TILES_META_NAME).read_text()) == meta

    def test_every_tile_is_full_size(self, tmp_path):
        run = self._make_run(tmp_path)
        generate_tiles(run)
        for tile_path in run.glob("tiles/**/*.png"):
            with Image.open(tile_path) as tile:
                assert tile.size == (256, 256), tile_path

    def test_idempotent_rerun_skips_existing(self, tmp_path):
        run = self._make_run(tmp_path)
        generate_tiles(run)
        marker = run / "tiles" / "0" / "0" / "0.png"
        before = marker.stat().st_mtime_ns
        generate_tiles(run)
        assert marker.stat().st_mtime_ns == before

    def test_rerun_rewrites_tiles_when_stitched_map_changed(self, tmp_path):
        run = self._make_run(tmp_path)
        generate_tiles(run)
        marker = run / "tiles" / "0" / "0" / "0.png"
        before = marker.stat().st_mtime_ns

        src = run / "map_full.png"
        Image.new("RGB", (600, 500), (90, 60, 30)).save(src)
        os.utime(src, ns=(before + 1_000_000_000, before + 1_000_000_000))
        generate_tiles(run)

        assert marker.stat().st_mtime_ns > before

    def test_missing_map_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="stitched"):
            generate_tiles(tmp_path)
