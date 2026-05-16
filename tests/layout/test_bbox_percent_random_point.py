from __future__ import annotations

import random

from layout.bbox_percent import (
    bbox_percent_center_to_device_point,
    bbox_percent_random_point_to_device_point,
)


def test_random_point_stays_within_inset_rect() -> None:
    bbox = {"x": 10.0, "y": 20.0, "width": 40.0, "height": 30.0}
    dev_w, dev_h = 1000, 2000
    inset = 0.15

    # Inset rectangle in device pixels.
    x0 = (bbox["x"] + bbox["width"] * inset) / 100.0 * dev_w
    x1 = (bbox["x"] + bbox["width"] - bbox["width"] * inset) / 100.0 * dev_w
    y0 = (bbox["y"] + bbox["height"] * inset) / 100.0 * dev_h
    y1 = (bbox["y"] + bbox["height"] - bbox["height"] * inset) / 100.0 * dev_h

    rng = random.Random(0xC0FFEE)
    for _ in range(500):
        pt = bbox_percent_random_point_to_device_point(
            bbox, dev_w, dev_h, inset_pct=inset, rng=rng
        )
        # ±1 px tolerance for rounding.
        assert x0 - 1 <= pt.x <= x1 + 1, pt
        assert y0 - 1 <= pt.y <= y1 + 1, pt


def test_random_point_full_bbox_when_inset_zero() -> None:
    bbox = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0}
    rng = random.Random(42)
    xs, ys = set(), set()
    for _ in range(200):
        pt = bbox_percent_random_point_to_device_point(
            bbox, 100, 100, inset_pct=0.0, rng=rng
        )
        assert 0 <= pt.x <= 100
        assert 0 <= pt.y <= 100
        xs.add(pt.x)
        ys.add(pt.y)
    # With 200 samples on a 100×100 grid the spread should be wide.
    assert len(xs) > 20
    assert len(ys) > 20


def test_random_point_degenerate_bbox_returns_center() -> None:
    bbox = {"x": 50.0, "y": 50.0, "width": 0.0, "height": 0.0}
    pt = bbox_percent_random_point_to_device_point(bbox, 100, 100)
    expected = bbox_percent_center_to_device_point(bbox, 100, 100)
    assert (pt.x, pt.y) == (expected.x, expected.y)


def test_random_point_extreme_inset_clamped() -> None:
    # inset_pct=0.9 would invert the rect; helper must clamp.
    bbox = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0}
    rng = random.Random(1)
    for _ in range(50):
        pt = bbox_percent_random_point_to_device_point(
            bbox, 100, 100, inset_pct=0.9, rng=rng
        )
        # Clamped to 0.49 → x in roughly [49, 51], y similarly.
        assert 48 <= pt.x <= 52
        assert 48 <= pt.y <= 52
