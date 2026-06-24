"""Decoupled fishing decision endpoint — what the bot *would* do, no taps.

Reads the instance's latest rolling frame, runs the Roboflow fish detector + the
cyan-hook locator + an OCR of the ``fishing_tournament.level`` altitude counter,
then asks the pure :mod:`api.services.fish_engine` for a phase + swipe. Returns
the decision as JSON for the ``/fish-detect`` page to overlay live — exactly the
dreamscape "live polling, decoupled from the worker" pattern, but for the
dodge→collect minigame logic. It **never controls the device** (executing the
swipe is the separate ``fish_drive`` driver), so it is safe to poll at any time.

A small per-instance ring-buffer of altitude readings is kept here so the phase
follows the counter's *direction* across stateless poll calls — dodge while it is
flat/falling, collect while it climbs ("набор высоты"). The previous poll's
detections + frame time are also cached to measure each fish's velocity and aim
the swipe at its lead position (latency compensation). State resets when a new
round is detected (the counter drops sharply) or on an explicit ``reset``.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import TYPE_CHECKING, Any, TypedDict

from api.services.fish_common import FishDetectionRow, decode_bgr, detections_to_rows
from api.services.fish_detect import _load_frame
from api.services.fish_engine import SwipePlan, parse_level, plan_action
from config.loader import load_settings
from inference.roboflow_client import InferenceUnavailableError, RoboflowDetector

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

_LEVEL_REGION = "fishing_tournament.level"

# Per-instance altitude history so the phase latches across poll calls.
_LEVELS: dict[str, list[int]] = {}
_LEVELS_LOCK = threading.Lock()
_MAX_LEVELS = 40              # keep the last N readings (plenty to latch)
_ROUND_RESET_DROP = 5        # counter dropping by more than this ⇒ new round

# Per-instance previous frame (detections + mtime) → fish velocity / lead.
_LAST_FRAME: dict[str, tuple[list[FishDetectionRow], float]] = {}
_FRAME_LOCK = threading.Lock()
_ACTION_BUDGET_S = 0.15      # aim this far past the frame's age (inference + swipe)


class FishPlanResult(TypedDict):
    """Response payload for ``GET /api/instances/{id}/fish-plan``."""

    instance_id: str
    available: bool          # inference detector configured/reachable
    model_id: str
    confidence: float
    frame_width: int
    frame_height: int
    preview_available: bool
    preview_rel: str
    preview_mtime: float | None
    phase: str               # "dodge" | "collect"
    level: int | None        # current altitude reading
    level_total: int | None  # the "/N" denominator
    level_text: str          # raw OCR text (diagnostics)
    hook_x: int | None
    hook_y: int | None
    protected: bool | None   # blue shield ring present around the hook (None: unknown)
    hook_direction: str | None  # "down"|"up"|None — travel dir from hook y-zone
    target_index: int        # index into detections, or -1
    swipe: SwipePlan | None
    detections: list[FishDetectionRow]
    error: str


def _record_level(instance_id: str, level: int | None, *, reset: bool) -> list[int]:
    """Append a reading and return the round's altitude history (latching)."""
    with _LEVELS_LOCK:
        hist = _LEVELS.setdefault(instance_id, [])
        if reset:
            hist.clear()
        if level is not None:
            if hist and level < hist[-1] - _ROUND_RESET_DROP:
                hist.clear()  # counter fell sharply → a new round started
            hist.append(level)
            if len(hist) > _MAX_LEVELS:
                del hist[: len(hist) - _MAX_LEVELS]
        return list(hist)


def reset_levels(instance_id: str) -> None:
    """Drop the cached altitude history (e.g. when (re)starting a round)."""
    with _LEVELS_LOCK:
        _LEVELS.pop(instance_id, None)
    with _FRAME_LOCK:
        _LAST_FRAME.pop(instance_id, None)


def _take_prev_frame(
    instance_id: str,
    rows: list[FishDetectionRow],
    mtime: float | None,
    *,
    reset: bool,
) -> tuple[list[FishDetectionRow] | None, float | None]:
    """Swap in this frame, returning the previous ``(rows, dt_s)`` for velocity.

    ``dt_s`` is the real gap between frame timestamps (preview mtime); ``None``
    when there is no comparable prior frame (first poll, a stale/identical frame,
    or a reset) so the tracker reports zero velocity instead of a bogus one.
    """
    with _FRAME_LOCK:
        prev = None if reset else _LAST_FRAME.get(instance_id)
        if reset:
            _LAST_FRAME.pop(instance_id, None)
        if mtime is not None:
            _LAST_FRAME[instance_id] = (rows, mtime)
    if prev is None or mtime is None:
        return None, None
    prev_rows, prev_mtime = prev
    return prev_rows, (mtime - prev_mtime if mtime > prev_mtime else None)


def _read_level(client: Any, instance_id: str) -> tuple[int | None, int | None, str]:
    """OCR the altitude counter region → ``(current, total, raw_text)``.

    Off the gameplay screen the region holds something else and won't parse,
    yielding ``(None, None, text)`` — which leaves the latched history untouched.
    """
    try:
        from api.services.overlay_test.ocr import run_region_ocr

        res = run_region_ocr(
            client=client, instance_id=instance_id, regions=[_LEVEL_REGION]
        )
    except Exception:
        logger.debug("fish-plan: level OCR failed", exc_info=True)
        return None, None, ""
    rows = res.get("rows") or []
    text = str(rows[0].get("text", "")).strip() if rows else ""
    parsed = parse_level(text)
    if parsed is None:
        return None, None, text
    return parsed[0], parsed[1], text


def run_fish_plan(
    *,
    client: Any,
    instance_id: str,
    threshold: float | None = None,
    reset: bool = False,
) -> FishPlanResult:
    """Decide phase + swipe for the instance's latest frame (no device taps)."""
    cfg = load_settings().inference
    detector = RoboflowDetector.from_settings(cfg)
    conf = cfg.confidence if threshold is None else threshold

    png, rel, mtime = _load_frame(instance_id)
    width = height = 0
    image_bgr: np.ndarray | None = None
    if png is not None:
        image_bgr = decode_bgr(png)
        if image_bgr is not None:
            height, width = int(image_bgr.shape[0]), int(image_bgr.shape[1])

    base: FishPlanResult = FishPlanResult(
        instance_id=instance_id,
        available=detector.available(),
        model_id=detector.model_id,
        confidence=round(conf, 4),
        frame_width=width,
        frame_height=height,
        preview_available=png is not None,
        preview_rel=rel,
        preview_mtime=mtime,
        phase="dodge",
        level=None,
        level_total=None,
        level_text="",
        hook_x=None,
        hook_y=None,
        protected=None,
        hook_direction=None,
        target_index=-1,
        swipe=None,
        detections=[],
        error="",
    )

    # Fish detections (graceful when inference is off — hook + level still work).
    rows: list[FishDetectionRow] = []
    if image_bgr is not None and detector.available():
        try:
            dets = asyncio.run(detector.detect(image_bgr, threshold=conf))
            rows = detections_to_rows(dets)
        except InferenceUnavailableError as exc:
            base["available"] = False
            base["error"] = str(exc)
        except Exception as exc:
            logger.debug("fish-plan: detection failed", exc_info=True)
            base["available"] = False
            base["error"] = f"{type(exc).__name__}: {exc}"
    elif not detector.available():
        base["error"] = "inference not configured (set WOS_INFERENCE_URL / ROBOFLOW_API_KEY)"
    elif image_bgr is None:
        base["error"] = "no rolling preview frame available yet"

    # Altitude counter → phase-direction history.
    level, level_total, level_text = _read_level(client, instance_id)
    history = _record_level(instance_id, level, reset=reset)

    # Previous frame → fish velocity + a lead aim that covers processing latency.
    prev_rows, dt_s = _take_prev_frame(instance_id, rows, mtime, reset=reset)
    lead_s = _ACTION_BUDGET_S + (max(0.0, time.time() - mtime) if mtime else 0.0)

    plan = plan_action(
        image_bgr, rows, history,
        prev_detections=prev_rows, dt_s=dt_s, lead_s=lead_s,
    )

    base["detections"] = rows
    base["phase"] = plan["phase"]
    base["level"] = level
    base["level_total"] = level_total
    base["level_text"] = level_text
    base["hook_x"] = plan["hook_x"]
    base["hook_y"] = plan["hook_y"]
    base["protected"] = plan["protected"]
    base["hook_direction"] = plan["hook_direction"]
    base["target_index"] = plan["target_index"]
    base["swipe"] = plan["swipe"]
    return base
