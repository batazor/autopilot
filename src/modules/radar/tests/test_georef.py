"""Unit tests for the pure game↔canvas affine helpers."""

from __future__ import annotations

import numpy as np
import pytest

from modules.radar.georef import affine_from_meta, invert_affine

_LINEAR = ((5.0, 0.5), (-0.4, 4.0))
_OFFSET = (1200.0, 850.0)


def test_invert_affine_round_trips() -> None:
    inv_linear, inv_offset = invert_affine(_LINEAR, _OFFSET)
    rng = np.random.default_rng(0)
    for gx, gy in rng.uniform(0, 1200, size=(20, 2)):
        cx = _LINEAR[0][0] * gx + _LINEAR[0][1] * gy + _OFFSET[0]
        cy = _LINEAR[1][0] * gx + _LINEAR[1][1] * gy + _OFFSET[1]
        bx = inv_linear[0][0] * cx + inv_linear[0][1] * cy + inv_offset[0]
        by = inv_linear[1][0] * cx + inv_linear[1][1] * cy + inv_offset[1]
        assert bx == pytest.approx(gx, abs=1e-6)
        assert by == pytest.approx(gy, abs=1e-6)


def test_invert_affine_rejects_singular() -> None:
    with pytest.raises(ValueError, match="singular"):
        invert_affine(((1.0, 2.0), (2.0, 4.0)), (0.0, 0.0))  # rank-1


def test_affine_from_meta_present_and_absent() -> None:
    meta = {
        "game_to_canvas_linear": [[5.0, 0.5], [-0.4, 4.0]],
        "game_to_canvas_offset": [1200.0, 850.0],
    }
    got = affine_from_meta(meta)
    assert got is not None
    linear, offset = got
    assert linear == _LINEAR
    assert offset == _OFFSET
    # Linear-only (origin never anchored) → not fully pinned → None.
    assert affine_from_meta({"game_to_canvas_linear": [[5.0, 0.0], [0.0, 5.0]]}) is None
    assert affine_from_meta({}) is None
