"""Pure decision engine for the Fishing Tournament "Trial Stages" mini-game.

The mini-game (``gameplay`` screen) is a steer-the-hook game: a glowing cyan
ring (the hook/bait) hangs near the top and the player **swipes horizontally**
to move it left/right across the lane of swimming fish. There are two phases,
gated by the on-screen altitude counter (``fishing_tournament.level`` — an OCR
"N/100" readout):

* **dodge** — when altitude is flat or falling, steer the hook *away* from the
  nearest fish (avoid contact).
* **collect** — while altitude is *climbing* ("набор высоты"), steer the hook
  *toward* the nearest fish to catch it.

The phase comes from the **direction** of the altitude counter — each reading is
compared with the recent ones, not latched against a baseline — so the bot
collects only while actively gaining height and reverts to dodging the moment
the climb stops.

Fish move at roughly **constant velocity**, so a fish tracked across two frames
gets a velocity vector and the engine aims at its *extrapolated* position
``lead_s`` seconds ahead — compensating for the capture→inference→swipe latency
so the swipe lands where the fish *will be*, not where it was.

Everything here is pure and frame-local so it can be unit-tested without a
device or the inference service. The live driver (``fish_drive``) and the
``/fish-detect`` dry-run overlay both call :func:`plan_action`; the worker
``exec`` handler turns the returned :class:`SwipePlan` into an ADB swipe.

Inputs are :class:`~api.services.fish_common.FishDetectionRow` dicts (the same
shape the detector/video tools already produce) plus the raw BGR frame (only the
hook locator looks at pixels — it delegates to the fishing_tournament
``hook_detect`` module, which also reports whether the protection shield is up)
and, optionally, the previous frame's detections + the elapsed time for velocity
tracking.
"""
from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:
    from collections.abc import Sequence

    import numpy as np
    from games.wos.events.fishing_tournament.hook_detect import HookDetection

    from api.services.fish_common import FishDetectionRow

# Emulator framebuffer (mandatory 720x1280 portrait).
_W, _H = 720, 1280

Phase = Literal["dodge", "collect"]
LevelTrend = Literal["up", "down", "flat"]

# --- altitude trend ----------------------------------------------------------
_LEVEL_TREND_WINDOW = 3       # readings compared to decide up/down/flat

# --- hook vertical zone (travel direction) -----------------------------------
# The hook descends from the top and reels back up: high on screen ⇒ going down
# ("едем вниз"), low ⇒ going up ("набор высоты"). The mid band is ambiguous.
_HOOK_TOP_ZONE = 0.45         # hook_y fraction below this ⇒ descending
_HOOK_BOTTOM_ZONE = 0.55      # hook_y fraction above this ⇒ ascending

# --- fish tracking -----------------------------------------------------------
_MATCH_MAX_DIST_PX = 160.0    # max centre travel to call it the same fish

# --- steering tuning ---------------------------------------------------------
_COLLECT_DEADZONE_PX = 24     # hook within this of the target x → already aligned
_DODGE_TRIGGER_PX = 150       # only dodge a fish closer than this to the hook
_SWIPE_MIN_PX = 40            # don't bother with sub-this nudges
_SWIPE_MAX_PX = 320          # cap a single swipe so it stays on-screen


class TrackedFish(TypedDict):
    """A detection annotated with cross-frame velocity + a lead position."""

    center_x: int            # measured centre on the current frame
    center_y: int
    lead_x: int              # extrapolated centre at +lead_s (clamped on-frame)
    lead_y: int
    vx: float                # px/s (0 when unmatched / no dt)
    vy: float
    speed_px_s: float
    width: int
    height: int
    class_name: str
    confidence: float
    tracked: bool            # matched to a previous fish ⇒ velocity is real


class SwipePlan(TypedDict):
    """A horizontal steer of the hook, in pixel coords of the source frame."""

    direction: Literal["left", "right"]
    from_x: int
    from_y: int
    to_x: int
    to_y: int
    dx: int                   # signed horizontal travel (px); sign matches direction
    phase: Phase
    target_index: int         # index into the tracked/detections list, or -1
    reason: str


class ActionPlan(TypedDict):
    """Full per-tick decision: phase, where the hook is, and the swipe to do."""

    phase: Phase
    level_trend: LevelTrend
    level: int | None
    level_total: int | None
    hook_x: int | None
    hook_y: int | None
    protected: bool | None    # blue shield ring present around the hook (None: unknown)
    hook_direction: str | None  # "down" | "up" | None — travel dir from hook y-zone
    target_index: int         # index into ``tracked``, or -1
    target_lead_x: int | None
    target_lead_y: int | None
    lead_s: float
    swipe: SwipePlan | None
    tracked: list[TrackedFish]
    detections: int


# --- altitude counter --------------------------------------------------------
_LEVEL_RE = re.compile(r"(\d{1,3})\s*/\s*(\d{1,4})")


def parse_level(text: str | None) -> tuple[int, int] | None:
    """Parse an OCR "N/100" altitude readout into ``(current, total)``.

    Tolerates surrounding noise and spaces around the slash. Returns ``None``
    when no ``N/M`` pair is present.
    """
    if not text:
        return None
    m = _LEVEL_RE.search(str(text))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def level_trend(
    levels: Sequence[int], *, window: int = _LEVEL_TREND_WINDOW, min_delta: int = 1
) -> LevelTrend:
    """Direction of the altitude counter over the last ``window`` readings.

    Compares the newest reading with the oldest in the window: a net rise of at
    least ``min_delta`` is ``up`` ("набор высоты"), a net fall is ``down``,
    anything in between is ``flat``. The window smooths single-frame OCR jitter.
    """
    if len(levels) < 2:
        return "flat"
    recent = levels[-window:]
    delta = recent[-1] - recent[0]
    if delta >= min_delta:
        return "up"
    if delta <= -min_delta:
        return "down"
    return "flat"


def decide_phase(
    levels: Sequence[int], *, min_delta: int = 1, window: int = _LEVEL_TREND_WINDOW
) -> Phase:
    """``collect`` while the altitude counter is climbing, else ``dodge``."""
    return "collect" if level_trend(levels, window=window, min_delta=min_delta) == "up" else "dodge"


def hook_zone_direction(hook_y: int | None, frame_h: int) -> str | None:
    """Travel direction from the hook's vertical position, or ``None`` mid-screen.

    The hook descends from the top and reels back up, so a hook high on screen
    means we are going *down* ("едем вниз"), low means going *up* ("набор
    высоты"). The middle band is ambiguous → ``None`` (the caller falls back to
    the shield / altitude counter).
    """
    if hook_y is None or frame_h <= 0:
        return None
    frac = hook_y / frame_h
    if frac < _HOOK_TOP_ZONE:
        return "down"
    if frac > _HOOK_BOTTOM_ZONE:
        return "up"
    return None


def resolve_phase(
    levels: Sequence[int],
    *,
    protected: bool = False,
    hook_direction: str | None = None,
    min_delta: int = 1,
) -> Phase:
    """Final phase from the direction signals, strongest first.

    Going **down** ⇒ ``dodge``; going **up** ⇒ ``collect``. The hook's vertical
    position is the primary tell (top ⇒ down, bottom ⇒ up); the blue shield also
    means descending; in the ambiguous mid-screen band with no shield we fall
    back to the altitude counter's direction.
    """
    if protected or hook_direction == "down":
        return "dodge"
    if hook_direction == "up":
        return "collect"
    return decide_phase(levels, min_delta=min_delta)


# --- hook detection ----------------------------------------------------------
def _detect_hook_state(frame: np.ndarray | None) -> HookDetection | None:
    """Run the robust multi-feature hook detector, or ``None`` for an empty frame.

    Lazy import: the detector lives in the games tree, and keeping it off
    fish_engine's module-load path preserves the cheap import the worker relies on.
    """
    if frame is None or getattr(frame, "size", 0) == 0:
        return None
    from games.wos.events.fishing_tournament.hook_detect import detect_hook

    return detect_hook(frame)


def _hook_center(det: HookDetection | None) -> tuple[int, int] | None:
    """Best hook centre from a detection: blue shield ring → green node, else None.

    The ring is the bait/catch point; when the shield is spent the ring vanishes,
    so we fall back to the green node at the top of the hook (same column — what
    horizontal steering needs). The fishing line alone is too weak to anchor on,
    so a line-only detection yields ``None`` and the caller uses its top-centre
    fallback (matching the old behaviour when no ring was found).
    """
    if det is None:
        return None
    if det.ring is not None:
        return int(round(det.ring.x)), int(round(det.ring.y))
    if det.green_node is not None:
        return int(round(det.green_node[0])), int(round(det.green_node[1]))
    return None


def find_hook(frame: np.ndarray) -> tuple[int, int] | None:
    """Locate the hook's centre in ``frame`` → ``(cx, cy)`` px, or ``None``.

    Delegates to the fishing_tournament ``hook_detect`` module, which finds the
    hook from three features (blue shield ring, green node, fishing line) and is
    robust to the same-blue icebergs at the screen edges. ``None`` when neither
    the ring nor the green node is found (hook off-screen / occluded) — the caller
    falls back to a fixed top-centre point.
    """
    return _hook_center(_detect_hook_state(frame))


# --- fish tracking + lead extrapolation --------------------------------------
def track_fish(
    prev_rows: Sequence[FishDetectionRow],
    cur_rows: Sequence[FishDetectionRow],
    *,
    dt_s: float | None,
    lead_s: float = 0.0,
    max_match_dist: float = _MATCH_MAX_DIST_PX,
    frame_w: int = _W,
    frame_h: int = _H,
) -> list[TrackedFish]:
    """Annotate ``cur_rows`` with velocity (greedy nearest match to the previous
    frame) and a lead position ``lead_s`` seconds ahead.

    Motion is assumed locally linear, so ``lead = centre + velocity * lead_s``.
    Unmatched fish (new, or no usable ``dt_s``) get zero velocity and lead at
    their measured centre — safe (no extrapolation when we can't measure it).
    """
    out: list[TrackedFish] = []
    used: set[int] = set()
    valid_dt = dt_s is not None and dt_s > 1e-3
    for c in cur_rows:
        cx, cy = int(c["center_x"]), int(c["center_y"])
        vx = vy = 0.0
        matched = False
        if valid_dt and prev_rows:
            best_j, best_d = -1, max_match_dist
            for j, p in enumerate(prev_rows):
                if j in used:
                    continue
                d = math.hypot(cx - p["center_x"], cy - p["center_y"])
                if d < best_d:
                    best_d, best_j = d, j
            if best_j >= 0:
                used.add(best_j)
                p = prev_rows[best_j]
                vx = (cx - int(p["center_x"])) / dt_s  # type: ignore[operator]
                vy = (cy - int(p["center_y"])) / dt_s  # type: ignore[operator]
                matched = True
        lead_x = int(round(min(frame_w - 1, max(0, cx + vx * lead_s))))
        lead_y = int(round(min(frame_h - 1, max(0, cy + vy * lead_s))))
        out.append(
            TrackedFish(
                center_x=cx,
                center_y=cy,
                lead_x=lead_x,
                lead_y=lead_y,
                vx=round(vx, 1),
                vy=round(vy, 1),
                speed_px_s=round(math.hypot(vx, vy), 1),
                width=int(c["width"]),
                height=int(c["height"]),
                class_name=str(c["class_name"]),
                confidence=float(c["confidence"]),
                tracked=matched,
            )
        )
    return out


def _nearest_index(points: Sequence[tuple[int, int]], hook: tuple[int, int]) -> int:
    """Index of the point nearest the hook (Euclidean), or ``-1`` when empty."""
    hx, hy = hook
    best_i, best_d = -1, math.inf
    for i, (px, py) in enumerate(points):
        d = math.hypot(px - hx, py - hy)
        if d < best_d:
            best_d, best_i = d, i
    return best_i


def plan_swipe(
    hook: tuple[int, int],
    target_x: int,
    phase: Phase,
    *,
    target_index: int = -1,
    frame_w: int = _W,
) -> SwipePlan | None:
    """Plan the horizontal swipe that steers the hook for ``phase``.

    ``collect`` moves the hook toward ``target_x`` (``None`` when already within
    the deadzone); ``dodge`` moves it away, picking the side with more room when
    the fish sits right on the hook.
    """
    hx, hy = hook
    offset = target_x - hx  # +ve: target is to the right of the hook

    if phase == "collect":
        if abs(offset) <= _COLLECT_DEADZONE_PX:
            return None  # ring already over the fish — let it grab
        sign = 1 if offset > 0 else -1
        reason = "chase nearest fish"
    else:  # dodge
        if abs(offset) >= _DODGE_TRIGGER_PX:
            return None  # nearest fish is far enough — hold position
        if offset > 0:
            sign = -1  # fish to the right → flee left
        elif offset < 0:
            sign = 1  # fish to the left → flee right
        else:
            # Fish dead-centre on the hook: flee toward the side with more room.
            sign = 1 if hx < frame_w / 2 else -1
        reason = "flee nearest fish"

    dx = sign * int(min(_SWIPE_MAX_PX, max(_SWIPE_MIN_PX, abs(offset) or _SWIPE_MIN_PX)))
    to_x = int(min(frame_w - 1, max(0, hx + dx)))
    dx = to_x - hx
    if dx == 0:
        return None
    return SwipePlan(
        direction="right" if dx > 0 else "left",
        from_x=hx,
        from_y=hy,
        to_x=to_x,
        to_y=hy,
        dx=dx,
        phase=phase,
        target_index=target_index,
        reason=reason,
    )


def plan_action(
    frame: np.ndarray | None,
    detections: Sequence[FishDetectionRow],
    levels: Sequence[int],
    *,
    prev_detections: Sequence[FishDetectionRow] | None = None,
    dt_s: float | None = None,
    lead_s: float = 0.0,
    min_delta: int = 1,
    fallback_hook: tuple[int, int] | None = None,
) -> ActionPlan:
    """Decide the phase, locate the hook, lead the target, and plan one swipe.

    Pure orchestration over the helpers above — the single entry point the live
    driver and the dry-run overlay call each tick. ``prev_detections`` + ``dt_s``
    enable velocity tracking; ``lead_s`` is how far ahead (seconds) to aim,
    typically the frame age plus the swipe-execution budget.
    """
    det = _detect_hook_state(frame)
    real_hook = _hook_center(det)
    protected = det.protected if det is not None else None
    frame_h = frame.shape[0] if frame is not None else _H
    # Direction only from a real detection — never infer it from the fallback.
    hook_direction = hook_zone_direction(real_hook[1] if real_hook else None, frame_h)
    hook = real_hook if real_hook is not None else (fallback_hook or (_W // 2, int(0.15 * _H)))

    trend = level_trend(levels, min_delta=min_delta)
    level = levels[-1] if levels else None
    # Direction → phase: hook position (top=down / bottom=up) > shield > altitude.
    phase = resolve_phase(
        levels,
        protected=bool(protected),
        hook_direction=hook_direction,
        min_delta=min_delta,
    )

    tracked = (
        track_fish(prev_detections or [], detections, dt_s=dt_s, lead_s=lead_s)
        if detections
        else []
    )

    target_index = -1
    target_lead: tuple[int, int] | None = None
    swipe: SwipePlan | None = None
    if tracked:
        leads = [(t["lead_x"], t["lead_y"]) for t in tracked]
        target_index = _nearest_index(leads, hook)
        if target_index >= 0:
            target_lead = leads[target_index]
            swipe = plan_swipe(hook, target_lead[0], phase, target_index=target_index)

    return ActionPlan(
        phase=phase,
        level_trend=trend,
        level=level,
        level_total=None,
        hook_x=hook[0],
        hook_y=hook[1],
        protected=protected,
        hook_direction=hook_direction,
        target_index=target_index,
        target_lead_x=target_lead[0] if target_lead else None,
        target_lead_y=target_lead[1] if target_lead else None,
        lead_s=round(lead_s, 3),
        swipe=swipe,
        tracked=tracked,
        detections=len(detections),
    )
