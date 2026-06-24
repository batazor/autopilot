"""Python FSM that auto-plays the Fishing Tournament "Trial Stages" minigame.

The minigame (``gameplay`` screen) is a steer-the-hook game: a glowing cyan ring
(the hook/bait) hangs near the top and the player **swipes horizontally** to move
it across a lane of swimming fish — dodge the fish while descending, collect them
while the altitude counter climbs ("набор высоты"). The *decision* is pure and
already implemented in :func:`api.services.fish_engine.plan_action` (it also
drives the ``/fish-detect`` dry-run overlay). This handler is its missing
on-device **executor**: each tick it captures a fresh frame, runs the fish
detector + hook locator + level OCR, asks ``plan_action`` for a swipe, and turns
the returned :class:`~api.services.fish_engine.SwipePlan` into an ADB swipe.

Self-contained FSM (no screen-graph dependency), modelled on
``tundra_trek.exec`` so a stray landing can never strand the bot:

* **main_ready** (live hub) → tap a play button to start a round,
* **gameplay** → swipe the hook per ``plan_action``,
* **pause** modal → Continue to resume the round,
* left to **main_city** / the **event splash**, or stuck → stop on a recognised
  screen so the scenario's claim steps + ``check_main_city`` take over.

Swipes go through ``BotActions.swipe`` (which, unlike ``tap``, does not gate on
click-approval — a minigame can't pause for per-swipe approval; run it with
approvals off, as the Fishing-control Play button does).

Reached via ``- exec: drive_fishing`` from ``scenarios/event.fishing_tournament.yaml``
after the scenario navigates to ``node: event.fishing_tournament``.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2

from api.services.fish_engine import parse_level, plan_action

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

# Emulator's mandatory 720x1280 portrait framebuffer.
_W, _H = 720, 1280

# Altitude counter region (top-left "N/100"), as (x, y, w, h) frame fractions —
# from games/wos/events/fishing_tournament/area.yaml ``fishing_tournament.level``.
_LEVEL_WIN = (0.0840, 0.0974, 0.150, 0.0302)

# Pause modal's Continue button (fixed centre — rare path).
_CONTINUE_TAP = (0.645, 0.501)      # fishing_tournament.continue centre

# Round-start entry buttons, located by template so the FSM taps whichever the
# hub is showing — "Go Fish" (resume state) or "Ice Fishing" (choose state) — in
# priority order. play.frosty ("Frosty Prospector") is PAID, so it is omitted:
# the FSM only ever starts FREE rounds.
_CROP_DIR = Path(__file__).resolve().parent / "references" / "crop"
_ENTRY_BUTTONS = (
    ("fishing_tournament.go_fish", "main_ready_fishing_tournament.go_fish.png"),
    ("fishing_tournament.play.free", "main_ready_fishing_tournament.play.free.png"),
)
_ENTRY_MATCH_THR = 0.80

# Run budget. A round is ~tens of seconds of swiping; inference latency (not the
# 100 ms capture cadence) sets the real tick rate, so cap by wall-clock too.
_MAX_STEPS = 400
_MAX_RUN_S = 180.0
_MAX_PLAYS = 1                # rounds started per exec run (overlay re-triggers)
_MAX_NONGAME = 6             # consecutive off-gameplay/unknown frames → give up
_MAX_GAME_STUCK = 12        # consecutive *frozen* gameplay frames → round ended
_FRAME_DIFF = 4.0            # mean abs gray diff above which "the screen moved"
_MAX_EXIT_BACKS = 6
_KNOWN_EXIT = ("main_city", "event.fishing_tournament", "main_ready")

# Swipe strength. The in-game hook moves by a *throw*, not a 1:1 drag, so a soft
# gesture barely nudges it. A sharp, over-travelled flick lands real movement:
# shorter duration = harder throw; _SWIPE_GAIN over-shoots the planned offset
# (the closed loop re-aims next tick, so overshoot self-corrects); _SWIPE_MIN_PX
# floors every swipe so even small corrections are decisive.
_SWIPE_MS = 90               # sharp flick (was 140 — too soft)
_SWIPE_GAIN = 1.8            # execute this × the planned hook offset
_SWIPE_MIN_PX = 120         # never weaker than this, in the swipe direction
_LEVEL_HISTORY = 40         # readings kept for the phase-direction trend
_LEAD_CAP_S = 0.4           # never extrapolate fish further than this ahead
_TICK_SLEEP_S = 0.05        # tiny breather between gameplay ticks


def _crop(frame: Any, win: tuple[float, float, float, float]) -> Any:
    x, y, w, h = win
    fh, fw = frame.shape[:2]
    return frame[int(y * fh):int((y + h) * fh), int(x * fw):int((x + w) * fw)]


def _frame_sig(frame: Any) -> Any:
    """Cheap grayscale signature for frame-to-frame change detection."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (80, 45), interpolation=cv2.INTER_AREA)


_entry_templates: list[tuple[str, Any]] | None = None


def _entry_button_templates() -> list[tuple[str, Any]]:
    """Lazily load + cache the free round-start button crops."""
    global _entry_templates
    if _entry_templates is None:
        _entry_templates = []
        for label, fname in _ENTRY_BUTTONS:
            tpl = cv2.imread(str(_CROP_DIR / fname))
            if tpl is not None:
                _entry_templates.append((label, tpl))
    return _entry_templates


def _find_entry_button(frame: Any) -> tuple[str, tuple[float, float]] | None:
    """Locate the FREE round-start button the hub is showing → (label, (cx, cy)
    frame fractions), or None. Template-matches the candidates in priority order
    so it works in BOTH hub states (resume "Go Fish" / choose-mode "Ice Fishing")
    — the live icon lands on either depending on whether a stage is in progress.
    """
    if frame is None or getattr(frame, "size", 0) == 0:
        return None
    fh, fw = frame.shape[:2]
    for label, tpl in _entry_button_templates():
        th, tw = tpl.shape[:2]
        if th > fh or tw > fw:
            continue
        res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
        _min, score, _minloc, loc = cv2.minMaxLoc(res)
        if score >= _ENTRY_MATCH_THR:
            return label, ((loc[0] + tw / 2) / fw, (loc[1] + th / 2) / fh)
    return None


def _moved(prev: Any, cur: Any) -> bool:
    if prev is None or cur is None:
        return True
    return float(cv2.absdiff(prev, cur).mean()) > _FRAME_DIFF


async def _capture(actions: Any, instance_id: str) -> Any:
    import asyncio

    try:
        return await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
    except Exception:
        logger.debug("fishing FSM: capture failed", exc_info=True)
        return None


async def _screen(detector: Any, frame: Any) -> str:
    try:
        return str(await detector.detect_screen(frame))
    except Exception:
        return "unknown"


async def _read_level(ocr: Any, frame: Any) -> int | None:
    """OCR the altitude counter ('N/100' → N), or None when it doesn't parse."""
    import asyncio

    crop = _crop(frame, _LEVEL_WIN)
    if crop is None or crop.size == 0:
        return None
    try:
        text, _conf = await asyncio.to_thread(ocr._run_tesseract, crop)
    except Exception:
        return None
    parsed = parse_level(text)
    return parsed[0] if parsed else None


async def _tap(actions: Any, instance_id: str, frac: tuple[float, float], label: str) -> None:
    import asyncio

    from layout.types import Point

    x, y = frac
    pt = Point(int(x * _W), int(y * _H))
    try:
        await asyncio.to_thread(actions.tap, instance_id, pt, approval_region=label)
    except Exception:
        logger.debug("fishing FSM: tap %s failed", label, exc_info=True)


async def _swipe(actions: Any, instance_id: str, swipe: dict[str, Any]) -> None:
    import asyncio

    from layout.types import Point

    fx, fy = int(swipe["from_x"]), int(swipe["from_y"])
    # Amplify the planned offset into a harder throw, floored so even small
    # corrections move the hook, and keep it on-frame. Direction is preserved.
    raw_dx = int(swipe["to_x"]) - fx
    sign = 1 if raw_dx >= 0 else -1
    mag = max(_SWIPE_MIN_PX, abs(raw_dx) * _SWIPE_GAIN)
    end_x = min(_W - 1, max(0, int(fx + sign * mag)))
    start = Point(fx, fy)
    end = Point(end_x, fy)
    try:
        await asyncio.to_thread(
            actions.swipe, instance_id, start, end, duration_ms=_SWIPE_MS
        )
    except Exception:
        logger.debug("fishing FSM: swipe failed", exc_info=True)


async def _back(actions: Any, instance_id: str) -> None:
    import asyncio

    try:
        await asyncio.to_thread(actions.system_back, instance_id)
    except Exception:
        logger.debug("fishing FSM: system_back failed", exc_info=True)


async def _exec_drive_fishing(ctx: DslExecContext) -> None:
    """Play the Fishing Tournament minigame, executing the dodge/collect swipes,
    then stop on a recognised screen so the scenario can claim + navigate home."""
    import asyncio

    from api.services.fish_common import detections_to_rows
    from config.loader import load_settings
    from inference.roboflow_client import InferenceUnavailableError, RoboflowDetector
    from navigation.detector import ScreenDetector
    from tasks import dsl_runtime

    actions = dsl_runtime.bot_actions()
    ocr = dsl_runtime.ocr_client()
    screen_detector = ScreenDetector(ocr)
    inst = ctx.instance_id

    cfg = load_settings().inference
    fish_detector = RoboflowDetector.from_settings(cfg)
    threshold = ctx.args.get("threshold")
    conf = float(threshold) if threshold is not None else cfg.confidence

    if not fish_detector.available():
        ctx.result.update(
            {"action": "drive_fishing", "error": "inference not configured"}
        )
        logger.warning(
            "fishing FSM: inference service not configured — cannot drive (%s)", inst
        )
        return

    levels: list[int] = []
    prev_rows: list[Any] | None = None
    prev_t: float | None = None
    prev_sig: Any = None
    swipes = plays = 0
    nongame = 0
    game_stuck = 0
    gameplay_seen = False
    t0 = time.monotonic()

    for _step in range(_MAX_STEPS):
        if time.monotonic() - t0 > _MAX_RUN_S:
            break

        frame = await _capture(actions, inst)
        if frame is None:
            await asyncio.sleep(0.5)
            continue

        screen = await _screen(screen_detector, frame)

        # Left the event entirely → done.
        if screen == "main_city":
            break

        # Pause modal overlaying the round → resume it.
        if screen == "pause":
            await _tap(actions, inst, _CONTINUE_TAP, "fishing_tournament.continue")
            nongame = 0
            await asyncio.sleep(1.0)
            continue

        # Live hub → start a round (one per exec run; the overlay re-triggers).
        # Tap whichever FREE entry button the hub shows — "Go Fish" (resume) or
        # "Ice Fishing" (choose-mode) — located by template so either state works.
        if screen == "main_ready":
            if gameplay_seen or plays >= _MAX_PLAYS:
                break  # already played this run, or hit the per-run cap
            entry = _find_entry_button(frame)
            if entry is None:
                # No free entry visible (e.g. only the paid Frosty Prospector).
                nongame += 1
                if nongame >= _MAX_NONGAME:
                    break
                await asyncio.sleep(1.0)
                continue
            label, frac = entry
            await _tap(actions, inst, frac, label)
            plays += 1
            nongame = 0
            await asyncio.sleep(2.5)
            continue

        # The minigame: decide + swipe on a fresh frame.
        if screen == "gameplay":
            gameplay_seen = True
            nongame = 0
            sig = _frame_sig(frame)
            if not _moved(prev_sig, sig):
                game_stuck += 1
                if game_stuck >= _MAX_GAME_STUCK:
                    break  # frozen gameplay → round most likely finished
            else:
                game_stuck = 0
            prev_sig = sig

            try:
                dets = await fish_detector.detect(frame, threshold=conf)
            except InferenceUnavailableError:
                ctx.result["error"] = "inference unavailable mid-round"
                break
            except Exception:
                logger.debug("fishing FSM: detection failed", exc_info=True)
                dets = []
            rows = detections_to_rows(dets)

            level = await _read_level(ocr, frame)
            if level is not None:
                levels.append(level)
                if len(levels) > _LEVEL_HISTORY:
                    del levels[0]

            now = time.monotonic()
            dt_s = (now - prev_t) if prev_t is not None else None
            lead_s = min(_LEAD_CAP_S, dt_s or 0.15)
            plan = plan_action(
                frame, rows, levels,
                prev_detections=prev_rows, dt_s=dt_s, lead_s=lead_s,
            )
            prev_rows = rows
            prev_t = now

            swipe = plan["swipe"]
            if swipe is not None:
                await _swipe(actions, inst, swipe)
                swipes += 1
            await asyncio.sleep(_TICK_SLEEP_S)
            continue

        # Event splash / unknown — can't drive from here. Bounded patience, then
        # escape so we never spin the whole budget on a screen we can't act on.
        nongame += 1
        if nongame >= _MAX_NONGAME:
            break
        await asyncio.sleep(1.0)

    # Escape to a recognised screen so the framework can navigate home. Never
    # back out while already on main_city (that opens the quit dialog).
    for _ in range(_MAX_EXIT_BACKS):
        frame = await _capture(actions, inst)
        if frame is not None and await _screen(screen_detector, frame) in _KNOWN_EXIT:
            break
        await _back(actions, inst)
        await asyncio.sleep(1.5)

    ctx.result.update(
        {
            "action": "drive_fishing",
            "plays": plays,
            "swipes": swipes,
            "gameplay_seen": gameplay_seen,
            "level_last": levels[-1] if levels else None,
        }
    )
    logger.info(
        "fishing FSM: plays=%d swipes=%d gameplay_seen=%s level_last=%s instance=%s",
        plays, swipes, gameplay_seen, levels[-1] if levels else None, inst,
    )


DSL_EXEC_HANDLERS = {
    "drive_fishing": _exec_drive_fishing,
}
