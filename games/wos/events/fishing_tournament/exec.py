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
from typing import TYPE_CHECKING, Any, TypedDict

import cv2

from api.services.fish_engine import (
    _HOOK_STEER_SPEED_PX_S,
    parse_level,
    plan_action,
)

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
# Haul (post-round) "to fishing tournament" button → back to the hub.
_HAUL_EXIT_TAP = (0.300, 0.876)     # fishing_tournament_haul.to.fishing_tournament centre

# Round-start entry buttons, located by template so the FSM taps whichever the
# hub is showing — "Go Fish" (resume state) or "Ice Fishing" (choose state) — in
# priority order. play.frosty ("Frosty Prospector") is PAID, so it is omitted:
# the FSM only ever starts FREE rounds.
_CROP_DIR = Path(__file__).resolve().parent / "references" / "crop"
_ENTRY_BUTTONS = (
    ("fishing_tournament.go_fish", "main_ready_fishing_tournament.go_fish.png"),
    ("fishing_tournament.play.free", "main_ready_fishing_tournament.play.free.png"),
)
_ENTRY_MATCH_THR = 0.72  # tolerant of scrcpy-frame variance (adb match ~0.90)

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
# Swipe zone doesn't matter to the minigame (it reads the horizontal flick
# anywhere), so flick across the SCREEN CENTRE — a guaranteed-safe play area,
# away from the top HUD where a hook-row swipe might land on a control.
_SWIPE_Y_FRAC = 0.5
_LEVEL_HISTORY = 40         # readings kept for the phase-direction trend
_LEAD_CAP_S = 1.0           # cap the interception horizon (inference-bound loop)
_TICK_SLEEP_S = 0.02        # tiny breather between gameplay ticks
# Re-run the expensive full detect_screen only every Nth gameplay tick; assume
# we're still playing between (catches the exit to haul/hub within N ticks).
_SCREEN_RECHECK = 4
# On the re-check tick, first try a CHEAP single-template gameplay confirm before
# falling back to the full ~100-rule detect_screen — so a continuing round never
# pays the full scan.
_GAMEPLAY_TITLE_CROP = "gameplay_fishing_tournament.gameplay.title.png"
_GAMEPLAY_FAST_THR = 0.80
# The altitude counter is now the PRIMARY phase signal (набор высоты → collect),
# so OCR it every tick to catch the start of an ascent promptly. (Was every 3rd
# for FPS, but missing the ascent costs far more than a few ms of Tesseract.)
_LEVEL_OCR_EVERY = 1
# EMA factor for the fish velocity vector (1.0 = raw, no smoothing). <1 blends
# the new inter-frame estimate with the prior so the lead doesn't jitter.
_VEL_EMA_ALPHA = 0.5

# Debug telemetry — only written when the exec step passes `debug: true`.
_TEMPORAL_DIR = Path(__file__).resolve().parents[4] / "temporal"
_DEBUG_FRAME_EVERY = 4      # save every Nth annotated gameplay frame for review


class _SwipeExecution(TypedDict):
    """What the executor actually sent to the device for one planned swipe."""

    ok: bool
    raw_dx: int
    executed_dx: int
    start_x: int
    end_x: int
    y: int
    duration_ms: int


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


_gameplay_title_tpl: Any = None
_gameplay_title_loaded = False


def _is_gameplay_fast(frame: Any) -> bool:
    """Cheap gameplay confirm: match the tiny top-left gameplay-title crop in the
    top-left corner, instead of the full ~100-rule detect_screen. Lets a
    continuing round skip the expensive scan."""
    global _gameplay_title_tpl, _gameplay_title_loaded
    if not _gameplay_title_loaded:
        _gameplay_title_tpl = cv2.imread(str(_CROP_DIR / _GAMEPLAY_TITLE_CROP))
        _gameplay_title_loaded = True
    tpl = _gameplay_title_tpl
    if frame is None or tpl is None or getattr(frame, "size", 0) == 0:
        return False
    fh, fw = frame.shape[:2]
    th, tw = tpl.shape[:2]
    region = frame[: int(0.08 * fh), : int(0.16 * fw)]
    if region.shape[0] < th or region.shape[1] < tw:
        return False
    res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
    return float(res.max()) >= _GAMEPLAY_FAST_THR


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


def _draw_decision(frame: Any, rows: list[Any], plan: dict[str, Any]) -> Any:
    """Annotate a gameplay frame with the decision (fish boxes + hook + swipe),
    reusing the /fish-detect overlay colours, for debug review."""
    from api.services.fish_common import draw_detections

    out = draw_detections(frame.copy(), rows)
    hx, hy = plan.get("hook_x"), plan.get("hook_y")
    if hx is not None and hy is not None:
        col = (0, 230, 120) if plan.get("phase") == "collect" else (80, 80, 255)
        cv2.drawMarker(out, (int(hx), int(hy)), col, cv2.MARKER_CROSS, 28, 2)
    sw = plan.get("swipe")
    if sw:
        cv2.arrowedLine(
            out, (int(sw["from_x"]), int(sw["from_y"])),
            (int(sw["to_x"]), int(sw["to_y"])), (0, 230, 120), 3, tipLength=0.3,
        )
    cv2.putText(
        out, f"{plan.get('phase')} lvl={plan.get('level')} dir={plan.get('hook_direction')}",
        (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2,
    )
    return out


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


def _swipe_motion(swipe: dict[str, Any]) -> dict[str, int]:
    """Translate a planned hook dx into the real centre-screen flick geometry."""
    raw_dx = int(swipe["to_x"]) - int(swipe["from_x"])
    sign = 1 if raw_dx >= 0 else -1
    mag = max(_SWIPE_MIN_PX, abs(raw_dx) * _SWIPE_GAIN)
    half = int(mag / 2)
    cx, cy = _W // 2, int(_H * _SWIPE_Y_FRAC)
    start_x = min(_W - 1, max(0, cx - sign * half))
    end_x = min(_W - 1, max(0, cx + sign * half))
    return {
        "raw_dx": raw_dx,
        "executed_dx": end_x - start_x,
        "start_x": start_x,
        "end_x": end_x,
        "y": cy,
        "duration_ms": _SWIPE_MS,
    }


async def _swipe(actions: Any, instance_id: str, swipe: dict[str, Any]) -> _SwipeExecution:
    import asyncio

    from layout.types import Point

    # Direction + magnitude come from the plan; execute it as a horizontal flick
    # centred on the screen (zone-agnostic, away from the top HUD). Amplify and
    # floor the travel, centre it around the mid-x so it stays on-frame.
    motion = _swipe_motion(swipe)
    start = Point(motion["start_x"], motion["y"])
    end = Point(motion["end_x"], motion["y"])
    ok = False
    try:
        # min_duration_ms bypasses the controller's ~900 ms human-scroll floor —
        # the minigame needs a fast flick, not a slow drag (else the hook barely
        # moves). Return the real result so we count swipes that ACTUALLY ran.
        ok = bool(await asyncio.to_thread(
            lambda: actions.swipe(
                instance_id, start, end,
                duration_ms=_SWIPE_MS, min_duration_ms=_SWIPE_MS, settle_ms=0,
            )
        ))
    except Exception:
        logger.debug("fishing FSM: swipe failed", exc_info=True)
    return _SwipeExecution(ok=ok, **motion)


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

    debug = bool(ctx.args.get("debug"))
    ticks: list[dict[str, Any]] = []  # per-gameplay-tick telemetry for tuning
    screen_seq: list[str] = []  # every distinct screen the FSM walked (diagnosis)
    frame_none = 0              # iterations where capture returned no frame
    levels: list[int] = []
    last_level: int | None = None
    prev_rows: list[Any] | None = None
    prev_tracked: list[Any] | None = None  # last tick's TrackedFish (EMA velocity)
    est_hook: tuple[int, int] | None = None  # last-known hook, carried by swipes
    prev_cap: float | None = None   # capture time of the previous gameplay frame
    prev_sig: Any = None
    last_screen = ""
    gp_assumed = 0                  # consecutive ticks we skipped the full detect
    gp_tick = 0                     # gameplay ticks (for the level-OCR cadence)
    swipes = plays = 0
    nongame = 0
    game_stuck = 0
    gameplay_seen = False
    t0 = time.monotonic()

    for _step in range(_MAX_STEPS):
        if time.monotonic() - t0 > _MAX_RUN_S:
            break

        frame = await _capture(actions, inst)
        t_cap = time.monotonic()
        if frame is None:
            frame_none += 1
            if not screen_seq or screen_seq[-1] != "no-frame":
                screen_seq.append("no-frame")
            await asyncio.sleep(0.5)
            continue

        # Responsiveness: the full detect_screen (~1 s) dominates the loop. While
        # in gameplay, skip it for a few ticks (assume we're still playing); on
        # the re-check tick try a CHEAP gameplay-title match first, and only fall
        # back to the full detect_screen when that fails (round ended → haul/hub).
        if last_screen == "gameplay" and gp_assumed < _SCREEN_RECHECK:
            screen = "gameplay"
            gp_assumed += 1
        elif last_screen == "gameplay" and _is_gameplay_fast(frame):
            screen = "gameplay"
            gp_assumed = 0
        else:
            screen = await _screen(screen_detector, frame)
            gp_assumed = 0
        last_screen = screen
        if not screen_seq or screen_seq[-1] != screen:
            screen_seq.append(screen)  # log distinct-screen transitions for diagnosis

        # Left the event entirely → done.
        if screen == "main_city":
            break

        # Pause modal overlaying the round → resume it.
        if screen == "pause":
            await _tap(actions, inst, _CONTINUE_TAP, "fishing_tournament.continue")
            nongame = 0
            await asyncio.sleep(1.0)
            continue

        # Post-round Haul summary → the round ended. Return to the hub via its
        # "to fishing tournament" button and stop (one round per exec run).
        if screen == "haul":
            await _tap(
                actions, inst, _HAUL_EXIT_TAP,
                "fishing_tournament_haul.to.fishing_tournament",
            )
            await asyncio.sleep(1.5)
            break

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

            detect_t0 = time.monotonic()
            try:
                dets = await fish_detector.detect(frame, threshold=conf)
            except InferenceUnavailableError:
                ctx.result["error"] = "inference unavailable mid-round"
                break
            except Exception:
                logger.debug("fishing FSM: detection failed", exc_info=True)
                dets = []
            detect_ms = (time.monotonic() - detect_t0) * 1000.0
            rows = detections_to_rows(dets)

            # Altitude moves slowly → OCR it (slow Tesseract) only every Nth tick;
            # reuse the last reading between to keep the loop fast.
            gp_tick += 1
            ocr_ms: float | None = None
            level_fresh = False
            if gp_tick % _LEVEL_OCR_EVERY == 1:
                ocr_t0 = time.monotonic()
                fresh = await _read_level(ocr, frame)
                ocr_ms = (time.monotonic() - ocr_t0) * 1000.0
                if fresh is not None:
                    last_level = fresh
                    levels.append(fresh)
                    if len(levels) > _LEVEL_HISTORY:
                        del levels[0]
                    level_fresh = True
            level = last_level

            now = time.monotonic()
            # dt between successive gameplay FRAMES (captures) → fish velocity.
            dt_s = (t_cap - prev_cap) if prev_cap is not None else None
            # Interception horizon: time from THIS frame's capture until the
            # swipe lands = decision latency so far (capture→now, mostly
            # inference) + the flick duration. This is what makes the hook meet
            # the fish on its body instead of clipping the trailing tail.
            base_latency_s = min(_LEAD_CAP_S, (now - t_cap) + _SWIPE_MS / 1000.0)
            plan_t0 = time.monotonic()
            plan = plan_action(
                frame, rows, levels,
                prev_detections=prev_rows, dt_s=dt_s,
                lead_s=base_latency_s,
                base_latency_s=base_latency_s,
                hook_speed_px_s=_HOOK_STEER_SPEED_PX_S,
                prev_tracked=prev_tracked,
                vel_ema_alpha=_VEL_EMA_ALPHA,
                fallback_hook=est_hook,  # better steer origin when the ring is lost
            )
            plan_ms = (time.monotonic() - plan_t0) * 1000.0
            prev_rows = rows
            prev_tracked = plan["tracked"]
            prev_cap = t_cap

            swipe = plan["swipe"]
            swiped = False
            swipe_exec: _SwipeExecution | None = None
            swipe_ms: float | None = None
            if swipe is not None:
                swipe_t0 = time.monotonic()
                swipe_exec = await _swipe(actions, inst, swipe)
                swipe_ms = (time.monotonic() - swipe_t0) * 1000.0
                swiped = swipe_exec["ok"]
                if swiped:
                    swipes += 1

            # Hook-position robustness: snap the estimate to a real detection,
            # else carry it forward by the swipe we just issued (so the next
            # tick steers from where the hook actually is, not a fixed point).
            if plan["hook_detected"] and plan["hook_x"] is not None:
                est_hook = (plan["hook_x"], plan["hook_y"])
            elif est_hook is not None and swiped and swipe_exec is not None:
                est_hook = (
                    int(min(_W - 1, max(0, est_hook[0] + swipe_exec["executed_dx"]))),
                    est_hook[1],
                )

            # Per-tick telemetry — the tuning signal (phase correctness, swipe
            # steering, whether the swipe ACTUALLY ran, altitude/level progress).
            ticks.append({
                "t": round(now - t0, 2),
                "level": level,
                "level_fresh": level_fresh,
                "trend": plan["level_trend"],
                "phase": plan["phase"],
                "hook_y_frac": (round(plan["hook_y"] / _H, 3)
                                if plan["hook_y"] is not None else None),
                "hook_dir": plan["hook_direction"],
                "hook_det": plan["hook_detected"],
                "hook_source": plan["hook_source"],
                "protected": plan["protected"],
                "n_dets": plan["detections"],
                "dt_s": round(dt_s, 3) if dt_s is not None else None,
                "base_latency_s": round(base_latency_s, 3),
                "detect_ms": round(detect_ms, 1),
                "ocr_ms": round(ocr_ms, 1) if ocr_ms is not None else None,
                "plan_ms": round(plan_ms, 1),
                "swipe_ms": round(swipe_ms, 1) if swipe_ms is not None else None,
                "swipe_dir": (swipe["direction"] if swipe else None),
                "swipe_dx": (swipe["dx"] if swipe else None),
                "executed_dx": (
                    swipe_exec["executed_dx"] if swipe_exec is not None else None
                ),
                "swiped": swiped,
            })
            if debug and len(ticks) % _DEBUG_FRAME_EVERY == 1:
                try:
                    _TEMPORAL_DIR.mkdir(parents=True, exist_ok=True)
                    annotated = _draw_decision(frame, rows, plan)
                    cv2.imwrite(
                        str(_TEMPORAL_DIR / f"fishing_tick_{len(ticks):04d}.png"),
                        annotated,
                    )
                except Exception:
                    logger.debug("fishing FSM: debug frame save failed", exc_info=True)

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

    seen_levels = [t["level"] for t in ticks if t["level"] is not None]

    def _avg_tick(key: str) -> float | None:
        vals = [t[key] for t in ticks if isinstance(t.get(key), (int, float))]
        return round(sum(vals) / len(vals), 1) if vals else None

    def _avg_base_latency_ms() -> float | None:
        vals = [
            t["base_latency_s"]
            for t in ticks
            if isinstance(t.get("base_latency_s"), (int, float))
        ]
        return round(1000.0 * sum(vals) / len(vals), 1) if vals else None

    ctx.result.update(
        {
            "action": "drive_fishing",
            "plays": plays,
            "swipes": swipes,
            "gameplay_seen": gameplay_seen,
            "level_last": levels[-1] if levels else None,
            # Tuning telemetry — the dodge/collect quality signal per round.
            "ticks": len(ticks),
            "phase_dodge": sum(1 for t in ticks if t["phase"] == "dodge"),
            "phase_collect": sum(1 for t in ticks if t["phase"] == "collect"),
            "swipe_left": sum(1 for t in ticks if t["swipe_dir"] == "left"),
            "swipe_right": sum(1 for t in ticks if t["swipe_dir"] == "right"),
            # Why the phase split looks the way it does (diagnoses dodge-bias):
            # how often the ring was found, where it read, and shield-up rate.
            "hook_detected_pct": (round(100 * sum(1 for t in ticks if t["hook_det"])
                                        / len(ticks)) if ticks else None),
            "hook_source_ring": sum(1 for t in ticks if t["hook_source"] == "ring"),
            "hook_source_green": sum(
                1 for t in ticks if t["hook_source"] == "green_node"
            ),
            "hook_source_line": sum(
                1 for t in ticks if t["hook_source"] == "line"
            ),
            "hook_source_fallback": sum(
                1 for t in ticks if t["hook_source"] == "fallback"
            ),
            "hook_source_default": sum(
                1 for t in ticks if t["hook_source"] == "default"
            ),
            "hook_dir_down": sum(1 for t in ticks if t["hook_dir"] == "down"),
            "hook_dir_up": sum(1 for t in ticks if t["hook_dir"] == "up"),
            "hook_dir_none": sum(1 for t in ticks if t["hook_dir"] is None),
            "protected_pct": (round(100 * sum(1 for t in ticks if t["protected"])
                                    / len(ticks)) if ticks else None),
            "trend_up_ever": any(t["trend"] == "up" for t in ticks),
            "level_min": min(seen_levels) if seen_levels else None,
            "level_max": max(seen_levels) if seen_levels else None,
            "mean_dets": (round(sum(t["n_dets"] for t in ticks) / len(ticks), 1)
                          if ticks else 0),
            "avg_detect_ms": _avg_tick("detect_ms"),
            "avg_ocr_ms": _avg_tick("ocr_ms"),
            "avg_plan_ms": _avg_tick("plan_ms"),
            "avg_swipe_ms": _avg_tick("swipe_ms"),
            "avg_base_latency_ms": _avg_base_latency_ms(),
            # Effective gameplay frame rate (ticks/s) — the responsiveness metric.
            "fps": (round((len(ticks) - 1) / (ticks[-1]["t"] - ticks[0]["t"]), 1)
                    if len(ticks) > 1 and ticks[-1]["t"] > ticks[0]["t"] else None),
            # Diagnosis: the exact screen path + capture failures this run.
            "screen_seq": " → ".join(screen_seq[-40:]),
            "frame_none": frame_none,
        }
    )
    if debug and ticks:
        try:
            import json

            _TEMPORAL_DIR.mkdir(parents=True, exist_ok=True)
            (_TEMPORAL_DIR / "fishing_ticks.jsonl").write_text(
                "\n".join(json.dumps(t) for t in ticks), encoding="utf-8"
            )
        except Exception:
            logger.debug("fishing FSM: ticks dump failed", exc_info=True)
    logger.info(
        "fishing FSM: plays=%d swipes=%d gameplay_seen=%s ticks=%d "
        "dodge/collect=%d/%d level_max=%s instance=%s",
        plays, swipes, gameplay_seen, len(ticks),
        ctx.result["phase_dodge"], ctx.result["phase_collect"],
        ctx.result["level_max"], inst,
    )
    # Release the reused inference HTTP client.
    try:
        await fish_detector.aclose()
    except Exception:
        logger.debug("fishing FSM: detector aclose failed", exc_info=True)


DSL_EXEC_HANDLERS = {
    "drive_fishing": _exec_drive_fishing,
}
