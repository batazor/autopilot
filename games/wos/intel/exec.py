"""DSL exec handlers for the Intel screen."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2  # type: ignore[import-untyped]
import numpy as np

from layout.types import Point
from tasks import dsl_runtime

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).resolve().parent
_MARKER_TEMPLATES = {
    "fight": _MODULE_DIR / "references" / "crop" / "main_intel.fight.png",
    "skull": _MODULE_DIR / "references" / "crop" / "claim_intel.skull.png",
    "skull_horned": _MODULE_DIR / "references" / "crop" / "camp_intel.skull_horned.png",
    "camp": _MODULE_DIR / "references" / "crop" / "camp_intel.camp.png",
}
_MARKER_KIND_PRIORITY = {
    # Within the same color tier, prefer the rarer/special intel types first.
    "skull_horned": 0,
    "camp": 0,
    "fight": 1,
    "skull": 1,
}
_MARKER_COLOR_PRIORITY = {
    "gold": 0,
    "purple": 1,
    "blue": 2,
    "green": 2,
    "unknown": 3,
}
_DEFAULT_THRESHOLD = 0.72
_DEFAULT_NMS_DISTANCE_PX = 40


class IntelMarker:
    __slots__ = ("color", "h", "kind", "score", "w", "x", "y")

    def __init__(
        self,
        *,
        x: int,
        y: int,
        w: int,
        h: int,
        score: float,
        kind: str,
        color: str = "unknown",
    ) -> None:
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.score = score
        self.kind = kind
        self.color = color

    @property
    def center(self) -> Point:
        return Point(self.x + self.w // 2, self.y + self.h // 2)


def _as_int_arg(args: dict[str, Any], key: str, default: int) -> int:
    try:
        value = int(args.get(key))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _as_float_arg(args: dict[str, Any], key: str, default: float) -> float:
    try:
        value = float(args.get(key))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _load_gray_template(path: Path) -> np.ndarray | None:
    template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if template is None or template.size == 0:
        return None
    return template


def _load_marker_templates() -> dict[str, np.ndarray]:
    templates: dict[str, np.ndarray] = {}
    for kind, path in _MARKER_TEMPLATES.items():
        template = _load_gray_template(path)
        if template is not None:
            templates[kind] = template
    return templates


def _is_far_enough(
    candidate: IntelMarker,
    accepted: list[IntelMarker],
    *,
    min_distance_px: int,
) -> bool:
    min_dist_sq = min_distance_px * min_distance_px
    c = candidate.center
    for marker in accepted:
        m = marker.center
        dx = c.x - m.x
        dy = c.y - m.y
        if dx * dx + dy * dy < min_dist_sq:
            return False
    return True


def _marker_color_from_hsv(frame_hsv: np.ndarray, marker: IntelMarker) -> str:
    """Classify the marker pin color from its saturated pixels."""
    height, width = frame_hsv.shape[:2]
    x0 = max(0, marker.x - 8)
    y0 = max(0, marker.y - 8)
    x1 = min(width, marker.x + marker.w + 8)
    y1 = min(height, marker.y + marker.h + 8)
    if x0 >= x1 or y0 >= y1:
        return "unknown"

    roi = frame_hsv[y0:y1, x0:x1]
    saturated = (roi[:, :, 1] > 60) & (roi[:, :, 2] > 80)
    hues = roi[:, :, 0][saturated]
    if hues.size == 0:
        return "unknown"

    counts = {
        "gold": int(((hues >= 10) & (hues <= 38)).sum()),
        "green": int(((hues > 38) & (hues <= 85)).sum()),
        "blue": int(((hues > 85) & (hues <= 125)).sum()),
        "purple": int(((hues > 125) & (hues <= 165)).sum()),
    }
    color, count = max(counts.items(), key=lambda item: item[1])
    if count < 25 or count / float(hues.size) < 0.10:
        return "unknown"
    return color


def detect_intel_markers(
    image_bgr: np.ndarray,
    *,
    threshold: float = _DEFAULT_THRESHOLD,
    nms_distance_px: int = _DEFAULT_NMS_DISTANCE_PX,
    templates_gray: dict[str, np.ndarray] | None = None,
) -> list[IntelMarker]:
    """Find visible Intel action pins using color-tolerant grayscale matching."""
    if image_bgr is None or not hasattr(image_bgr, "shape"):
        return []
    templates = templates_gray if templates_gray is not None else _load_marker_templates()
    if not templates:
        return []

    frame_gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    frame_hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    raw: list[IntelMarker] = []
    for kind, template in templates.items():
        th, tw = template.shape[:2]
        if frame_gray.shape[0] < th or frame_gray.shape[1] < tw:
            continue

        result = cv2.matchTemplate(frame_gray, template, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= float(threshold))
        for y, x in zip(ys, xs, strict=False):
            marker = IntelMarker(
                x=int(x),
                y=int(y),
                w=int(tw),
                h=int(th),
                score=float(result[y, x]),
                kind=kind,
            )
            marker.color = _marker_color_from_hsv(frame_hsv, marker)
            raw.append(marker)
    raw.sort(key=lambda marker: marker.score, reverse=True)

    accepted: list[IntelMarker] = []
    for marker in raw:
        if _is_far_enough(marker, accepted, min_distance_px=nms_distance_px):
            accepted.append(marker)
    return accepted


def detect_fight_markers(
    image_bgr: np.ndarray,
    *,
    threshold: float = _DEFAULT_THRESHOLD,
    nms_distance_px: int = _DEFAULT_NMS_DISTANCE_PX,
    template_gray: np.ndarray | None = None,
) -> list[IntelMarker]:
    """Backward-compatible wrapper for old tests/callers."""
    fight_template = (
        template_gray
        if template_gray is not None
        else _load_gray_template(_MARKER_TEMPLATES["fight"])
    )
    templates = {"fight": fight_template} if fight_template is not None else {}
    return detect_intel_markers(
        image_bgr,
        threshold=threshold,
        nms_distance_px=nms_distance_px,
        templates_gray=templates,
    )


def _kind_priority(marker: IntelMarker) -> int:
    return _MARKER_KIND_PRIORITY.get(marker.kind, 1)


def _color_priority(marker: IntelMarker) -> int:
    return _MARKER_COLOR_PRIORITY.get(marker.color, _MARKER_COLOR_PRIORITY["unknown"])


def _marker_base_priority(marker: IntelMarker) -> tuple[int, int]:
    return (_color_priority(marker), _kind_priority(marker))


def _pick_marker(markers: list[IntelMarker], strategy: str) -> IntelMarker | None:
    if not markers:
        return None
    strategy_lc = strategy.strip().lower()
    if strategy_lc == "topmost":
        return min(markers, key=lambda m: (*_marker_base_priority(m), m.y, -m.score))
    if strategy_lc == "bottommost":
        return min(markers, key=lambda m: (*_marker_base_priority(m), -m.y, -m.score))
    if strategy_lc == "center":
        return min(
            markers,
            key=lambda m: (
                *_marker_base_priority(m),
                (m.center.x - 360) ** 2 + (m.center.y - 640) ** 2,
                -m.score,
            ),
        )
    return min(markers, key=lambda m: (*_marker_base_priority(m), -m.score))


async def _exec_tap_intel_fight(ctx: DslExecContext) -> None:
    """Tap one visible Intel action marker.

    Args:
      threshold: grayscale template score floor, default 0.72.
      nms_distance_px: merge nearby duplicate matches, default 40.
      strategy: best_score | center | topmost | bottommost, default best_score.
    """
    threshold = _as_float_arg(ctx.args, "threshold", _DEFAULT_THRESHOLD)
    nms_distance_px = _as_int_arg(
        ctx.args,
        "nms_distance_px",
        _DEFAULT_NMS_DISTANCE_PX,
    )
    strategy = str(ctx.args.get("strategy") or "best_score")

    actions = dsl_runtime.bot_actions()
    try:
        image = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
    except Exception:
        logger.exception(
            "dsl exec tap_intel_fight: capture_screen_bgr failed instance=%s",
            ctx.instance_id,
        )
        ctx.result.update({"action": "capture_failed"})
        return

    markers = detect_intel_markers(
        image,
        threshold=threshold,
        nms_distance_px=nms_distance_px,
    )
    marker = _pick_marker(markers, strategy)
    if marker is None:
        ctx.result.update(
            {
                "action": "not_found",
                "threshold": threshold,
                "markers": [],
            }
        )
        return

    point = marker.center
    try:
        tapped = await asyncio.to_thread(
            actions.tap,
            ctx.instance_id,
            point,
            approval_region="intel.fight",
            approval_context={
                "score": round(marker.score, 4),
                "strategy": strategy,
                "kind": marker.kind,
                "color": marker.color,
            },
        )
    except Exception:
        logger.exception(
            "dsl exec tap_intel_fight: tap failed instance=%s point=%s",
            ctx.instance_id,
            point,
        )
        ctx.result.update({"action": "tap_failed", "tap_x": point.x, "tap_y": point.y})
        return

    ctx.result.update(
        {
            "action": "tapped" if tapped else "tap_blocked",
            "tap_x": point.x,
            "tap_y": point.y,
            "score": marker.score,
            "kind": marker.kind,
            "color": marker.color,
            "detected": len(markers),
            "markers": [
                {
                    "kind": m.kind,
                    "color": m.color,
                    "x": m.x,
                    "y": m.y,
                    "w": m.w,
                    "h": m.h,
                    "score": m.score,
                }
                for m in markers[:20]
            ],
        }
    )
    logger.info(
        "dsl exec tap_intel_fight: action=%s instance=%s kind=%s tap=(%d,%d) score=%.3f detected=%d",
        "tapped" if tapped else "tap_blocked",
        ctx.instance_id,
        marker.kind,
        point.x,
        point.y,
        marker.score,
        len(markers),
    )


DSL_EXEC_HANDLERS = {
    "tap_intel_fight": _exec_tap_intel_fight,
}
