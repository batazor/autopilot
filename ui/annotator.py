"""Draw OCR overlays on screenshots (OpenCV only + layout types — no worker imports)."""

from __future__ import annotations

import base64
from dataclasses import dataclass

import cv2  # type: ignore[import-untyped]
import numpy as np

from layout.types import Region


@dataclass(frozen=True)
class OverlayItem:
    """Single region to highlight with OCR text."""

    region: Region
    text: str
    confidence: float


def annotate_screenshot(image_b64: str, items: list[OverlayItem]) -> str:
    """Decode PNG base64, draw green rectangles and labels, return base64 PNG string."""
    raw = base64.b64decode(image_b64)
    buf = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Failed to decode image")

    for item in items:
        r = item.region
        cv2.rectangle(img, (r.x, r.y), (r.x + r.w, r.y + r.h), (0, 200, 0), 2)
        label = f"{item.text[:40]} ({item.confidence:.2f})"
        cv2.putText(
            img,
            label,
            (r.x, max(0, r.y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 0, 255),
            1,
            cv2.LINE_AA,
        )

    ok, encoded = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("PNG encode failed")
    return base64.b64encode(encoded.tobytes()).decode("ascii")
