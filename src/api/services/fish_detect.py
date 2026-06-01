"""Run the Roboflow fish detector on an instance's current frame (operator tool).

Backs the Fishing Tournament debug page: load the rolling preview PNG, send it
to the inference sidecar, and return detections both as JSON (for client-side
overlay boxes) and as a pre-annotated PNG.

Degrades gracefully: when the sidecar is unconfigured or unreachable the result
carries ``available=False`` plus an ``error`` string instead of raising, so the
UI can show a banner rather than a 500.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, TypedDict

import cv2
import numpy as np

from api.services.click_approval_overlay import load_preview_bytes
from config.loader import load_settings
from dashboard.reference_preview import load_rolling_instance_preview
from inference.roboflow_client import (
    Detection,
    InferenceUnavailableError,
    RoboflowDetector,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

_BOX_COLOR = (0, 200, 255)  # BGR — amber, readable on the icy game palette


class FishDetectionRow(TypedDict):
    """One detected fish in pixel coords of the source frame (top-left origin)."""

    x: int
    y: int
    width: int
    height: int
    center_x: int
    center_y: int
    confidence: float
    class_name: str


class FishDetectResult(TypedDict):
    """Response payload for ``GET /api/instances/{id}/fish-detect``."""

    instance_id: str
    available: bool
    model_id: str
    confidence: float
    frame_width: int
    frame_height: int
    preview_available: bool
    preview_rel: str
    preview_mtime: float | None
    detections: list[FishDetectionRow]
    error: str


def _load_frame(instance_id: str) -> tuple[bytes | None, str, float | None]:
    png, rel, mtime = load_preview_bytes(
        instance_id=instance_id, payload=None, source="live"
    )
    if png is None:
        png, rel, mtime = load_rolling_instance_preview(instance_id)
    return png, rel or "", mtime


def _decode_bgr(png: bytes) -> np.ndarray | None:
    arr = np.frombuffer(png, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img if img is not None else None


def _detections_to_rows(detections: Sequence[Detection]) -> list[FishDetectionRow]:
    rows: list[FishDetectionRow] = []
    for d in detections:
        cx, cy = d.center
        rows.append(
            FishDetectionRow(
                x=d.left,
                y=d.top,
                width=int(round(d.width)),
                height=int(round(d.height)),
                center_x=cx,
                center_y=cy,
                confidence=round(d.confidence, 4),
                class_name=d.class_name,
            )
        )
    return rows


def _run_detection(
    image_bgr: np.ndarray,
    *,
    detector: RoboflowDetector,
    threshold: float | None,
) -> list[Detection]:
    """Run the async detector from sync API context."""
    return asyncio.run(detector.detect(image_bgr, threshold=threshold))


def run_fish_detect(
    *,
    instance_id: str,
    threshold: float | None = None,
) -> FishDetectResult:
    """Detect fish on the instance's latest rolling frame."""
    cfg = load_settings().inference
    detector = RoboflowDetector.from_settings(cfg)
    conf = cfg.confidence if threshold is None else threshold

    png, rel, mtime = _load_frame(instance_id)
    width = height = 0
    image_bgr: np.ndarray | None = None
    if png is not None:
        image_bgr = _decode_bgr(png)
        if image_bgr is not None:
            height, width = int(image_bgr.shape[0]), int(image_bgr.shape[1])

    base: FishDetectResult = FishDetectResult(
        instance_id=instance_id,
        available=detector.available(),
        model_id=detector.model_id,
        confidence=round(conf, 4),
        frame_width=width,
        frame_height=height,
        preview_available=png is not None,
        preview_rel=rel,
        preview_mtime=mtime,
        detections=[],
        error="",
    )

    if not detector.available():
        base["error"] = "inference service not configured (set WOS_INFERENCE_URL / ROBOFLOW_API_KEY)"
        return base
    if image_bgr is None:
        base["error"] = "no rolling preview frame available yet"
        return base

    try:
        detections = _run_detection(image_bgr, detector=detector, threshold=conf)
    except InferenceUnavailableError as exc:
        base["available"] = False
        base["error"] = str(exc)
        return base
    except Exception as exc:
        logger.debug("fish-detect: unexpected failure", exc_info=True)
        base["available"] = False
        base["error"] = f"{type(exc).__name__}: {exc}"
        return base

    base["detections"] = _detections_to_rows(detections)
    return base


def _draw_detections(image_bgr: np.ndarray, rows: list[FishDetectionRow]) -> np.ndarray:
    out = image_bgr.copy()
    for r in rows:
        x, y, w, h = r["x"], r["y"], r["width"], r["height"]
        cv2.rectangle(out, (x, y), (x + w, y + h), _BOX_COLOR, 2)
        label = f"{r['class_name']} {r['confidence']:.2f}".strip()
        ty = max(0, y - 6)
        cv2.putText(
            out, label, (x, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.45, _BOX_COLOR, 1, cv2.LINE_AA
        )
    return out


def load_fish_detect_image(
    instance_id: str,
    *,
    threshold: float | None = None,
) -> tuple[bytes | None, FishDetectResult]:
    """Return the source frame annotated with detection boxes (PNG bytes).

    Re-runs detection so the drawn boxes always match the returned frame. Returns
    ``(None, result)`` when no frame is available; ``result.error`` explains why.
    """
    result = run_fish_detect(instance_id=instance_id, threshold=threshold)
    png, _, _ = _load_frame(instance_id)
    if png is None:
        return None, result
    image_bgr = _decode_bgr(png)
    if image_bgr is None:
        return None, result
    annotated = _draw_detections(image_bgr, result["detections"])
    ok, enc = cv2.imencode(".png", annotated)
    if not ok:
        return None, result
    return enc.tobytes(), result
