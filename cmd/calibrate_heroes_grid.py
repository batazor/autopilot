"""Compute hero-grid layout constants from the reference frame.

Heroes sit on a vivid blue underlay (``H≈105, S≈228, V≈100-125``) that
shows through the inter-card gaps. The script masks those gap pixels,
projects the mask onto each axis, and treats the high-density bands as
column / row separators. The card pitch is derived from the gap
midpoints — no hand-tuned constants.

Run::

    uv run python cmd/calibrate_heroes_grid.py

Output is a ready-to-paste Python block; the same numbers are also
printed in ROI-relative pixels so you can sanity-check against the
defaults baked into :mod:`navigation.hero_grid_search`.
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import cv2
import numpy as np

# Gap-blue HSV band on the heroes screen, derived empirically — wide
# enough to absorb minor compression noise but tight enough to exclude
# card interior (which is a different blue at H≈133 S≈150 V≈220).
_GAP_HSV_LO = (100, 200, 70)
_GAP_HSV_HI = (115, 255, 160)
# Fraction of the strongest axis-projection peak that still counts as
# gap. 0.6 is loose enough to find narrow gaps yet rejects pixel noise
# inside cards.
_PEAK_RATIO = 0.60
# Offsets from the card-frame top-left to the portrait-template top-left.
# The card frame includes a thin border and a rarity-star bar above the
# portrait — they shift the matched template down/right relative to the
# gap edges this script detects. Constant across cells.
_PORTRAIT_OFFSET_Y = 54
_PORTRAIT_OFFSET_X = 9


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _heroes_grid_bbox_percent(area_path: Path) -> dict[str, float]:
    data = json.loads(area_path.read_text(encoding="utf-8"))
    for screen in data.get("screens", []):
        for region in screen.get("regions", []):
            if region.get("name") == "heroes.grid":
                return dict(region["bbox"])
    msg = "heroes.grid region not found in area.json"
    raise SystemExit(msg)


def _group_runs(indices: np.ndarray, max_gap: int = 3) -> list[tuple[int, int]]:
    """Collapse a sorted index array into ``(start, end)`` runs."""
    if indices.size == 0:
        return []
    runs: list[tuple[int, int]] = []
    start = end = int(indices[0])
    for v in indices[1:]:
        v = int(v)
        if v - end <= max_gap:
            end = v
        else:
            runs.append((start, end))
            start = end = v
    runs.append((start, end))
    return runs


def _axis_centers(mask_proj: np.ndarray, peak_ratio: float) -> list[int]:
    if mask_proj.size == 0 or mask_proj.max() == 0:
        return []
    cutoff = mask_proj.max() * peak_ratio
    idx = np.where(mask_proj >= cutoff)[0]
    return [(s + e) // 2 for s, e in _group_runs(idx)]


def main() -> int:
    repo = _repo_root()
    ref_path = repo / "references" / "page.heroes.png"
    area_path = repo / "area.json"

    ref = cv2.imread(str(ref_path))
    if ref is None:
        print(f"cannot read {ref_path}", file=sys.stderr)
        return 2
    fh, fw = ref.shape[:2]
    bbox = _heroes_grid_bbox_percent(area_path)
    rx = int(round(bbox["x"] / 100.0 * fw))
    ry = int(round(bbox["y"] / 100.0 * fh))
    rw = int(round(bbox["width"] / 100.0 * fw))
    rh = int(round(bbox["height"] / 100.0 * fh))
    roi = ref[ry : ry + rh, rx : rx + rw]

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(_GAP_HSV_LO, np.uint8), np.array(_GAP_HSV_HI, np.uint8))
    col_proj = mask.sum(axis=0)
    row_proj = mask.sum(axis=1)

    col_gaps = _axis_centers(col_proj, _PEAK_RATIO)
    row_gaps = _axis_centers(row_proj, _PEAK_RATIO)

    # Strip the outer ROI borders (those are not card gaps).
    inner_col_gaps = [c for c in col_gaps if 10 < c < rw - 10]
    inner_row_gaps = [r for r in row_gaps if 10 < r < rh - 10]

    if len(inner_col_gaps) < 3:
        print(
            f"calibration failed: only {len(inner_col_gaps)} column gaps found "
            f"(need ≥3 for 4 columns).",
            file=sys.stderr,
        )
        return 3
    if len(inner_row_gaps) < 1:
        print("calibration failed: no row gaps found.", file=sys.stderr)
        return 4

    # Derive cell pitches from the column / row gap midpoints.
    col_pitch_samples = [b - a for a, b in itertools.pairwise(inner_col_gaps)]
    row_pitch_samples = [b - a for a, b in itertools.pairwise(inner_row_gaps)]
    col_pitch = int(round(float(np.mean(col_pitch_samples))))
    row_pitch = int(
        round(float(np.mean(row_pitch_samples)))
        if row_pitch_samples
        else _row_pitch_from_card_height(rh, inner_row_gaps)
    )

    # Card-frame top-left = one pitch before the first gap.
    card_first_col_x = inner_col_gaps[0] - col_pitch
    card_first_row_y = inner_row_gaps[0] - row_pitch
    # Template top-left = card-frame top-left + portrait offset.
    first_col_x = card_first_col_x + _PORTRAIT_OFFSET_X
    first_row_y = card_first_row_y + _PORTRAIT_OFFSET_Y

    grid_cols = len(inner_col_gaps) + 1
    grid_rows = len(inner_row_gaps) + 1  # at least one row below the last gap

    print(f"reference:        {ref_path.relative_to(repo)}")
    print(f"frame size:       {fw}×{fh}")
    print(f"heroes.grid ROI:  x={rx} y={ry} w={rw} h={rh}")
    print()
    print(f"column gaps (ROI x): {inner_col_gaps}")
    print(f"row gaps (ROI y):    {inner_row_gaps}")
    print(f"col pitch samples:   {col_pitch_samples}  → {col_pitch}")
    print(f"row pitch samples:   {row_pitch_samples}  → {row_pitch}")
    print(f"card frame top-left: ({card_first_col_x}, {card_first_row_y})")
    print(
        f"portrait offset:     ({_PORTRAIT_OFFSET_X}, {_PORTRAIT_OFFSET_Y}) "
        "(rarity bar + card border — constant across cells)"
    )
    print()
    print("Suggested constants for navigation/hero_grid_search.py:")
    print(f"_GRID_COLS = {grid_cols}")
    print(f"_GRID_FIRST_ROW_Y = {first_row_y}")
    print(f"_GRID_FIRST_COL_X = {first_col_x}")
    print(f"_GRID_ROW_PITCH = {row_pitch}")
    print(f"_GRID_COL_PITCH = {col_pitch}")
    print(f"# Observed rows on this frame: {grid_rows} (last row may be clipped)")

    _render_overlay(
        ref=ref,
        roi_xywh=(rx, ry, rw, rh),
        mask=mask,
        col_gaps=inner_col_gaps,
        row_gaps=inner_row_gaps,
        card_first=(card_first_col_x, card_first_row_y),
        col_pitch=col_pitch,
        row_pitch=row_pitch,
        grid_cols=grid_cols,
        grid_rows=grid_rows,
        out_path=Path("/tmp/heroes_grid_calibration.png"),
    )
    return 0


def _render_overlay(
    *,
    ref: np.ndarray,
    roi_xywh: tuple[int, int, int, int],
    mask: np.ndarray,
    col_gaps: list[int],
    row_gaps: list[int],
    card_first: tuple[int, int],
    col_pitch: int,
    row_pitch: int,
    grid_cols: int,
    grid_rows: int,
    out_path: Path,
) -> None:
    """Save a 3-up overlay: gap mask, detected gap lines, derived cells."""
    rx, ry, rw, rh = roi_xywh
    out = ref.copy()

    # 1) Tint the gap-blue mask so you can see which pixels drove the calibration.
    mask_color = np.zeros_like(ref)
    mask_color[ry : ry + rh, rx : rx + rw][mask > 0] = (255, 200, 0)
    cv2.addWeighted(mask_color, 0.45, out, 1.0, 0.0, out)

    # 2) Detected gap lines (cyan).
    for gx in col_gaps:
        cv2.line(out, (rx + gx, ry), (rx + gx, ry + rh), (255, 220, 0), 1)
    for gy in row_gaps:
        cv2.line(out, (rx, ry + gy), (rx + rw, ry + gy), (255, 220, 0), 1)

    # 3) Derived cell rectangles (green).
    cfx, cfy = card_first
    card_w = col_pitch - 1
    card_h = row_pitch - 1
    for ri in range(grid_rows):
        for ci in range(grid_cols):
            x0 = rx + cfx + ci * col_pitch
            y0 = ry + cfy + ri * row_pitch
            x1 = x0 + card_w
            y1 = y0 + card_h
            # Stay inside the global frame so partial rows still render.
            if x0 >= ref.shape[1] or y0 >= ref.shape[0]:
                continue
            x1 = min(x1, ref.shape[1] - 1)
            y1 = min(y1, ref.shape[0] - 1)
            cv2.rectangle(out, (x0, y0), (x1, y1), (0, 220, 0), 2)
            cv2.putText(
                out,
                f"r{ri}c{ci}",
                (x0 + 4, y0 + 16),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.40,
                (0, 0, 0),
                3,
                cv2.LINE_AA,
            )
            cv2.putText(
                out,
                f"r{ri}c{ci}",
                (x0 + 4, y0 + 16),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.40,
                (0, 220, 0),
                1,
                cv2.LINE_AA,
            )

    cv2.imwrite(str(out_path), out)
    print(f"overlay saved to: {out_path}")


def _row_pitch_from_card_height(rh: int, gaps: list[int]) -> float:
    # Fallback when there's only one inter-row gap (3 rows but only 1 internal gap).
    # The first gap is roughly one card-height in from the top.
    return float(gaps[0]) if gaps else 0.0


if __name__ == "__main__":
    sys.exit(main())
