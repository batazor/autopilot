"""Build overlay descriptors for click-approval preview (browser canvas)."""
from __future__ import annotations

from pathlib import Path  # noqa: TC003 — used at runtime for area.json
from typing import Any, Literal, TypedDict

from dashboard.click_approvals import (
    _approval_region_name,
    active_player_state_flat,
    load_area_doc,
    pct_bbox_to_px_rect,
)
from dashboard.reference_preview import load_rolling_instance_preview, references_root
from layout.area_lookup import screen_region_by_name


class OverlayRect(TypedDict, total=False):
    type: Literal["rect"]
    x: int
    y: int
    w: int
    h: int
    label: str
    stroke: str


class OverlayCrosshair(TypedDict):
    type: Literal["crosshair"]
    x: int
    y: int


class OverlayArrow(TypedDict):
    type: Literal["arrow"]
    x1: int
    y1: int
    x2: int
    y2: int
    label: str


OverlayShape = OverlayRect | OverlayCrosshair | OverlayArrow


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _tap_coords(payload: dict[str, Any]) -> tuple[int | None, int | None]:
    x = payload.get("x")
    y = payload.get("y")
    x_i = int(x) if isinstance(x, (int, float)) else None
    y_i = int(y) if isinstance(y, (int, float)) else None
    if x_i is not None and y_i is not None:
        return x_i, y_i
    if _as_text(payload.get("type")).lower() != "swipe":
        return None, None
    try:
        sx1 = int(payload.get("x1") or 0)
        sy1 = int(payload.get("y1") or 0)
        sx2 = int(payload.get("x2") or 0)
        sy2 = int(payload.get("y2") or 0)
        if sx1 == sx2 and sy1 == sy2:
            return sx1, sy1
    except (TypeError, ValueError):
        pass
    return None, None


def build_overlays(
    *,
    payload: dict[str, Any],
    image_width: int,
    image_height: int,
    area_path: Path,
    client: Any,
    instance_id: str,
) -> list[OverlayShape]:
    w, h = image_width, image_height
    overlays: list[OverlayShape] = []
    state_flat = active_player_state_flat(client=client, instance_id=instance_id)
    area_doc = load_area_doc(area_path)
    stroke_region = "#00dcff"

    ptype = _as_text(payload.get("type")).lower()
    if ptype == "set_node":
        return overlays

    if ptype == "swipe":
        try:
            x1 = int(max(0, min(w - 1, int(payload.get("x1") or 0))))
            y1 = int(max(0, min(h - 1, int(payload.get("y1") or 0))))
            x2 = int(max(0, min(w - 1, int(payload.get("x2") or 0))))
            y2 = int(max(0, min(h - 1, int(payload.get("y2") or 0))))
            dist = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            is_long_press = _as_text(payload.get("gesture")).lower() == "long_press" or dist < 1.0
            if is_long_press:
                overlays.append(
                    OverlayRect(type="rect", x=x1, y=y1, w=2, h=2, label="long press", stroke=stroke_region)
                )
                overlays.append(OverlayCrosshair(type="crosshair", x=x1, y=y1))
            else:
                overlays.append(
                    OverlayArrow(
                        type="arrow",
                        x1=x1,
                        y1=y1,
                        x2=x2,
                        y2=y2,
                        label=f"swipe {dist:.0f}px",
                    )
                )
        except (TypeError, ValueError):
            pass

    reg = payload.get("region")
    if isinstance(reg, dict):
        try:
            rx = int(float(reg.get("x") or 0))
            ry = int(float(reg.get("y") or 0))
            rw = int(float(reg.get("w") or 0))
            rh = int(float(reg.get("h") or 0))
            if rw > 0 and rh > 0:
                overlays.append(
                    OverlayRect(
                        type="rect",
                        x=max(0, min(w - 1, rx)),
                        y=max(0, min(h - 1, ry)),
                        w=max(1, min(w, rw)),
                        h=max(1, min(h, rh)),
                        stroke=stroke_region,
                    )
                )
        except (TypeError, ValueError):
            pass

    ctx0 = payload.get("context")
    if isinstance(ctx0, dict):
        reg_name = _approval_region_name(payload, ctx0)
        if reg_name:
            try:
                mtlx = int(float(ctx0.get("current_task_match_top_left_x") or 0))
                mtly = int(float(ctx0.get("current_task_match_top_left_y") or 0))
                tw = int(float(ctx0.get("current_task_template_w") or 0))
                th = int(float(ctx0.get("current_task_template_h") or 0))
                if tw > 0 and th > 0:
                    overlays.append(
                        OverlayRect(
                            type="rect",
                            x=max(0, min(w - 1, mtlx)),
                            y=max(0, min(h - 1, mtly)),
                            w=max(1, min(w, tw)),
                            h=max(1, min(th, th)),
                            label=f"{reg_name} (match)",
                            stroke=stroke_region,
                        )
                    )
                else:
                    pair = screen_region_by_name(area_doc, reg_name, state_flat=state_flat)
                    if pair is not None and isinstance(pair[1].get("bbox"), dict):
                        left, top, right, bottom = pct_bbox_to_px_rect(pair[1]["bbox"], w, h)
                        overlays.append(
                            OverlayRect(
                                type="rect",
                                x=left,
                                y=top,
                                w=right - left,
                                h=bottom - top,
                                label=reg_name,
                                stroke=stroke_region,
                            )
                        )
            except (TypeError, ValueError):
                pass

    x_i, y_i = _tap_coords(payload)
    if x_i is not None and y_i is not None:
        overlays.append(
            OverlayCrosshair(
                type="crosshair",
                x=int(max(0, min(w - 1, x_i))),
                y=int(max(0, min(h - 1, y_i))),
            )
        )

    return overlays


def load_preview_bytes(
    *,
    instance_id: str,
    payload: dict[str, Any] | None,
    source: str,
) -> tuple[bytes | None, str, float | None]:
    src = (source or "capture").strip().lower()
    if src not in {"capture", "live"}:
        src = "capture"
    if src == "capture" and isinstance(payload, dict):
        rel_raw = _as_text(payload.get("preview_png_rel")).replace("\\", "/").lstrip("/")
        if rel_raw:
            root = references_root()
            path = (root / rel_raw).resolve()
            try:
                path.relative_to(root.resolve())
                if path.is_file():
                    return path.read_bytes(), path.relative_to(root).as_posix(), path.stat().st_mtime
            except (OSError, ValueError):
                pass
    return load_rolling_instance_preview(instance_id)


def image_dimensions(png: bytes) -> tuple[int, int]:
    import cv2
    import numpy as np

    arr = np.frombuffer(png, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        return 0, 0
    h, w = int(bgr.shape[0]), int(bgr.shape[1])
    return w, h
