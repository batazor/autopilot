"""Navigate to the State of Power tab of the Events panel.

The Events panel is a horizontal, swipe-only carousel of event tabs that opens on
whatever event is currently *primary* (Alliance Championship, etc.), so State of
Power is usually scrolled off to the left. The standard ``tab_identify`` resolver
can't reach it: the tab strip doesn't segment cleanly, active-tab detection
fails, and the per-event icons are all crown-ish (not discriminative). The only
reliable discriminator is the tab's **text label** "State of Power".

So this handler drives it imperatively, verified on bs1 (2026-06-21):

1. open the panel (``events.button`` on main_city),
2. swipe the carousel to its leftmost page (where Calendar + the State of Power
   tab group live) — detected by the strip image going still,
3. OCR the tab-label row; only act if it contains "state of power" (absent on the
   other carousel pages),
4. tap the State of Power tab (the 2nd slot on the aligned leftmost page) and
   verify the screen detector now reports ``event.state_of_power``.

Reached via ``- exec: goto_state_of_power`` from
``scenarios/event.state_of_power.yaml`` after the scenario navigates to
``node: main_city``. On failure it leaves the bot on a recognised screen so the
scenario's ``check_main_city`` can return home.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import cv2

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

# Frames come back at the emulator's mandatory 720x1280.
_W, _H = 720, 1280

# Tap targets (x, y) fractions.
_EVENTS_BUTTON = (0.932, 0.110)   # the fixed events.button on main_city (~671,141)
_SOP_TAB_TAP = (0.385, 0.095)     # State of Power = 2nd tab on the aligned leftmost page

# OCR windows (x, y, w, h) fractions.
_LABEL_BAND = (0.0, 0.105, 1.0, 0.055)   # the tab-label row under the icons
_STRIP_BAND = (0.0, 0.045, 1.0, 0.10)    # the tab strip (icons), for stillness check

_MAX_SWIPE = 6           # swipes to reach the leftmost page (carousel is small)
_SWIPE_Y = 0.09          # swipe along the tab row
_STRIP_MOVE_EPS = 4.0    # mean abs gray diff above which a swipe "moved" the strip


def _crop(frame: Any, win: tuple[float, float, float, float]) -> Any:
    x, y, w, h = win
    fh, fw = frame.shape[:2]
    return frame[int(y * fh):int((y + h) * fh), int(x * fw):int((x + w) * fw)]


def _strip_sig(frame: Any) -> Any:
    patch = _crop(frame, _STRIP_BAND)
    if patch is None or patch.size == 0:
        return None
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3 else patch
    return cv2.resize(gray, (120, 16), interpolation=cv2.INTER_AREA)


def _moved(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return True
    return float(cv2.absdiff(a, b).mean()) > _STRIP_MOVE_EPS


async def _ocr_lower(ocr: Any, frame: Any, win: tuple[float, float, float, float]) -> str:
    from layout.types import Region

    x, y, w, h = win
    fh, fw = frame.shape[:2]
    region = Region(int(x * fw), int(y * fh), int(w * fw), int(h * fh))
    try:
        result = await ocr.ocr_region(frame, region, region_id="sop_label")
    except Exception:
        return ""
    return str(getattr(result, "text", "") or "").strip().lower()


async def _tap(actions: Any, instance_id: str, frac: tuple[float, float], label: str) -> None:
    from layout.types import Point

    x, y = frac
    pt = Point(int(x * _W), int(y * _H))
    try:
        await asyncio.to_thread(actions.tap, instance_id, pt, approval_region=label)
    except Exception:
        logger.debug("goto_sop: tap %s failed", label, exc_info=True)


async def _swipe(actions: Any, instance_id: str, *, forward: bool) -> None:
    """forward=True drags right->left (reveal right tabs); forward=False drags
    left->right (reveal left / toward the start)."""
    from layout.types import Point

    y = int(_SWIPE_Y * _H)
    lo, hi = int(0.20 * _W), int(0.80 * _W)
    start, end = (Point(hi, y), Point(lo, y)) if forward else (Point(lo, y), Point(hi, y))
    try:
        await asyncio.to_thread(actions.swipe, instance_id, start, end)
    except Exception:
        logger.debug("goto_sop: swipe failed", exc_info=True)


async def _capture(actions: Any, instance_id: str) -> Any:
    try:
        return await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
    except Exception:
        return None


async def _screen(detector: Any, frame: Any, *, expected: str | None = None) -> str:
    try:
        return str(await detector.detect_screen(frame, expected=expected))
    except Exception:
        return "unknown"


async def _exec_goto_state_of_power(ctx: DslExecContext) -> None:
    """Open the Events panel and select the State of Power tab (see module docs)."""
    from navigation.detector import ScreenDetector
    from tasks import dsl_runtime

    actions = dsl_runtime.bot_actions()
    ocr = dsl_runtime.ocr_client()
    detector = ScreenDetector(ocr)
    inst = ctx.instance_id

    # 1) open the events panel
    await _tap(actions, inst, _EVENTS_BUTTON, "events.button")
    await asyncio.sleep(1.5)

    # 2) swipe the carousel to its (still) leftmost page
    prev_sig: Any = None
    for _ in range(_MAX_SWIPE):
        frame = await _capture(actions, inst)
        if frame is None:
            await asyncio.sleep(0.8)
            continue
        if await _screen(detector, frame, expected="event.state_of_power") == "event.state_of_power":
            ctx.result.update({"action": "already_on_sop"})
            return
        sig = _strip_sig(frame)
        if prev_sig is not None and not _moved(prev_sig, sig):
            break  # strip went still → snapped at the leftmost page
        prev_sig = sig
        await _swipe(actions, inst, forward=False)
        await asyncio.sleep(0.8)

    # 3) confirm State of Power is on this page by its label, then tap its tab
    frame = await _capture(actions, inst)
    label = await _ocr_lower(ocr, frame, _LABEL_BAND) if frame is not None else ""
    if "state of power" not in label:
        logger.info("goto_sop: 'State of Power' not on leftmost page (label=%r) inst=%s", label[:80], inst)
        ctx.result.update({"action": "sop_not_found", "label": label[:80]})
        return

    await _tap(actions, inst, _SOP_TAB_TAP, "state_of_power.tab")
    await asyncio.sleep(1.5)
    frame = await _capture(actions, inst)
    after = await _screen(detector, frame, expected="event.state_of_power") if frame is not None else "unknown"
    verified = after == "event.state_of_power"
    logger.info("goto_sop: tapped SoP tab verified=%s inst=%s", verified, inst)
    ctx.result.update({"action": "tapped_sop", "verified": verified})


DSL_EXEC_HANDLERS = {
    "goto_state_of_power": _exec_goto_state_of_power,
}
