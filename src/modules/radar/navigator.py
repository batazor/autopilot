"""Building-to-building navigation over a scanned city.

Given a finished scan — the stitched canvas, the building registry
(``buildings.json``: name → canvas px) and the swipe scale (manifest
``swipe_calibration``) — the navigator locates the live camera on the canvas by
ORB and swipes toward a target building until it is centred. Distances come from
the metric canvas; the scale converts canvas px to finger travel.

This is the consumer of everything the radar built: it needs only positions and
scale, not a pretty map.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import cv2

from modules.radar.labels import BUILDINGS_NAME, _canvas_offset, _norm
from modules.radar.scanner import MANIFEST_NAME
from modules.radar.stitch import MAP_FULL_NAME

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np

logger = logging.getLogger(__name__)

# Screen anchor a swipe is centred on (720x1280 portrait), and limits so one
# step stays on-screen and never degrades into a tap (which would open a building).
_SCREEN_CX, _SCREEN_CY = 360.0, 640.0
_MAX_FINGER = 300.0
_MIN_FINGER = 90.0
# A step must close the gap by at least this much to count as progress (else the
# route is judged stalled — overshoot oscillation or a wall of identical sprites).
_PROGRESS_EPS = 8.0
# A centred building's name plate floats ABOVE its footprint, so the tap that
# opens it lands this far below the screen centre.
_OPEN_TAP_OFFSET_Y = 70.0


def open_tap_point() -> tuple[float, float]:
    """Screen point to tap a centred building open (below its floating label)."""
    return (_SCREEN_CX, _SCREEN_CY + _OPEN_TAP_OFFSET_Y)


def route_decision(dist: float, stalls: int, *, tol: float, patience: int) -> str:
    """``done`` once within ``tol`` px, ``stalled`` after ``patience`` steps with
    no progress, else ``go``. Pure so the control loop is unit-testable."""
    if dist <= tol:
        return "done"
    if stalls >= patience:
        return "stalled"
    return "go"


def plan_step(
    current: tuple[float, float],
    target: tuple[float, float],
    scale: tuple[float, float],
    max_finger: float = _MAX_FINGER,
) -> tuple[float, float]:
    """Finger drag (dx, dy) that moves the camera from ``current`` toward
    ``target`` (both canvas px). The map is dragged OPPOSITE the desired camera
    move; ``scale`` is camera px per finger px; the step is clamped to
    ``max_finger`` so it stays on-screen."""
    dcx, dcy = target[0] - current[0], target[1] - current[1]
    sx = scale[0] or 1.0
    sy = scale[1] or 1.0
    fx, fy = -dcx / sx, -dcy / sy
    norm = math.hypot(fx, fy)
    if norm > max_finger:
        fx, fy = fx * max_finger / norm, fy * max_finger / norm
    return fx, fy


# The assembled whole-base map lives at a stable path so navigation always
# loads the fused map (not whichever chunk was scanned last).
CITYMAP_DIRNAME = "citymap"


def _is_city_run(d: Path) -> bool:
    if not d.is_dir() or not (d / MAP_FULL_NAME).is_file() or not (d / BUILDINGS_NAME).is_file():
        return False
    try:
        man = json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (man.get("config") or {}).get("target") == "main_city"


def latest_city_run(runs_root: str | Path) -> Path | None:
    """The map navigation should use: the assembled ``citymap`` if present, else
    the newest single ``main_city`` scan. ``None`` when none has been scanned."""
    root = Path(runs_root)
    if not root.is_dir():
        return None
    assembled = root / CITYMAP_DIRNAME
    if _is_city_run(assembled):
        return assembled
    cands = [d for d in root.iterdir() if d.name != CITYMAP_DIRNAME and _is_city_run(d)]
    return max(cands, key=lambda d: d.stat().st_mtime) if cands else None


@dataclass
class Navigator:
    canvas: np.ndarray
    buildings: dict[str, tuple[tuple[float, float], str]]  # norm name → (canvas_px, display)
    scale: tuple[float, float]
    crop: dict

    @classmethod
    def from_run(cls, run_dir: str | Path) -> Navigator:
        run_dir = Path(run_dir)
        canvas = cv2.imread(str(run_dir / MAP_FULL_NAME))
        if canvas is None:
            msg = f"no stitched canvas at {run_dir / MAP_FULL_NAME}"
            raise FileNotFoundError(msg)
        reg = json.loads((run_dir / BUILDINGS_NAME).read_text(encoding="utf-8"))
        man = json.loads((run_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
        crop = (man.get("config") or {}).get("crop")
        if not crop:
            msg = f"manifest at {run_dir} carries no config.crop"
            raise ValueError(msg)
        sc = man.get("swipe_calibration") or {}
        scale = (float(sc.get("scale_x") or 1.0), float(sc.get("scale_y") or 1.0))
        buildings = {
            _norm(b["name"]): ((float(b["canvas_px"][0]), float(b["canvas_px"][1])), b["name"])
            for b in reg.get("buildings", [])
        }
        return cls(canvas=canvas, buildings=buildings, scale=scale, crop=crop)

    def names(self) -> list[str]:
        return sorted(v[1] for v in self.buildings.values())

    def find(self, name: str) -> tuple[float, float] | None:
        """Resolve a (fuzzy) building name to its canvas position."""
        n = _norm(name)
        if n in self.buildings:
            return self.buildings[n][0]
        hits = [v[0] for k, v in self.buildings.items() if n and (n in k or k in n)]
        return hits[0] if hits else None

    def locate(self, frame: np.ndarray) -> tuple[float, float] | None:
        """Where the screen centre currently sits on the canvas (px), by ORB —
        or None when the live view does not overlap the scanned map."""
        c = self.crop
        sub = frame[c["y"] : c["y"] + c["h"], c["x"] : c["x"] + c["w"]]
        off = _canvas_offset(self.canvas, sub)  # canvas px = sub px + off
        if off is None:
            return None
        return (c["w"] / 2.0 + off[0], c["h"] / 2.0 + off[1])

    def _locate_retry(
        self, capture: Callable[[], np.ndarray], retries: int = 4
    ) -> tuple[float, float] | None:
        """Localize, re-capturing a few times — a transient popup, a mid-pan
        blur, or a low-texture frame (snow/water near the city edge) fails one
        capture but not the next. On a large stitched canvas with big snow/water
        regions ORB localization is probabilistic per frame, so a handful of
        retries lifts the per-step lock rate from ~60% to ~99%."""
        for _ in range(retries + 1):
            cur = self.locate(capture())
            if cur is not None:
                return cur
        return None

    @staticmethod
    def _pan(swipe: Callable[[int, int, int, int], None], fx: float, fy: float) -> None:
        """Issue one drag of finger travel (fx, fy) centred on the screen, never
        shorter than a pan (a short drag reads as a tap → opens a building)."""
        norm = math.hypot(fx, fy)
        if 0 < norm < _MIN_FINGER:
            fx, fy = fx * _MIN_FINGER / norm, fy * _MIN_FINGER / norm
        swipe(
            int(_SCREEN_CX - fx / 2), int(_SCREEN_CY - fy / 2),
            int(_SCREEN_CX + fx / 2), int(_SCREEN_CY + fy / 2),
        )

    def route_to(
        self,
        name: str,
        capture: Callable[[], np.ndarray],
        swipe: Callable[[int, int, int, int], None],
        *,
        max_steps: int = 14,
        tol_px: float = 90.0,
        patience: int = 3,
        settle_s: float = 0.7,
        on_lost: Callable[[], None] | None = None,
    ) -> bool:
        """Swipe until ``name`` is centred. ``capture`` returns a screenshot;
        ``swipe(x1,y1,x2,y2)`` drags. True once within ``tol_px``.

        Robustness: a lost fix is retried and, failing that, the last move is
        undone once (an overshoot off the scanned canvas); ``on_lost`` (e.g. a
        popup dismisser) is then invoked and localization tried a last time
        before giving up; a run that stops closing the gap for ``patience`` steps
        is abandoned instead of oscillating forever.
        """
        target = self.find(name)
        if target is None:
            msg = f"unknown building {name!r}; have {self.names()}"
            raise ValueError(msg)
        best = math.inf
        stalls = 0
        last: tuple[float, float] | None = None
        for step in range(max_steps):
            cur = self._locate_retry(capture)
            if cur is None and last is not None:
                # Off-canvas overshoot: undo the last move and try to re-acquire.
                self._pan(swipe, -last[0], -last[1])
                time.sleep(settle_s)
                cur = self._locate_retry(capture)
                last = None
            if cur is None and on_lost is not None:
                # A modal may be covering the map — let the caller clear it.
                on_lost()
                time.sleep(settle_s)
                cur = self._locate_retry(capture)
            if cur is None:
                logger.warning("navigator: lost localization at step %d", step)
                return False
            dist = math.hypot(target[0] - cur[0], target[1] - cur[1])
            stalls = 0 if dist < best - _PROGRESS_EPS else stalls + 1
            best = min(best, dist)
            logger.info(
                "nav %s step %d: at (%.0f,%.0f) → (%.0f,%.0f) dist %.0f stalls %d",
                name, step, cur[0], cur[1], target[0], target[1], dist, stalls,
            )
            decision = route_decision(dist, stalls, tol=tol_px, patience=patience)
            if decision == "done":
                return True
            if decision == "stalled":
                logger.info("navigator: no progress at %.0f px — stopping", dist)
                return dist <= tol_px * 1.4
            fx, fy = plan_step(cur, target, self.scale)
            last = (fx, fy)
            self._pan(swipe, fx, fy)
            time.sleep(settle_s)
        return False
