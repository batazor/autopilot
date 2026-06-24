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

from api.services.click_approval_overlay import load_preview_bytes
from api.services.fish_common import (
    FishDetectionRow,
    decode_bgr,
    detections_to_rows,
    draw_detections,
)
from config.loader import load_settings
from dashboard.reference_preview import load_rolling_instance_preview
from inference.roboflow_client import (
    Detection,
    InferenceUnavailableError,
    RoboflowDetector,
)

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


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


def _run_detection(
    image_bgr: np.ndarray,
    *,
    detector: RoboflowDetector,
    threshold: float | None,
) -> list[Detection]:
    """Run the async detector from sync API context."""
    return asyncio.run(detector.detect(image_bgr, threshold=threshold))


# Substrings that mean "nothing answered at the endpoint" rather than a real
# application error (a down/never-started container, not a bad request or 401).
_CONNECT_FAILURE_MARKERS = (
    "connect",
    "all connection attempts failed",
    "connection refused",
    "timed out",
    "name or service not known",
)


def _explain_inference_error(raw: str) -> str:
    """Turn a raw connect failure into a message that points at the control.

    A bare ``ConnectError: All connection attempts failed`` is confusing when the
    optional inference container simply hasn't been started yet. If the endpoint
    is unreachable and the lifecycle says it isn't running, say so plainly and
    point the operator at the Inference service widget instead of the stack trace.
    HTTP errors (e.g. 401 auth) are meaningful, so they pass through unchanged.
    """
    if not any(marker in raw.lower() for marker in _CONNECT_FAILURE_MARKERS):
        return raw
    try:
        from api.services import inference_lifecycle

        phase = inference_lifecycle.get_status()["phase"]
    except Exception:
        logger.debug("fish-detect: lifecycle status probe failed", exc_info=True)
        phase = ""
    if phase == "ready":
        return raw  # genuinely running yet unreachable — keep the detail
    return (
        "inference container is not running — start it with the Inference "
        f"service control above (status: {phase or 'unknown'})"
    )


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
        image_bgr = decode_bgr(png)
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
        base["error"] = _explain_inference_error(str(exc))
        return base
    except Exception as exc:
        logger.debug("fish-detect: unexpected failure", exc_info=True)
        base["available"] = False
        base["error"] = f"{type(exc).__name__}: {exc}"
        return base

    base["detections"] = detections_to_rows(detections)
    return base


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
    image_bgr = decode_bgr(png)
    if image_bgr is None:
        return None, result
    annotated = draw_detections(image_bgr, result["detections"])
    ok, enc = cv2.imencode(".png", annotated)
    if not ok:
        return None, result
    return enc.tobytes(), result
