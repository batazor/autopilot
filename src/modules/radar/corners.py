"""Operator-marked kingdom corners → grid constraints for the stitcher.

Auto-detecting the four kingdom vertices is unreliable (the border colour is
ambiguous — salmon over land, blue water, plus foliage/alliance-border noise),
but a human sees them instantly. The operator clicks the four diamond vertices
on the stitched map; each click is a canvas pixel whose *game* coordinate is
known exactly (the kingdom is a fixed ``game_size`` square: top=(0,0),
right=(G-1,0), bottom=(G-1,G-1), left=(0,G-1)).

A click maps to ``(frame_key, frame_px)`` — the corner's position *inside* the
frame that shows it. That intra-frame position is drift-free (``frame_px =
click − frame_canvas_px`` cancels the frame's accumulated placement error), so
it is a sound constraint for the joint position+affine solve
(:func:`modules.radar.stitch_matching._solve_matched_positions`): the marked
corners pull their frames onto the square grid, spreading the drift out.

The sidecar (``corners.json``) persists the mapping so a re-stitch re-applies it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from modules.radar.stitch_georef import MAP_META_NAME

logger = logging.getLogger(__name__)

CORNERS_SIDECAR_NAME = "corners.json"
# Click order the UI collects them in; each maps to a fixed game-grid vertex.
CORNER_ORDER: tuple[str, ...] = ("top", "right", "bottom", "left")


def corner_game_xy(game_size: int) -> dict[str, tuple[int, int]]:
    """The four kingdom vertices in game coordinates for a ``game_size`` square."""
    g = int(game_size) - 1
    return {"top": (0, 0), "right": (g, 0), "bottom": (g, g), "left": (0, g)}


def map_click_to_frame(
    canvas_px: tuple[float, float],
    frames: dict,
    frame_w: int,
    frame_h: int,
) -> tuple[str, tuple[float, float]] | None:
    """Frame whose image shows ``canvas_px`` → ``(frame_key, frame_px)``.

    Among the frames whose full-frame region contains the click, pick the one
    where the click sits nearest the frame centre — that frame sees the corner
    most completely (least chance of it falling in a cut-off margin). ``None``
    when no frame covers the click.
    """
    cx, cy = float(canvas_px[0]), float(canvas_px[1])
    best: tuple[float, str, float, float] | None = None
    for key, fp in frames.items():
        tlx, tly = fp["canvas_px"]
        fpx, fpy = cx - float(tlx), cy - float(tly)
        if 0.0 <= fpx < frame_w and 0.0 <= fpy < frame_h:
            d = (fpx - frame_w / 2.0) ** 2 + (fpy - frame_h / 2.0) ** 2
            if best is None or d < best[0]:
                best = (d, key, fpx, fpy)
    if best is None:
        return None
    return best[1], (round(best[2], 1), round(best[3], 1))


def read_corners(run_dir: str | Path) -> dict | None:
    """The persisted corners sidecar for ``run_dir``, or ``None`` if absent/bad."""
    path = Path(run_dir) / CORNERS_SIDECAR_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_corners(
    run_dir: str | Path,
    clicks: dict[str, tuple[float, float]],
    *,
    game_size: int,
    frame_w: int,
    frame_h: int,
) -> dict:
    """Map operator corner clicks → ``(frame_key, frame_px, game_xy)`` and persist.

    ``clicks`` maps a corner name (``top``/``right``/``bottom``/``left``) to its
    canvas pixel on the current stitched map. Each is resolved against the run's
    ``map_meta.json`` frame positions. Raises ``ValueError`` if a click lands on
    no frame (off the map) or ``map_meta`` is missing.
    """
    run_dir = Path(run_dir)
    meta_path = run_dir / MAP_META_NAME
    if not meta_path.is_file():
        msg = f"{meta_path} not found — stitch the run before marking corners"
        raise ValueError(msg)
    frames = json.loads(meta_path.read_text(encoding="utf-8")).get("frames") or {}
    if not frames:
        msg = "map_meta.json has no per-frame positions — re-stitch the run"
        raise ValueError(msg)
    game = corner_game_xy(game_size)
    out: list[dict] = []
    for name in CORNER_ORDER:
        if name not in clicks:
            continue
        hit = map_click_to_frame(clicks[name], frames, frame_w, frame_h)
        if hit is None:
            msg = f"the {name} corner click is off the scanned map — click a kingdom vertex"
            raise ValueError(msg)
        frame_key, frame_px = hit
        out.append(
            {
                "corner": name,
                "game_xy": list(game[name]),
                "frame_key": frame_key,
                "frame_px": list(frame_px),
                "canvas_px": [round(float(clicks[name][0]), 1), round(float(clicks[name][1]), 1)],
            }
        )
    if len(out) < 3:
        msg = f"need at least 3 corner clicks to pin the grid, got {len(out)}"
        raise ValueError(msg)
    sidecar = {"game_size": int(game_size), "corners": out}
    (run_dir / CORNERS_SIDECAR_NAME).write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    logger.info("radar corners: saved %d corner constraint(s) → %s", len(out), CORNERS_SIDECAR_NAME)
    return sidecar


def load_corner_constraints(
    run_dir: str | Path, entries: list[dict]
) -> list[tuple[int, tuple[float, float], tuple[float, float]]]:
    """Build solver constraints ``[(frame_index, frame_px, game_xy), ...]``.

    Resolves each sidecar corner's ``frame_key`` to its index in ``entries``
    (the stitch's frame order). Corners whose frame is no longer present are
    dropped. Empty list when there is no sidecar.
    """
    sidecar = read_corners(run_dir)
    if not sidecar:
        return []
    key_to_index = {f"{int(e['ix']):02d}_{int(e['iy']):02d}": i for i, e in enumerate(entries)}
    out: list[tuple[int, tuple[float, float], tuple[float, float]]] = []
    for c in sidecar.get("corners") or []:
        idx = key_to_index.get(str(c.get("frame_key")))
        if idx is None:
            continue
        fpx = c.get("frame_px") or [0, 0]
        gxy = c.get("game_xy") or [0, 0]
        out.append((idx, (float(fpx[0]), float(fpx[1])), (float(gxy[0]), float(gxy[1]))))
    return out
