"""Intel-board vision: detect the action pins and pick which one to clear.

This is the cv2-coupled layer behind ``tap_intel_fight``. It locates the Intel
markers on a captured frame (colour-tolerant grayscale template match + HSV pin
colour), then bridges them to the pure value-greedy planner in :mod:`planner`
via :func:`select_planned_marker`. No Redis, no DSL — :mod:`exec` owns the I/O.
"""
from __future__ import annotations

import logging
from collections import namedtuple
from pathlib import Path
from typing import Any

import cv2  # type: ignore[import-untyped]
import numpy as np

from layout.types import Point

from .planner import (
    DEFAULT_COST_PER_EVENT,
    IntelEvent,
    from_marker,
    plan_next,
)

logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).resolve().parent
_CROP_DIR = _MODULE_DIR / "references" / "crop"


# One template-crop spec: a unique ``name`` matched as one logical ``kind``.
# Several art variants can share a logical ``kind`` (the normal tent and the
# special-event tent are both ``camp``), so the planner values them identically.
# A plain namedtuple (not a dataclass) so this module stays importable via the
# bare ``importlib`` loader the tests/exec-registry use (a dataclass would need
# its module registered in ``sys.modules`` to resolve string annotations).
_MarkerTemplate = namedtuple("_MarkerTemplate", ["name", "kind", "path"])


_MARKER_TEMPLATES: tuple[_MarkerTemplate, ...] = (
    _MarkerTemplate("fight", "fight", _CROP_DIR / "main_intel.fight.png"),
    _MarkerTemplate("skull", "skull", _CROP_DIR / "claim_intel.skull.png"),
    _MarkerTemplate("skull_horned", "skull_horned", _CROP_DIR / "camp_intel.skull_horned.png"),
    _MarkerTemplate("camp", "camp", _CROP_DIR / "camp_intel.camp.png"),
    # Special-event Intel skin (references/main_special.png) — new marker art.
    _MarkerTemplate("camp_v2", "camp", _CROP_DIR / "main_special_intel.camp_v2.png"),
    _MarkerTemplate("fight_v3", "fight", _CROP_DIR / "main_special_intel.fight_v3.png"),
    _MarkerTemplate("beast", "beast", _CROP_DIR / "main_special_intel.fight_v2.png"),
)
_TEMPLATE_KIND_BY_NAME: dict[str, str] = {t.name: t.kind for t in _MARKER_TEMPLATES}
_MARKER_KIND_PRIORITY = {
    # Within the same color tier, prefer the rarer/special intel types first.
    "skull_horned": 0,
    "camp": 0,
    "beast": 0,
    "fight": 1,
    "skull": 1,
}


def _logical_kind(name: str) -> str:
    """Map a template *name* to its logical *kind* (unknown names pass through).

    Lets callers/tests pass ``templates_gray`` keyed by kind (e.g. ``{"fight": ...}``)
    and still get the right ``IntelMarker.kind``.
    """
    return _TEMPLATE_KIND_BY_NAME.get(name, name)


def _template_path(name: str) -> Path | None:
    for spec in _MARKER_TEMPLATES:
        if spec.name == name:
            return spec.path
    return None


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


def _load_gray_template(path: Path) -> np.ndarray | None:
    template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if template is None or template.size == 0:
        return None
    return template


def _load_marker_templates() -> dict[str, np.ndarray]:
    """Load every template crop, keyed by its unique template *name*."""
    templates: dict[str, np.ndarray] = {}
    for spec in _MARKER_TEMPLATES:
        template = _load_gray_template(spec.path)
        if template is not None:
            templates[spec.name] = template
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
    for name, template in templates.items():
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
                kind=_logical_kind(name),
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
    """Backward-compatible wrapper for old tests/callers (matches only the
    original ``fight`` art, never the special-event variants)."""
    if template_gray is not None:
        fight_template = template_gray
    else:
        fight_path = _template_path("fight")
        fight_template = _load_gray_template(fight_path) if fight_path else None
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


def select_planned_marker(
    markers: list[IntelMarker],
    *,
    stamina: float | None,
    reserve: int = 0,
    cost: int = DEFAULT_COST_PER_EVENT,
    daily_quota_left: int | None = None,
    min_value: float = 0.0,
    priority_only: bool = False,
    fallback_strategy: str = "best_score",
) -> tuple[IntelMarker | None, dict[str, Any]]:
    """Choose which marker to clear this pass under the shared stamina budget.

    Bridges the cv2 detector to the pure value-greedy planner (the "brain"). With
    no live stamina signal we can't budget, so we fall back to the deterministic
    :func:`_pick_marker` (the previous behaviour — never worse). With a stamina
    estimate the planner ranks markers by loot value and may *decline* the run —
    insufficient stamina, daily quota exhausted, or nothing worth taking —
    returning ``(None, trace)`` so the caller skips instead of burning a march on
    a low-value pin. The ``trace`` dict is surfaced on the scenario result.
    """
    if not markers:
        return None, {"reason": "no_markers", "detected": 0}
    if stamina is None:
        return _pick_marker(markers, fallback_strategy), {
            "reason": "no_stamina_signal",
            "detected": len(markers),
        }

    events: list[IntelEvent] = []
    by_event: dict[int, IntelMarker] = {}
    for marker in markers:
        event = from_marker(marker)
        events.append(event)
        by_event[id(event)] = marker
    plan = plan_next(
        events,
        stamina=stamina,
        cost_per_event=cost,
        reserve=reserve,
        daily_quota_left=daily_quota_left,
        min_value=min_value,
        priority_only=priority_only,
    )
    trace: dict[str, Any] = {
        "reason": plan.reason,
        "detected": len(markers),
        "stamina": stamina,
        "reserve": plan.reserve,
        "batch_cost": plan.total_cost,
        "stamina_short": plan.stamina_short,
    }
    step = plan.step
    if step is None:
        return None, trace
    trace["value"] = round(step.value, 4)
    trace["rank"] = step.rank
    return by_event.get(id(step.event)), trace
