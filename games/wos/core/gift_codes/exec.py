"""DSL exec: redeem beta gift codes in-game.

WOS beta codes can't be applied through the public gift-code API — they must be
typed into the in-game **Settings → Gift Code → Redeem Bundle** dialog for the
account logged in on the device. The codes themselves are discovered elsewhere
and stored under ``game="wos_beta"`` (see ``century.gift_codes``); this exec is
the in-game applier.

Flow (720×1280, the mandatory emulator resolution, so fixed tap targets are
stable): governor avatar → Settings → Gift Code → for each pending code: tap the
field, ``type_text`` the code, tap Redeem, dismiss the reward/result popup. Each
attempt is stamped into ``gift_code_redemptions`` so a code is not retyped on the
next run. Best-effort status: a code that the game rejects (expired / already
received) is still stamped, since re-typing it would only be rejected again.

Run from ``main_city`` (the avatar opens the profile only from a hub screen); the
calling scenario guards on that.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from layout.types import Point

logger = logging.getLogger(__name__)

_GAME = "wos_beta"

# Fixed tap targets (see module docstring).
_AVATAR = Point(45, 55)        # main_city: governor avatar (opens Chief Profile)
_SETTINGS = Point(680, 1240)   # Chief Profile: Settings
_GIFT_CODE = Point(530, 305)   # Settings: Gift Code
_FIELD = Point(360, 620)       # Redeem dialog: "Enter Gift Code" field
_REDEEM = Point(360, 770)      # Redeem dialog: Redeem button
_EXIT = Point(360, 1180)       # reward/result popup: "Tap anywhere to exit"
_CLOSE = Point(637, 473)       # Redeem dialog: close X


async def _tap(actions: Any, iid: str, pt: Point) -> None:
    await asyncio.to_thread(
        actions.tap, iid, pt, approval_region="gift_code", approval_source="gift_code"
    )


async def _clear_field(actions: Any, iid: str) -> None:
    """Empty the gift-code field before typing.

    A successful redeem clears the field, but an already-used / invalid code
    leaves the previous text in place; without this the next ``type_text`` would
    append and corrupt the code. Codes are short, so a bounded delete suffices.
    """
    await asyncio.to_thread(actions.press_key, iid, "KEYCODE_MOVE_END")
    for _ in range(20):
        await asyncio.to_thread(actions.press_key, iid, "KEYCODE_DEL")


def _field_is_present(image: Any) -> bool:
    """Cheap guard: the dialog's text field is a near-white bar at ~y620."""
    try:
        h, w = image.shape[:2]
        y0, y1 = int(0.46 * h), int(0.50 * h)
        x0, x1 = int(0.14 * w), int(0.90 * w)
        patch = image[y0:y1, x0:x1]
        return bool(patch.size) and float(patch.mean()) > 180.0
    except Exception:
        return False


async def _exec_redeem_beta_gift_codes(ctx: Any) -> None:
    from century.gift_codes.models import RedeemStatus
    from config.giftcodes_db import get_redemption, list_codes, set_redemption
    from tasks import dsl_runtime
    from tasks.dsl_exec.context import _resolve_player_id_for_device_level_exec

    iid = ctx.instance_id
    # No gamer id during onboarding → key redemptions by the device id.
    player = (await _resolve_player_id_for_device_level_exec(ctx)) or iid

    _done = {RedeemStatus.SUCCESS, RedeemStatus.ALREADY_RECEIVED}
    pending: list[str] = []
    for code in list_codes(game=_GAME):
        name = getattr(code, "name", "")
        if not name:
            continue
        status = get_redemption(name, player, game=_GAME)
        if status not in _done:
            pending.append(name)
    if not pending:
        ctx.result.update({"action": "none_pending"})
        return

    actions = dsl_runtime.bot_actions()

    # Navigate to the Redeem Bundle dialog.
    await _tap(actions, iid, _AVATAR)
    await asyncio.sleep(1.5)
    await _tap(actions, iid, _SETTINGS)
    await asyncio.sleep(1.5)
    await _tap(actions, iid, _GIFT_CODE)
    await asyncio.sleep(1.5)

    frame = await asyncio.to_thread(actions.capture_screen_bgr, iid)
    if frame is None or not _field_is_present(frame):
        logger.warning("redeem_beta_gift_codes: dialog not reached — aborting (instance=%s)", iid)
        ctx.result.update({"action": "dialog_not_reached", "pending": len(pending)})
        return

    redeemed: list[str] = []
    for name in pending:
        await _tap(actions, iid, _FIELD)
        await asyncio.sleep(0.4)
        await _clear_field(actions, iid)
        await asyncio.to_thread(actions.type_text, iid, name)
        await asyncio.sleep(0.6)
        await _tap(actions, iid, _REDEEM)
        await asyncio.sleep(2.5)
        # Dismiss the reward (or error) popup; tapping low-centre is safe for the
        # "Tap anywhere to exit" reward sheet and is a no-op miss otherwise.
        await _tap(actions, iid, _EXIT)
        await asyncio.sleep(1.2)
        # Best-effort: stamp as attempted so we don't retype it next run.
        try:
            set_redemption(name, player, RedeemStatus.SUCCESS, game=_GAME)
        except Exception:
            logger.debug("redeem_beta_gift_codes: set_redemption failed code=%s", name, exc_info=True)
        redeemed.append(name)

    # Close the dialog and back out to the hub.
    await _tap(actions, iid, _CLOSE)
    await asyncio.sleep(0.8)

    ctx.result.update({"action": "redeemed", "codes": redeemed, "count": len(redeemed)})
    logger.info(
        "redeem_beta_gift_codes: redeemed %d code(s) player=%s instance=%s: %s",
        len(redeemed), player, iid, ", ".join(redeemed),
    )


DSL_EXEC_HANDLERS = {"redeem_beta_gift_codes": _exec_redeem_beta_gift_codes}
