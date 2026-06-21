"""Pure game↔canvas affine helpers (no I/O).

The stitcher persists the forward affine ``game → canvas`` to ``map_meta.json``
(``game_to_canvas_linear`` 2×2 + ``game_to_canvas_offset``). These two helpers
read it back and invert it so a canvas pixel resolves to an absolute game
``(x, y)`` — the substrate of the radar's coordinate readout. The forward
convention mirrors :meth:`modules.radar.geometry.Affine.from_corners`
(``cx = a·gx + b·gy + c`` ; ``cy = d·gx + e·gy + f``).
"""

from __future__ import annotations

import numpy as np

Vec2 = tuple[float, float]
Mat2 = tuple[tuple[float, float], tuple[float, float]]

# Below this |det| the 2×2 basis has collapsed to a line/point — no stable
# inverse (mirrors the guard in ``Affine.from_corners``).
_DEGENERATE = 1e-9


def _as_linear(linear: object) -> np.ndarray:
    m = np.asarray(linear, dtype=float)
    if m.shape != (2, 2):
        msg = f"linear must be 2×2, got shape {m.shape}"
        raise ValueError(msg)
    return m


def invert_affine(linear: object, offset: Vec2) -> tuple[Mat2, Vec2]:
    """Inverse affine taking canvas px → game ``(x, y)``.

    ``game = inv(linear) @ (canvas − offset)`` rewritten as
    ``inv(linear) @ canvas + (−inv(linear) @ offset)`` so a caller (e.g. the
    browser viewer) applies one mat-vec and never inverts a matrix itself.
    Raises on a singular ``linear`` (zero-area basis).
    """
    m = _as_linear(linear)
    if abs(float(np.linalg.det(m))) < _DEGENERATE:
        msg = "singular linear map (zero-area basis) — cannot invert"
        raise ValueError(msg)
    inv = np.linalg.inv(m)
    off = -inv @ np.asarray([float(offset[0]), float(offset[1])], dtype=float)
    inv_linear: Mat2 = (
        (float(inv[0, 0]), float(inv[0, 1])),
        (float(inv[1, 0]), float(inv[1, 1])),
    )
    return inv_linear, (float(off[0]), float(off[1]))


def affine_from_meta(meta: dict) -> tuple[Mat2, Vec2] | None:
    """Pull ``(linear, offset)`` out of a ``map_meta.json`` dict.

    Returns ``None`` when the forward affine is not fully pinned (no
    ``game_to_canvas_offset`` — the origin was never anchored), so callers can
    treat a run as "no coordinates yet". Pure dict access, no file I/O.
    """
    linear = meta.get("game_to_canvas_linear")
    offset = meta.get("game_to_canvas_offset")
    if not linear or not offset:
        return None
    m = _as_linear(linear)
    return (
        ((float(m[0, 0]), float(m[0, 1])), (float(m[1, 0]), float(m[1, 1]))),
        (float(offset[0]), float(offset[1])),
    )
