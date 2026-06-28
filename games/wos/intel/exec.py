"""DSL exec handlers for the Intel screen."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import namedtuple
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2  # type: ignore[import-untyped]
import numpy as np
from games.wos.core.resources import adapter as resource_adapter
from games.wos.intel.planner import (
    DEFAULT_COST_PER_EVENT,
    IntelEvent,
    from_marker,
    plan_next,
)

from layout.types import Point
from tasks import dsl_runtime

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

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
_DEFAULT_MARCH_TTL_FIELD = "intel.march_ttl"
_DEFAULT_MARCH_ROUND_TRIP_MULTIPLIER = 2.0
_DEFAULT_MARCH_EXTRA_SECONDS = 15


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


def _as_bool_arg(args: dict[str, Any], key: str, *, default: bool = False) -> bool:
    value = args.get(key)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_quota_arg(args: dict[str, Any], key: str) -> int | None:
    """Daily-quota-left arg: a non-negative int, or ``None`` (unlimited/unknown)."""
    value = args.get(key)
    if value in (None, ""):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _decode_redis_text(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace").strip()
    return str(raw).strip()


def parse_march_ttl_seconds(raw: Any) -> int | None:
    """Parse deploy-screen TTL text into seconds.

    Accepts the raw OCR forms used by the game (``MM:SS`` / ``HH:MM:SS``) plus
    bare integer seconds as a fallback for tests or pre-parsed state.
    """
    text = _decode_redis_text(raw)
    if not text:
        return None
    groups = [int(part) for part in re.findall(r"\d+", text)]
    if not groups:
        return None
    if ":" in text:
        if len(groups) >= 3:
            h, m, s = groups[-3], groups[-2], groups[-1]
            return h * 3600 + m * 60 + s
        if len(groups) == 2:
            m, s = groups
            return m * 60 + s
        return None
    return groups[-1]


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


async def _read_player_stamina(ctx: DslExecContext) -> float | None:
    """Latest stamina estimate for this player (written by ``read_stamina_bar``)."""
    if ctx.redis_client is None or not ctx.player_id:
        return None
    try:
        raw = await ctx.redis_client.hget(
            f"wos:player:{ctx.player_id}:state", "stamina"
        )
    except Exception:
        logger.debug(
            "intel: stamina read failed player=%s", ctx.player_id, exc_info=True
        )
        return None
    if raw is None:
        return None
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


async def _intel_reserve(ctx: DslExecContext) -> int:
    """Stamina to hold back for higher-priority *active* events (e.g. Crazy Joe).

    Reads the player's flat state and reuses the stamina budget's reserve rule
    (:func:`stamina.model.reserve_for`): higher-priority demands hold their
    ``reserve_floor`` while their ``active_when`` flag is set. Those flags are fed
    by the calendar fan-out (``joe_event_active`` = Crazy Joe live-or-imminent), so
    the intel reserve tracks the event window with no hardcoded schedule.
    """
    if ctx.redis_client is None or not ctx.player_id:
        return 0
    try:
        raw = await ctx.redis_client.hgetall(f"wos:player:{ctx.player_id}:state")
    except Exception:
        logger.debug(
            "intel: reserve state read failed player=%s", ctx.player_id, exc_info=True
        )
        return 0
    state = {_decode_redis_text(k): _decode_redis_text(v) for k, v in (raw or {}).items()}
    try:
        from games.wos.core.stamina.adapter import load_budget
        from games.wos.core.stamina.model import reserve_for

        return reserve_for(load_budget(), "intel_events", state)
    except Exception:
        logger.debug("intel: reserve derivation failed", exc_info=True)
        return 0


async def _read_player_state_field(
    ctx: DslExecContext,
    field: str,
) -> str:
    if ctx.redis_client is None or not ctx.player_id or not field:
        return ""
    try:
        raw = await ctx.redis_client.hget(f"wos:player:{ctx.player_id}:state", field)
    except Exception:
        logger.debug(
            "intel: player state read failed player=%s field=%s",
            ctx.player_id,
            field,
            exc_info=True,
        )
        return ""
    return _decode_redis_text(raw)


async def _write_manual_march_lease(
    ctx: DslExecContext,
    *,
    now: float,
    lease_seconds: int,
    ttl_seconds: int,
) -> str | None:
    """Fallback ledger write when the scenario was launched without a reservation."""
    if ctx.redis_client is None or not ctx.player_id:
        return None
    res_id = f"intel_run:manual:{int(now)}"
    entry = {
        "id": res_id,
        "action_id": str(ctx.args.get("resource_action_id") or "intel_run"),
        "slots": 1,
        "stamina": 0,
        "troops": dict(ctx.args.get("assign_troops") or {}),
        "heroes": list(ctx.args.get("assign_heroes") or []),
        "created_at": now,
        "confirm_by": now,
        "expires_at": now + lease_seconds,
        "lease_seconds": lease_seconds,
        "confirmed": True,
        "source": "intel.deploy",
        "ttl_seconds": ttl_seconds,
    }
    await ctx.redis_client.hset(
        f"wos:player:{ctx.player_id}:resource_reservations",
        res_id,
        json.dumps(entry),
    )
    return res_id


async def _annotate_confirmed_march_lease(
    ctx: DslExecContext,
    *,
    reservation: str,
    ends_at: float,
    lease_seconds: int,
    ttl_seconds: int,
) -> None:
    if ctx.redis_client is None or not ctx.player_id or not reservation:
        return
    key = f"wos:player:{ctx.player_id}:resource_reservations"
    raw = await ctx.redis_client.hget(key, reservation)
    if not raw:
        return
    text = _decode_redis_text(raw)
    try:
        entry = json.loads(text)
    except (TypeError, ValueError):
        return
    entry.update(
        {
            "confirmed": True,
            "expires_at": ends_at,
            "lease_seconds": lease_seconds,
            "source": "intel.deploy",
            "ttl_seconds": ttl_seconds,
        }
    )
    await ctx.redis_client.hset(key, reservation, json.dumps(entry))


async def _write_march_lease_state(
    ctx: DslExecContext,
    *,
    ttl_seconds: int,
    lease_seconds: int,
    ends_at: float,
) -> None:
    if ctx.redis_client is None or not ctx.player_id:
        return
    await ctx.redis_client.hset(
        f"wos:player:{ctx.player_id}:state",
        mapping={
            "intel.march_ttl_seconds": str(ttl_seconds),
            "intel.march_lease_seconds": str(lease_seconds),
            "intel.march_ends_at": str(ends_at),
            "intel.march_lease_at": str(time.time()),
        },
    )


async def _exec_confirm_intel_march_lease(ctx: DslExecContext) -> None:
    """Confirm an intel march slot lease from the deploy-screen TTL.

    The resource planner creates a short unconfirmed reservation before pushing
    ``intel_run``. Once the Deploy button is pressed, this handler stretches that
    reservation to the real round-trip duration: outbound TTL * 2 + event slack.
    If no reservation is present (manual run), it creates an equivalent confirmed
    one-slot lease so 2..6 march-slot capacity is still respected.
    """
    ttl_field = str(ctx.args.get("ttl_field") or _DEFAULT_MARCH_TTL_FIELD).strip()
    ttl_raw = await _read_player_state_field(ctx, ttl_field)
    if not ttl_raw:
        ttl_raw = await _read_player_state_field(ctx, f"{ttl_field}_text")
    ttl_seconds = parse_march_ttl_seconds(ttl_raw)
    if ttl_seconds is None or ttl_seconds <= 0:
        ctx.result.update(
            {
                "action": "lease_skipped",
                "reason": "ttl_parse_failed",
                "ttl_field": ttl_field,
                "ttl_raw": ttl_raw,
            }
        )
        return

    multiplier = _as_float_arg(
        ctx.args,
        "round_trip_multiplier",
        _DEFAULT_MARCH_ROUND_TRIP_MULTIPLIER,
    )
    extra_seconds = _as_int_arg(
        ctx.args,
        "extra_seconds",
        _DEFAULT_MARCH_EXTRA_SECONDS,
    )
    lease_seconds = int(round(ttl_seconds * multiplier + extra_seconds))
    now = time.time()
    ends_at = now + lease_seconds

    reservation = str(ctx.args.get("resource_reservation") or "").strip()
    confirmed = False
    if reservation and ctx.redis_client is not None and ctx.player_id:
        try:
            confirmed = await resource_adapter.confirm_reservation(
                ctx.redis_client,
                ctx.player_id,
                reservation,
                ends_at=ends_at,
            )
        except Exception:
            logger.debug(
                "intel: resource reservation confirm failed player=%s reservation=%s",
                ctx.player_id,
                reservation,
                exc_info=True,
            )
            confirmed = False

    if confirmed:
        try:
            await _annotate_confirmed_march_lease(
                ctx,
                reservation=reservation,
                ends_at=ends_at,
                lease_seconds=lease_seconds,
                ttl_seconds=ttl_seconds,
            )
        except Exception:
            logger.debug(
                "intel: resource reservation annotate failed player=%s reservation=%s",
                ctx.player_id,
                reservation,
                exc_info=True,
            )

    fallback_reservation = ""
    if not confirmed:
        try:
            fallback_reservation = (
                await _write_manual_march_lease(
                    ctx,
                    now=now,
                    lease_seconds=lease_seconds,
                    ttl_seconds=ttl_seconds,
                )
                or ""
            )
        except Exception:
            logger.debug(
                "intel: fallback march lease write failed player=%s",
                ctx.player_id,
                exc_info=True,
            )

    try:
        await _write_march_lease_state(
            ctx,
            ttl_seconds=ttl_seconds,
            lease_seconds=lease_seconds,
            ends_at=ends_at,
        )
    except Exception:
        logger.debug(
            "intel: march lease state write failed player=%s",
            ctx.player_id,
            exc_info=True,
        )

    ctx.result.update(
        {
            "action": "lease_confirmed" if confirmed else "lease_recorded",
            "reservation": reservation if confirmed else fallback_reservation,
            "ttl_field": ttl_field,
            "ttl_raw": ttl_raw,
            "ttl_seconds": ttl_seconds,
            "lease_seconds": lease_seconds,
            "ends_at": ends_at,
        }
    )


async def _exec_tap_intel_fight(ctx: DslExecContext) -> None:
    """Tap the most valuable affordable Intel marker, or skip the run.

    Selection runs through the value-greedy Intel planner (the "brain"): it ranks
    visible markers by loot value and spends ``stamina - reserve`` on the best
    one, declining when the run isn't worth it. Without a live stamina estimate it
    falls back to the deterministic colour/kind pick (previous behaviour).

    Args:
      threshold: grayscale template score floor, default 0.72.
      nms_distance_px: merge nearby duplicate matches, default 40.
      strategy: best_score | center | topmost | bottommost — the no-stamina
        fallback pick, default best_score.
      reserve: stamina to hold back for higher-priority demands (e.g. Joe), default 0.
      cost: stamina per marker, default 10 (mirrors budget.yaml intel_events).
      daily_quota_left: remaining intel runs today; omit for unlimited.
      min_value / priority_only: drop low-value / non-gold-purple markers.
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
    stamina = await _read_player_stamina(ctx)
    explicit_reserve = ctx.args.get("reserve")
    reserve = (
        _as_int_arg(ctx.args, "reserve", 0)
        if explicit_reserve is not None
        else await _intel_reserve(ctx)
    )
    marker, plan_trace = select_planned_marker(
        markers,
        stamina=stamina,
        reserve=reserve,
        cost=_as_int_arg(ctx.args, "cost", DEFAULT_COST_PER_EVENT),
        daily_quota_left=_as_quota_arg(ctx.args, "daily_quota_left"),
        min_value=_as_float_arg(ctx.args, "min_value", 0.0),
        priority_only=_as_bool_arg(ctx.args, "priority_only"),
        fallback_strategy=strategy,
    )
    if marker is None:
        # Nothing detected, or the planner declined the budget — skip the run
        # rather than clear a low-value pin or overspend the shared stamina pool.
        action = "not_found" if not markers else "skipped"
        ctx.result.update({"action": action, "threshold": threshold, **plan_trace})
        logger.info(
            "dsl exec tap_intel_fight: action=%s instance=%s reason=%s stamina=%s detected=%d",
            action,
            ctx.instance_id,
            plan_trace.get("reason"),
            stamina,
            len(markers),
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
            "stamina": stamina,
            "reserve": plan_trace.get("reserve"),
            "reason": plan_trace.get("reason"),
            "value": plan_trace.get("value"),
            "rank": plan_trace.get("rank"),
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


async def _exec_read_intel_stamina(ctx: DslExecContext) -> None:
    """Read the intel board's «current/max» stamina («44/90», green bar bottom-left)
    and store current + max to player state for the stamina budget.

    The avatar bar reader (``read_stamina_bar``) doesn't populate on the RU build,
    so intel reads its own explicit counter on-board instead of depending on the
    periodic ``read_stamina`` cron — the value is then fresh every run and
    ``_read_player_stamina`` (hence ``tap_intel_fight``) sees a real number rather
    than ``no_stamina_signal``. OCR ``fast_line`` reads the whole «44/90» reliably
    (~0.92); the «44»-alone crop is mangled by the green/grey bar edge.
    """
    from layout.area_lookup import screen_region_by_name
    from layout.area_manifest import load_area_doc
    from layout.types import Region
    from services import get_active_module_catalog, get_ocr_client, get_repo_root

    area_doc = load_area_doc(get_repo_root(), game=get_active_module_catalog())
    pair = screen_region_by_name(area_doc, "intel.stamina") if area_doc else None
    bbox = pair[1].get("bbox") if pair and isinstance(pair[1], dict) else None
    if not isinstance(bbox, dict):
        ctx.result.update({"action": "unknown_region"})
        return
    actions = dsl_runtime.bot_actions()
    try:
        image = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
    except Exception:
        logger.exception("intel stamina: capture failed instance=%s", ctx.instance_id)
        ctx.result.update({"action": "capture_failed"})
        return
    h, w = image.shape[:2]
    reg = Region(
        int(round(float(bbox["x"]) / 100.0 * w)),
        int(round(float(bbox["y"]) / 100.0 * h)),
        int(round(float(bbox["width"]) / 100.0 * w)),
        int(round(float(bbox["height"]) / 100.0 * h)),
    )
    res = await get_ocr_client().ocr_region(image, reg, preprocess="fast_line")
    text = (getattr(res, "text", "") or "").strip()
    m = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if not m:
        ctx.result.update({"action": "parse_failed", "text": text})
        return
    cur, mx = int(m.group(1)), int(m.group(2))
    if ctx.redis_client is not None and ctx.player_id:
        try:
            await ctx.redis_client.hset(
                f"wos:player:{ctx.player_id}:state",
                mapping={
                    "stamina": str(cur),
                    "stamina_max": str(mx),
                    "stamina_at": str(time.time()),
                    "stamina_source": "intel",
                },
            )
        except Exception:
            logger.exception("intel stamina: hset failed player=%s", ctx.player_id)
    ctx.result.update({"action": "measured", "stamina": cur, "stamina_max": mx, "text": text})
    logger.info(
        "dsl exec read_intel_stamina: instance=%s stamina=%d/%d", ctx.instance_id, cur, mx
    )


DSL_EXEC_HANDLERS = {
    "confirm_intel_march_lease": _exec_confirm_intel_march_lease,
    "tap_intel_fight": _exec_tap_intel_fight,
    "read_intel_stamina": _exec_read_intel_stamina,
}
