#!/usr/bin/env python3
"""Grid-swipe capture of the Whiteout Survival world map via scrcpy.

Reuses the project's battle-tested scrcpy client (``adb.scrcpy``) for both the
H.264 frame grab and the touch/swipe injection, so a single device-side
``scrcpy-server`` process powers capture and input — no separate bindings.

Output: ``./frames/frame_<row>_<col>.png`` (logical grid coords). The camera is
walked in a serpentine raster so consecutive captures always overlap, but the
filename always uses left-to-right column numbering regardless of travel
direction, which lets ``stitch.py`` reason about grid neighbours simply.

Run directly (uses the constants below) or override from the CLI / Streamlit UI:

    uv run python tools/map_stitch/capture.py --rows 3 --cols 5 --overlap 0.3
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import cv2

# --- make the in-repo `src/` packages importable (adb.scrcpy, adb.screencap) --
_BASE = Path(__file__).resolve().parent
_REPO_ROOT = _BASE.parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from adb.scrcpy import ScrcpyClient, get_or_create_scrcpy_client  # noqa: E402
from adb.screencap import resolve_adb_executable  # noqa: E402

if TYPE_CHECKING:
    import numpy as np

# ======================= CONFIGURABLE PARAMETERS =============================
DEVICE_SERIAL = "localhost:5555"  # BlueStacks default ADB endpoint
SWIPE_DURATION_MS = 300           # how long each grid swipe takes
SETTLE_DELAY_S = 1.0              # wait after each swipe for the map to stop moving
OVERLAP_RATIO = 0.30             # fraction of frame shared between neighbours
GRID_ROWS = 3                     # map height in screens (spec: ~5)
GRID_COLS = 5                     # map width in screens (spec: ~3)
HOME_FIRST = True                 # push the camera to the top-left corner first
FRAMES_DIR = _BASE / "frames"
# ============================================================================

# Keep swipe endpoints inside a central band so we never trigger system edge
# gestures (status-bar pull-down, recents, back) while scrolling the map.
_EDGE_MARGIN = 0.10  # fraction of width/height kept clear on every side


def _client(serial: str) -> ScrcpyClient:
    """Resolve adb, build the shared scrcpy client and wait for first frame."""
    adb_bin = resolve_adb_executable("adb")
    if not adb_bin:
        msg = "adb executable not found (set ANDROID_HOME or install platform-tools)"
        raise RuntimeError(msg)
    client = get_or_create_scrcpy_client(serial, adb_bin)
    client.start()
    # Block until the decoder has produced at least one frame, else codec_size
    # (device resolution) is unknown and we can't size swipes.
    deadline = time.monotonic() + 10.0
    while client.codec_size is None and time.monotonic() < deadline:
        client.read_latest_frame_bgr(timeout_s=0.5)
    if client.codec_size is None:
        msg = f"scrcpy produced no frames for {serial}: {client.last_error}"
        raise RuntimeError(msg)
    return client


def _grab(client: ScrcpyClient, boundary_s: float) -> np.ndarray:
    """Return a BGR frame decoded *after* boundary_s (rejects stale cache)."""
    img, err = client.read_latest_frame_bgr(timeout_s=3.0, not_before_s=boundary_s)
    if img is None:
        msg = f"frame grab failed: {err}"
        raise RuntimeError(msg)
    return img


def _do_swipe(
    client: ScrcpyClient, dx_frac: float, dy_frac: float, duration_ms: int,
) -> None:
    """Drag the camera by (dx_frac, dy_frac) of a screen.

    Positive dx_frac moves the *camera* right (reveals content to the right),
    which means the finger travels left — so the swipe vector is negated.
    """
    w, h = client.codec_size
    cx, cy = w * 0.5, h * 0.5
    # Finger displacement is opposite to camera displacement.
    fx = -dx_frac * w
    fy = -dy_frac * h
    x1, y1 = cx - fx / 2, cy - fy / 2
    x2, y2 = cx + fx / 2, cy + fy / 2
    # Clamp into the safe central band.
    lo_x, hi_x = _EDGE_MARGIN * w, (1 - _EDGE_MARGIN) * w
    lo_y, hi_y = _EDGE_MARGIN * h, (1 - _EDGE_MARGIN) * h
    x1, x2 = max(lo_x, min(hi_x, x1)), max(lo_x, min(hi_x, x2))
    y1, y2 = max(lo_y, min(hi_y, y1)), max(lo_y, min(hi_y, y2))
    client.swipe(int(x1), int(y1), int(x2), int(y2), duration_ms=duration_ms)


def _home_top_left(
    client: ScrcpyClient, rows: int, cols: int, duration_ms: int,
) -> None:
    """Best-effort: shove the camera to the top-left corner of the map.

    Repeated full-screen down-right drags until the map stops moving (edge).
    Map edges bounce, so this is heuristic — we just need a consistent origin.
    """
    print("Homing to top-left corner...", flush=True)
    for _ in range(max(rows, cols) + 2):
        _do_swipe(client, dx_frac=-0.9, dy_frac=-0.9, duration_ms=duration_ms)
        time.sleep(0.2)
    time.sleep(SETTLE_DELAY_S)


def capture(
    *,
    serial: str = DEVICE_SERIAL,
    rows: int = GRID_ROWS,
    cols: int = GRID_COLS,
    overlap: float = OVERLAP_RATIO,
    swipe_ms: int = SWIPE_DURATION_MS,
    settle_s: float = SETTLE_DELAY_S,
    home: bool = HOME_FIRST,
    frames_dir: Path = FRAMES_DIR,
) -> int:
    """Walk a serpentine raster over the map, saving one PNG per grid cell."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    for stale in frames_dir.glob("frame_*.png"):
        stale.unlink()  # fresh run — don't stitch yesterday's leftovers

    step = 1.0 - overlap  # camera advance per swipe, in screen fractions
    total = rows * cols
    client = _client(serial)
    print(f"scrcpy ready: {client.device_name} {client.codec_size}", flush=True)

    if home:
        _home_top_left(client, rows, cols, swipe_ms)

    done = 0
    for r in range(rows):
        # Serpentine: even rows left→right, odd rows right→left, to avoid a
        # long blind return-swipe at every row boundary.
        col_order = range(cols) if r % 2 == 0 else range(cols - 1, -1, -1)
        col_order = list(col_order)
        for idx, c in enumerate(col_order):
            time.sleep(settle_s)
            boundary = time.monotonic()
            time.sleep(0.05)  # ensure a strictly-newer frame than `boundary`
            frame = _grab(client, boundary)
            out = frames_dir / f"frame_{r}_{c}.png"
            cv2.imwrite(str(out), frame)
            done += 1
            print(f"Capturing {done}/{total}... saved {out.name}", flush=True)

            # Move to the next column in travel order (skip after row's last).
            if idx < len(col_order) - 1:
                direction = 1 if r % 2 == 0 else -1
                _do_swipe(client, dx_frac=direction * step, dy_frac=0.0,
                          duration_ms=swipe_ms)

        # Drop down to the next row (skip after the final row).
        if r < rows - 1:
            time.sleep(settle_s)
            _do_swipe(client, dx_frac=0.0, dy_frac=step, duration_ms=swipe_ms)

    print(f"Capture complete: {done} frames in {frames_dir}", flush=True)
    return done


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="scrcpy grid-swipe map capture")
    p.add_argument("--serial", default=DEVICE_SERIAL)
    p.add_argument("--rows", type=int, default=GRID_ROWS)
    p.add_argument("--cols", type=int, default=GRID_COLS)
    p.add_argument("--overlap", type=float, default=OVERLAP_RATIO)
    p.add_argument("--swipe-ms", type=int, default=SWIPE_DURATION_MS)
    p.add_argument("--settle-s", type=float, default=SETTLE_DELAY_S)
    p.add_argument("--frames-dir", type=Path, default=FRAMES_DIR)
    p.add_argument("--no-home", dest="home", action="store_false", default=HOME_FIRST)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        capture(
            serial=a.serial, rows=a.rows, cols=a.cols, overlap=a.overlap,
            swipe_ms=a.swipe_ms, settle_s=a.settle_s, home=a.home,
            frames_dir=a.frames_dir,
        )
    except Exception as exc:  # surface a clean one-liner to the UI log
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
