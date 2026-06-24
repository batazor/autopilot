"""Shared helpers for the fish detector debug tools.

Decoding, the JSON detection row shape, and box drawing live here so the live
``fish_detect`` page and the ``fish_plan`` panel render detections identically.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

import cv2
import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence

    from inference.roboflow_client import Detection

# BGR colors — readable on the icy game palette.
BOX_COLOR = (0, 200, 255)  # amber — detection boxes
ESCAPE_COLOR = (80, 80, 255)  # red-ish — where the fish is heading
CATCH_COLOR = (0, 230, 120)  # green — recommended catch swipe


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


def decode_bgr(png: bytes) -> np.ndarray | None:
    arr = np.frombuffer(png, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img if img is not None else None


def detections_to_rows(detections: Sequence[Detection]) -> list[FishDetectionRow]:
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


def draw_detections(image_bgr: np.ndarray, rows: list[FishDetectionRow]) -> np.ndarray:
    """Return a copy of ``image_bgr`` with detection boxes + labels drawn."""
    out = image_bgr.copy()
    for r in rows:
        x, y, w, h = r["x"], r["y"], r["width"], r["height"]
        cv2.rectangle(out, (x, y), (x + w, y + h), BOX_COLOR, 2)
        label = f"{r['class_name']} {r['confidence']:.2f}".strip()
        ty = max(0, y - 6)
        cv2.putText(
            out, label, (x, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.45, BOX_COLOR, 1, cv2.LINE_AA
        )
    return out
