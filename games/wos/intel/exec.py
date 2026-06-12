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
_FIGHT_TEMPLATE = _MODULE_DIR / "references" / "crop" / "main_intel.fight.png"
_DEFAULT_THRESHOLD = 0.72
_DEFAULT_NMS_DISTANCE_PX = 40


class IntelFightMarker:
    __slots__ = ("h", "score", "w", "x", "y")

    def __init__(self, *, x: int, y: int, w: int, h: int, score: float) -> None:
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.score = score

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


def _load_gray_template(path: Path = _FIGHT_TEMPLATE) -> np.ndarray | None:
    template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if template is None or template.size == 0:
        return None
    return template


def _is_far_enough(
    candidate: IntelFightMarker,
    accepted: list[IntelFightMarker],
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


def detect_fight_markers(
    image_bgr: np.ndarray,
    *,
    threshold: float = _DEFAULT_THRESHOLD,
    nms_distance_px: int = _DEFAULT_NMS_DISTANCE_PX,
    template_gray: np.ndarray | None = None,
) -> list[IntelFightMarker]:
    """Find visible Intel fight pins using color-tolerant grayscale matching."""
    if image_bgr is None or not hasattr(image_bgr, "shape"):
        return []
    template = template_gray if template_gray is not None else _load_gray_template()
    if template is None:
        return []

    frame_gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    th, tw = template.shape[:2]
    if frame_gray.shape[0] < th or frame_gray.shape[1] < tw:
        return []

    result = cv2.matchTemplate(frame_gray, template, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(result >= float(threshold))
    raw: list[IntelFightMarker] = [
        IntelFightMarker(
            x=int(x),
            y=int(y),
            w=int(tw),
            h=int(th),
            score=float(result[y, x]),
        )
        for y, x in zip(ys, xs, strict=False)
    ]
    raw.sort(key=lambda marker: marker.score, reverse=True)

    accepted: list[IntelFightMarker] = []
    for marker in raw:
        if _is_far_enough(marker, accepted, min_distance_px=nms_distance_px):
            accepted.append(marker)
    return accepted


def _pick_marker(markers: list[IntelFightMarker], strategy: str) -> IntelFightMarker | None:
    if not markers:
        return None
    strategy_lc = strategy.strip().lower()
    if strategy_lc == "topmost":
        return min(markers, key=lambda m: (m.y, -m.score))
    if strategy_lc == "bottommost":
        return max(markers, key=lambda m: (m.y, m.score))
    if strategy_lc == "center":
        return min(
            markers,
            key=lambda m: (m.center.x - 360) ** 2 + (m.center.y - 640) ** 2,
        )
    return max(markers, key=lambda m: m.score)


async def _exec_tap_intel_fight(ctx: DslExecContext) -> None:
    """Tap one visible Intel fight marker.

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

    markers = detect_fight_markers(
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
            "detected": len(markers),
            "markers": [
                {
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
        "dsl exec tap_intel_fight: action=%s instance=%s tap=(%d,%d) score=%.3f detected=%d",
        "tapped" if tapped else "tap_blocked",
        ctx.instance_id,
        point.x,
        point.y,
        marker.score,
        len(markers),
    )


DSL_EXEC_HANDLERS = {
    "tap_intel_fight": _exec_tap_intel_fight,
}
