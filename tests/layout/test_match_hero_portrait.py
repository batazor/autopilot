"""``navigation.hero_grid_search.match_hero_portrait`` — reusable portrait id.

The same grayscale-NCC matching the heroes-grid scan uses, exposed for screens
where the same hero portraits appear in a different layout (e.g. the hero Details
popup). Verified against the real wiki portrait library (``db/assets/wiki/heroes``)
by re-rendering each portrait at on-screen scale (~150 px) and matching it back.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from config.paths import repo_root
from navigation.hero_grid_search import _all_hero_ids, match_hero_portrait

_WIKI = repo_root() / "db" / "assets" / "wiki" / "heroes"


def _portrait_at(hero_id: str, px: int = 150) -> np.ndarray | None:
    """That hero's wiki portrait rendered at ``px`` square (simulates on-screen)."""
    imgs = sorted((_WIKI / hero_id).glob("*.*"))
    img = cv2.imread(str(imgs[0])) if imgs else None
    return None if img is None else cv2.resize(img, (px, px), interpolation=cv2.INTER_AREA)


_SAMPLE = [h for h in ("molly", "bahiti", "jeronimo", "natalia", "sergey", "gina") if h in _all_hero_ids()]


@pytest.mark.parametrize("hero_id", _SAMPLE)
def test_self_match_identifies_the_hero(hero_id: str):
    patch = _portrait_at(hero_id)
    assert patch is not None, f"no wiki portrait for {hero_id}"
    res = match_hero_portrait(patch)
    assert res is not None
    hit, score = res
    assert hit == hero_id
    assert score > 0.9


def test_does_not_confuse_two_heroes():
    if len(_SAMPLE) < 2:
        pytest.skip("need two sampled heroes")
    a, b = _SAMPLE[0], _SAMPLE[1]
    res = match_hero_portrait(_portrait_at(a), hero_ids=(a, b))
    assert res is not None and res[0] == a


def test_portrait_inside_a_larger_window_is_found():
    # the Details crop may include card border around the portrait — the slide finds it
    hero = _SAMPLE[0]
    win = np.full((176, 176, 3), 28, np.uint8)
    win[13:163, 13:163] = _portrait_at(hero)
    res = match_hero_portrait(win)
    assert res is not None and res[0] == hero


def test_noise_patch_returns_none():
    rng = np.random.default_rng(0)
    noise = rng.integers(0, 255, (150, 150, 3), dtype=np.uint8)
    assert match_hero_portrait(noise) is None


def test_patch_smaller_than_template_is_skipped():
    tiny = np.full((40, 40, 3), 100, np.uint8)
    assert match_hero_portrait(tiny) is None
