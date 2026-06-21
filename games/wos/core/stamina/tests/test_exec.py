"""Tests for the stamina fill-bar reader, against the real reference frames.

main_city.png shows a (near-)full bar; main_world/main.png a partial one — so
the same measurement yields a high ratio on one and a mid ratio on the other,
and clearly-not-a-bar regions return None.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from games.wos.core.stamina.exec import bar_fill_ratio

REPO = Path(__file__).resolve().parents[5]
BBOX = (16, 85, 79, 88)  # x0, y0, x1, y1 — matches the stamina.bar region


def _bar(rel: str) -> np.ndarray:
    im = cv2.imread(str(REPO / rel))
    assert im is not None, rel
    x0, y0, x1, y1 = BBOX
    return im[y0:y1, x0:x1]


def test_full_bar_on_main_city():
    r = bar_fill_ratio(_bar("games/wos/core/main_city/references/main_city.png"))
    assert r is not None
    assert r > 0.9


def test_partial_bar_on_main_world():
    r = bar_fill_ratio(_bar("games/wos/core/main_world/references/main.png"))
    assert r is not None
    assert 0.5 < r < 0.85


def test_non_bar_region_returns_none():
    im = cv2.imread(str(REPO / "games/wos/core/main_city/references/main_city.png"))
    # A patch of sky/snow nowhere near the HUD bar → not bar-like.
    assert bar_fill_ratio(im[400:404, 300:380]) is None


def test_degenerate_inputs_return_none():
    assert bar_fill_ratio(None) is None
    assert bar_fill_ratio(np.zeros((0, 0, 3), dtype=np.uint8)) is None
    # All-dark patch (no green) must not read as a false "0".
    assert bar_fill_ratio(np.zeros((3, 60, 3), dtype=np.uint8)) is None
