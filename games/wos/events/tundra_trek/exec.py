"""Python FSM that auto-plays the Tundra Trek travel mini-game.

Tundra Trek is a multi-state mini-game, not a simple claim screen. Verified
on-device (2026-06-21) it cycles through, in any order:

* **story stops / dialogue** — a blue "Continue Game" button (bottom-centre),
* **drive to next stop** — an *orange* "Next Stop" button (bottom-centre),
* **tactical battles** — a blue "Fight" button that auto-resolves into a
  Victory/Defeat result screen (``arena.result``, "tap anywhere to exit"),
* **game stalls** — a blue "Start" button (sits HIGHER, ~y0.65) that opens a
  shuffle mini-game; you tap a chest to claim a random reward,
* **milestone cutscenes** — a "Skip" button (bottom-right).

The in-game Idle/Auto autoplay toggles are LOCKED on this account, so the bot
drives by hand. A declarative YAML loop can't: the advance button changes label
AND vertical position AND colour per state, and the detector can't classify the
sub-screens. So this handler drives it imperatively, grounded on two robust,
position-independent signals instead of brittle fixed templates:

* **the advance button is the one saturated orange/blue pill in the lower
  screen** — find it by colour, tap its centroid; orange ⇒ a drive (costs fuel),
  blue ⇒ fight/continue/start. Label OCR on the pill is unreliable (white text),
  so colour alone tunes the post-tap wait and any battle result is dismissed on
  the next loop.
* **anything else** — skip story text / clear tap-anywhere popups with a BOTTOM
  tap (escalating to a centre tap for a chest mini-game's mid-screen target); a
  cutscene clears with Skip; the hub clears by tapping the signpost.

It **escapes any state it can't act on by backing out** to a recognised screen,
so it can never strand the bot (the failure the YAML version kept hitting). The
scenario then navigates home via ``check_main_city``.

Reached via ``- exec: drive_tundra_trek`` from ``scenarios/event.tundra_trek.yaml``
after the scenario navigates to ``node: event.tundra_trek``.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

import cv2

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

# Frames come back at the emulator's mandatory 720x1280.
_W, _H = 720, 1280

# Advance-button colour gates (OpenCV HSV). The trek's snow/sky is blue but
# desaturated (gated out by the sat floor) and its wood is dark (gated out by the
# value floor); only the saturated button pills survive. Validated on the
# Continue/Start/Fight (blue) and Next Stop (orange) frames.
_BLUE = ((90, 110, 140), (128, 255, 255))
_ORANGE = ((5, 120, 150), (28, 255, 255))
_BTN_TOP_FRAC = 0.45          # ignore buttons above this (sky / hud)
_BTN_MIN_W = 180              # a real action pill is wide…
_BTN_MIN_H, _BTN_MAX_H = 45, 135
_BTN_WIDE = 2.0              # …and clearly wider than tall
_BTN_CX_LO, _BTN_CX_HI = 0.28, 0.72  # and roughly centred (Idle/Auto sit right)

# Fixed windows / tap targets as (x, y[, w, h]) fractions of the frame.
_FUEL_WIN = (0.70, 0.010, 0.13, 0.040)   # "105/100" backpack counter (diagnostics)
_SKIP_WIN = (0.72, 0.905, 0.26, 0.060)   # bottom-right Skip label
_SKIP_TAP = (0.85, 0.945)
_SIGNPOST_TAP = (0.80, 0.42)             # the trek hub signpost ("set off")
_DISMISS_TAP = (0.50, 0.82)              # "tap anywhere to exit" (battle result)
_TEXT_SKIP_TAP = (0.50, 0.82)            # bottom tap: skip story text / clear popups
_CENTER_TAP = (0.50, 0.46)               # chest mini-game pick (mid-screen target)

_MAX_STEPS = 40        # actions per run; the overlay re-triggers to keep playing
_MAX_STUCK = 3         # consecutive *unchanged* fallback frames before giving up
_MAX_BTN_STUCK = 3     # consecutive button taps that don't move the screen before
                       # giving up (out of fuel, or the tap isn't landing) — without
                       # this an unadvancing Next Stop spins until the task timeout
_FRAME_DIFF = 5.0      # mean abs diff (0-255, 80x45 gray) above which "screen moved"
_MAX_SIGNPOST = 2      # hub taps before concluding the trek is out of fuel
_MAX_EXIT_BACKS = 8    # system-backs allowed while escaping to a known screen
_KNOWN_EXIT = ("main_city", "event.tundra_trek")


def _crop(frame: Any, win: tuple[float, float, float, float]) -> Any:
    x, y, w, h = win
    fh, fw = frame.shape[:2]
    return frame[int(y * fh):int((y + h) * fh), int(x * fw):int((x + w) * fw)]


def _frame_sig(frame: Any) -> Any:
    """Small grayscale signature for cheap frame-to-frame change detection."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (80, 45), interpolation=cv2.INTER_AREA)


def _moved(prev: Any, cur: Any) -> bool:
    """True if the screen changed meaningfully since the previous frame."""
    if prev is None or cur is None:
        return True
    return float(cv2.absdiff(prev, cur).mean()) > _FRAME_DIFF


def _find_button(frame: Any) -> tuple[float, float, str] | None:
    """Locate the primary action pill by colour. Returns (cx, cy, colour) as
    frame fractions, or None. Colour is 'orange' (drive) or 'blue' (act)."""
    fh, fw = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, _BLUE[0], _BLUE[1])
    orange = cv2.inRange(hsv, _ORANGE[0], _ORANGE[1])
    mask = cv2.bitwise_or(blue, orange)
    mask[: int(_BTN_TOP_FRAC * fh), :] = 0
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        x, y, cw, ch = cv2.boundingRect(c)
        cx = (x + cw / 2) / fw
        if (
            cw > _BTN_MIN_W
            and _BTN_MIN_H < ch < _BTN_MAX_H
            and cw > _BTN_WIDE * ch
            and _BTN_CX_LO < cx < _BTN_CX_HI
            and (best is None or cw * ch > best[0])
        ):
            sub = hsv[y:y + ch, x:x + cw]
            ob = int(cv2.inRange(sub, _ORANGE[0], _ORANGE[1]).sum())
            bl = int(cv2.inRange(sub, _BLUE[0], _BLUE[1]).sum())
            best = (cw * ch, cx, (y + ch / 2) / fh, "orange" if ob > bl else "blue")
    if best is None:
        return None
    return best[1], best[2], best[3]


async def _ocr_lower(ocr: Any, frame: Any, win: tuple[float, float, float, float]) -> str:
    crop = _crop(frame, win)
    if crop is None or crop.size == 0:
        return ""
    try:
        text, _conf = await asyncio.to_thread(ocr._run_tesseract, crop)
    except Exception:
        return ""
    return str(text or "").strip().lower()


async def _read_fuel(ocr: Any, frame: Any) -> int | None:
    """Best-effort read of the top-right fuel counter ('105/100' → 105)."""
    txt = await _ocr_lower(ocr, frame, _FUEL_WIN)
    m = re.search(r"(\d+)\s*/\s*\d+", txt)
    return int(m.group(1)) if m else None


async def _result_text(ocr: Any, frame: Any) -> bool:
    """True when a battle/claim result is showing (backup to the detector)."""
    bottom = await _ocr_lower(ocr, frame, (0.20, 0.86, 0.60, 0.060))
    if any(k in bottom for k in ("tap", "anywhere", "exit", "continue")):
        return True
    top = await _ocr_lower(ocr, frame, (0.20, 0.26, 0.60, 0.090))
    return any(k in top for k in ("victory", "defeat", "reward"))


async def _tap(actions: Any, instance_id: str, frac: tuple[float, float], label: str) -> None:
    from layout.types import Point

    x, y = frac
    pt = Point(int(x * _W), int(y * _H))
    try:
        await asyncio.to_thread(actions.tap, instance_id, pt, approval_region=label)
    except Exception:
        logger.debug("tundra_trek FSM: tap %s failed", label, exc_info=True)


async def _back(actions: Any, instance_id: str) -> None:
    try:
        await asyncio.to_thread(actions.system_back, instance_id)
    except Exception:
        logger.debug("tundra_trek FSM: system_back failed", exc_info=True)


async def _capture(actions: Any, instance_id: str) -> Any:
    try:
        return await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
    except Exception:
        return None


async def _screen(detector: Any, frame: Any) -> str:
    try:
        return str(await detector.detect_screen(frame))
    except Exception:
        return "unknown"


async def _exec_drive_tundra_trek(ctx: DslExecContext) -> None:
    """Drive the Tundra Trek mini-game to its per-run budget, then stop on a
    recognised screen so ``check_main_city`` can navigate home."""
    from navigation.detector import ScreenDetector
    from tasks import dsl_runtime

    actions = dsl_runtime.bot_actions()
    ocr = dsl_runtime.ocr_client()
    detector = ScreenDetector(ocr)
    inst = ctx.instance_id

    advances = dismisses = picks = signposts = 0
    stuck = 0
    btn_stuck = 0
    last_was_button = False
    prev_sig: Any = None
    fuel_start: int | None = None
    fuel_last: int | None = None

    for _step in range(_MAX_STEPS):
        frame = await _capture(actions, inst)
        if frame is None:
            await asyncio.sleep(1.0)
            continue

        screen = await _screen(detector, frame)
        fuel = await _read_fuel(ocr, frame)
        if fuel is not None:
            fuel_start = fuel if fuel_start is None else fuel_start
            fuel_last = fuel
        btn = _find_button(frame)
        sig = _frame_sig(frame)
        moved = _moved(prev_sig, sig)
        prev_sig = sig
        logger.debug(
            "tundra_trek step=%d screen=%s fuel=%s btn=%s moved=%s stuck=%d",
            _step, screen, fuel,
            (None if btn is None else (round(btn[0], 2), round(btn[1], 2), btn[2])),
            moved, stuck,
        )

        # Left the trek → done.
        if screen == "main_city":
            break

        # Battle / claim result → dismiss ("tap anywhere to exit").
        if screen == "arena.result" or await _result_text(ocr, frame):
            await _tap(actions, inst, _DISMISS_TAP, "arena.result.exit")
            dismisses += 1
            stuck = 0
            last_was_button = False
            await asyncio.sleep(1.8)
            continue

        # Primary action: the one saturated pill in the lower screen.
        # orange = Next Stop (drive, costs fuel); blue = Fight / Continue / Start.
        if btn is not None:
            # If the previous action was also a button tap and the screen hasn't
            # moved since, the drive isn't progressing (out of fuel, or the tap
            # isn't landing) — count it and bail before spinning the whole budget
            # on a dead button.
            if last_was_button and not moved:
                btn_stuck += 1
                if btn_stuck >= _MAX_BTN_STUCK:
                    logger.info(
                        "tundra_trek FSM: button not advancing after %d taps — stopping",
                        btn_stuck,
                    )
                    break
            else:
                btn_stuck = 0
            cx, cy, colour = btn
            await _tap(actions, inst, (cx, cy), f"tundra_trek.{colour}_button")
            advances += 1
            stuck = 0
            last_was_button = True
            # A blue press may launch a battle; give it longer to resolve before
            # the next probe (which then dismisses the result).
            await asyncio.sleep(6.5 if colour == "blue" else 5.0)
            continue

        # Hub / signpost screen (no bottom button): tap the signpost to set off.
        # Bounded — if nothing drives, the trek is out of fuel; stop.
        if screen == "event.tundra_trek":
            if signposts >= _MAX_SIGNPOST:
                break
            await _tap(actions, inst, _SIGNPOST_TAP, "tundra_trek.title")
            signposts += 1
            stuck = 0
            last_was_button = False
            await asyncio.sleep(5.0)
            continue

        # Milestone cutscene → Skip through it.
        if "skip" in await _ocr_lower(ocr, frame, _SKIP_WIN):
            await _tap(actions, inst, _SKIP_TAP, "tundra_trek.skip")
            stuck = 0
            last_was_button = False
            await asyncio.sleep(1.5)
            continue

        # Nothing actionable detected. Skip story text / clear tap-anywhere popups
        # by tapping the BOTTOM of the screen (safe — never lands on a character
        # or reward icon). Each tap CHANGES the screen, so we keep going. If a
        # bottom tap stops changing anything the screen is likely a chest
        # mini-game (its targets sit mid-screen) → escalate to a centre tap; give
        # up only when even that moves nothing.
        last_was_button = False
        if moved:
            stuck = 0
            await _tap(actions, inst, _TEXT_SKIP_TAP, "tundra_trek.text_skip")
        else:
            stuck += 1
            if stuck >= _MAX_STUCK:
                break
            await _tap(actions, inst, _CENTER_TAP, "tundra_trek.minigame")
        picks += 1
        await asyncio.sleep(1.5)

    # Escape to a recognised screen so the framework can navigate home. Never
    # tap-back while already on main_city (that opens the quit dialog) — the
    # loop checks first and stops there.
    for _ in range(_MAX_EXIT_BACKS):
        frame = await _capture(actions, inst)
        if frame is not None and await _screen(detector, frame) in _KNOWN_EXIT:
            break
        await _back(actions, inst)
        await asyncio.sleep(1.8)

    ctx.result.update(
        {
            "action": "drove_trek",
            "advances": advances,
            "dismisses": dismisses,
            "picks": picks,
            "signposts": signposts,
            "fuel_start": fuel_start,
            "fuel_last": fuel_last,
        }
    )
    logger.info(
        "tundra_trek FSM: advances=%d dismisses=%d picks=%d signposts=%d "
        "fuel=%s->%s stuck=%d instance=%s",
        advances, dismisses, picks, signposts, fuel_start, fuel_last, stuck, inst,
    )


DSL_EXEC_HANDLERS = {
    "drive_tundra_trek": _exec_drive_tundra_trek,
}
