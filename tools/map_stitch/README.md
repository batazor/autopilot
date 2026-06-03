# Map Stitcher (`tools/map_stitch/`)

Capture and stitch a full Whiteout Survival world-map screenshot from BlueStacks
over scrcpy. Two files:

| File         | Role                                                                 |
| ------------ | -------------------------------------------------------------------- |
| `capture.py` | Grid-swipe the camera over the map, save `frames/frame_<r>_<c>.png`. |
| `stitch.py`  | ORB + RANSAC stitch of `frames/` → `map_full.png`.                   |

## Run

```sh
# 1. Capture (reuses the project's scrcpy client for swipe + frame grab)
uv run python tools/map_stitch/capture.py --serial localhost:5555 --rows 3 --cols 5

# 2. Stitch
uv run python tools/map_stitch/stitch.py
```

## Web UI

The dashboard exposes these scripts at **Debug → Map stitch** (`/map-stitch`).
The page (`web/app/(debug)/map-stitch/page.tsx`) drives the FastAPI router
`src/api/routers/map_stitch.py`, which runs `capture.py` / `stitch.py` as
subprocesses from a background thread (`src/api/services/map_stitch.py`), polls
progress, previews frames + the stitched map, and saves into `maps/` — the same
gallery the CLI writes to. Per-run artifacts live under `temporal/mapstitch/`.

Capture grabs the device exclusively over scrcpy (it reaps stale scrcpy
servers on start), so stop the bot / leave the device idle on the map first.

Tunable constants live at the top of `capture.py` and `stitch.py`
(`DEVICE_SERIAL`, `SWIPE_DURATION_MS`, `SETTLE_DELAY_S`, `OVERLAP_RATIO`,
`GRID_ROWS`, `GRID_COLS`); the CLI flags and UI sliders override them.

## How it works / design notes

- **Capture** reuses `adb.scrcpy.ScrcpyClient` (the same server process the
  worker uses) for both H.264 frame grab and human-shaped touch swipes — no
  separate scrcpy bindings. The camera walks a **serpentine raster** (even rows
  L→R, odd rows R→L) to avoid a blind return-swipe each row; filenames always
  use logical left-to-right column numbers so the stitcher sees a clean grid.
  Each frame is grabbed only *after* the post-swipe settle boundary so a stale
  pre-swipe frame is never saved.

- **Stitch** estimates alignment from the low-parallax **ground** of each frame,
  then warps the full frame (rooftops may ghost slightly in overlaps — accepted,
  per the isometric-parallax tradeoff). Two deliberate deviations from a naïve
  "bottom-33% homography" recipe, both needed to make it actually converge:

  1. **Similarity, not full homography.** The camera only pans, and the overlap
     is a thin strip; a full 8-DOF `findHomography` fits the strip but its
     perspective terms explode when the whole frame is warped (canvas blew up
     ~10×). We fit a 4-DOF similarity via `estimateAffinePartial2D` with RANSAC
     and carry it as a 3×3 — still RANSAC outlier rejection, still
     `warpPerspective`, but stable.
  2. **Direction-aware overlap bands.** Bottom-33%-only matching can't stitch
     *vertical* seams: the lower frame's ground band sits below the upper
     frame's, so they share nothing. Features are detected on the full frame and
     matched per-pair in the band where the two frames overlap — bottom↔bottom
     horizontally, bottom↔top vertically.

  Homographies are chained from the centre frame (anchor) via BFS over grid
  neighbours, frames are alpha-feathered (distance-transform weights) onto one
  canvas, and the result is cropped to the bounding box of valid pixels.

- OpenCV only — no `cv2.Stitcher`.
```
